"""
Channel identity → CRM payload, and the value mappings the CRM demands.

This module is pure: no I/O, no network, no database. Everything here is
translation between what this app knows and what the Salesforce user API
accepts, which makes it the cheapest place to put the defences the plan calls
for against a contract we cannot change (decision D1).

Three upstream behaviours are handled here, all reproduced live in Phase 0:

**R2 — never send a blank identifier.** ``find_user`` matches
``Email__c = X OR Phone__c = Y LIMIT 1`` with no ordering, so an empty string
becomes ``Email__c = ''`` and can match an unrelated record. A lookup carrying
no usable identifier is therefore *refused* here rather than sent.

**R3 — SOQL injection.** The API interpolates email and phone straight into a
SOQL string. A quote or backslash in the payload either breaks the query or
alters it, so those characters are removed before anything is sent — and the
removal is logged, because silently rewriting a student's name is worth knowing
about.

**R16 — restricted picklists.** ``Sentiment__c`` and
``Offer_Letter_Accepted__c`` accept a fixed vocabulary, and an out-of-vocabulary
value fails as a **500**, indistinguishable by status code from a transient
fault. The mappings below are the only sanctioned way to produce those values,
and they return ``None`` for anything unrecognised so the caller can decline to
send rather than fire a doomed request.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("crm.identity")

# ── The CRM's vocabularies, observed live 2026-09-11 ─────────────────────────
#
# Reproduced by scripts/verify_salesforce_api.py against the dev org. Changing
# these means re-running that script first.

CRM_SENTIMENT_VOCAB = frozenset({"NURTURE", "HOT", "WARM", "AT-RISK", "DISQUALIFIED"})
CRM_OFFER_ACCEPTED_VOCAB = frozenset({"UNKNOWN", "ACCEPTED", "NOT_ACCEPTED"})

# app/sentiment/categorizer.py:31-35 → Sentiment__c.  A clean .upper() for all
# five, including At-Risk, which already carries the hyphen the CRM uses. (The
# separate Lead_Category__c field spells it "AT RISK" — do not copy across.)
SENTIMENT_MAP: dict[str, str] = {
    "Hot": "HOT",
    "Warm": "WARM",
    "Nurture": "NURTURE",
    "At-Risk": "AT-RISK",
    "Disqualified": "DISQUALIFIED",
}

# app/main.py:1494,1500 emits these two words. Note "rejected" is NOT_ACCEPTED —
# not REJECTED, not DECLINED.
OFFER_ACCEPTED_MAP: dict[str, str] = {
    "accepted": "ACCEPTED",
    "rejected": "NOT_ACCEPTED",
}

_MAX_NAME = 255
_MAX_EMAIL = 255
_MAX_COURSE = 255
_MAX_PHONE_DIGITS = 15  # E.164 maximum

# Characters that break or alter the API's interpolated SOQL literal, plus
# control characters. Stripped, not escaped: the API has no escaping.
_SOQL_HOSTILE = re.compile(r"['\"\\;]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Sanitisation ─────────────────────────────────────────────────────────────

def sanitize(value: object, *, max_length: int = _MAX_NAME, field: str = "value") -> str:
    """
    Make a value safe to interpolate into the API's SOQL.

    Removes control characters and anything that could terminate or alter the
    query literal, collapses runs of whitespace, and truncates. Logs when it
    changed something material — a name that arrives with an apostrophe is
    normal, a name that arrives with a quote-and-backslash is not.
    """
    if value is None:
        return ""
    # Control characters become spaces rather than vanishing: a newline inside a
    # pasted address is a separator, so "Ana\nMaria" is two words, not one.
    text = _CONTROL.sub(" ", str(value)).strip()
    text = _WHITESPACE.sub(" ", text)

    cleaned = _SOQL_HOSTILE.sub("", text)
    if cleaned != text:
        logger.warning(
            f"crm.identity: stripped SOQL-hostile characters from {field} "
            f"({len(text) - len(cleaned)} removed)"
        )

    if len(cleaned) > max_length:
        logger.warning(
            f"crm.identity: truncated {field} from {len(cleaned)} to {max_length} chars"
        )
        cleaned = cleaned[:max_length]

    return cleaned


def normalize_phone(raw: object) -> str:
    """
    Reduce a stored phone number to bare E.164, or "" if it is not usable.

    Stored numbers are inconsistent: WhatsApp leads keep Twilio's ``whatsapp:``
    prefix (``app/offers/service.py`` only normalises at *send* time), voice
    leads are bare, and test data carries spaces. The CRM wants one shape.

    Returns "" for anything that is not recognisably E.164 — a value the caller
    must treat as "no phone", never as a phone to send (R2).
    """
    if raw is None:
        return ""

    text = _CONTROL.sub("", str(raw)).strip().replace(" ", "").replace("-", "")
    if text.lower().startswith("whatsapp:"):
        text = text[len("whatsapp:"):]
    if text.lower().startswith("tel:"):
        text = text[len("tel:"):]

    if not text.startswith("+"):
        # No country code. Guessing one would silently mis-route a student, so
        # decline instead.
        return ""

    digits = text[1:]
    if not digits.isdigit():
        return ""
    if not (8 <= len(digits) <= _MAX_PHONE_DIGITS):
        return ""

    return "+" + digits


def normalize_email(raw: object) -> str:
    """Lower-case and shape-check an email, or "" if it is not usable."""
    email = sanitize(raw, max_length=_MAX_EMAIL, field="email").lower()
    if not email or not _EMAIL_SHAPE.match(email):
        return ""
    return email


# ── Value mappings (R16) ─────────────────────────────────────────────────────

def map_sentiment(value: object) -> str | None:
    """
    Map an app sentiment/category value onto ``Sentiment__c``.

    Accepts the categorizer's output in any casing. Returns None for anything
    outside the vocabulary — including ``primary_emotion`` values such as
    "excited", which is exactly the input we must NOT send (R16).

    Guaranteed to return either a member of ``CRM_SENTIMENT_VOCAB`` or None,
    never a value that would fail.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    if text in SENTIMENT_MAP:
        return SENTIMENT_MAP[text]

    upper = text.upper().replace(" ", "-")  # tolerate Lead_Category__c's "AT RISK"
    if upper in CRM_SENTIMENT_VOCAB:
        return upper

    # Case-insensitive match against the categorizer's own names.
    for app_value, crm_value in SENTIMENT_MAP.items():
        if app_value.upper() == text.upper():
            return crm_value

    logger.warning(f"crm.identity: {text!r} is not a valid Sentiment__c value — not sending")
    return None


def map_offer_accepted(value: object) -> str | None:
    """
    Map an offer response onto ``Offer_Letter_Accepted__c``.

    The app says "accepted"/"rejected"; the CRM wants ACCEPTED/NOT_ACCEPTED.
    Already-canonical values pass through. Returns None otherwise.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    mapped = OFFER_ACCEPTED_MAP.get(text.lower())
    if mapped:
        return mapped

    upper = text.upper()
    if upper in CRM_OFFER_ACCEPTED_VOCAB:
        return upper

    logger.warning(f"crm.identity: {text!r} is not a valid Offer_Letter_Accepted__c value")
    return None


# ── The identity object ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChannelIdentity:
    """
    Everything one conversation knows about the person, already normalised.

    Build it with :func:`from_channel` rather than constructing directly, so the
    normalisation cannot be skipped.
    """

    channel: str
    conversation_id: str = ""
    phone_number: str = ""
    email: str = ""
    name: str = ""
    course: str = ""
    lead_id: str = ""

    @property
    def has_identifier(self) -> bool:
        """At least one usable way to find this person. Without one, R2 bites."""
        return bool(self.email or self.phone_number)

    @property
    def is_sendable(self) -> bool:
        """
        Safe to call lookup-or-create.

        Both conditions are load-bearing: no identifier means the lookup can
        match an unrelated record (R2), and no conversation id means the record
        would be created without the linkage that is the point of the call.
        """
        return self.has_identifier and bool(self.conversation_id)

    def lookup_payload(self) -> dict[str, object]:
        """
        Body for ``POST /users/lookup-or-create``.

        ``name``, ``email`` and ``phoneNumber`` are all required and typed as
        plain ``str`` upstream, so they are always present — as empty strings,
        never omitted. Callers must have checked :attr:`is_sendable` first.
        """
        payload: dict[str, object] = {
            "name": self.name,
            "email": self.email,
            "phoneNumber": self.phone_number,
            "conversationId": self.conversation_id,
        }
        if self.course:
            payload["course"] = self.course
        return payload

    def dedupe_key(self) -> str:
        """
        Single-flight key: two concurrent lookups for the same person must
        collapse into one request. Email wins when present because it is the
        more precise identifier.
        """
        return f"email:{self.email}" if self.email else f"phone:{self.phone_number}"

    def describe(self) -> str:
        """Log-safe summary. Never includes the raw email or phone."""
        parts = [self.channel]
        if self.email:
            parts.append("email")
        if self.phone_number:
            parts.append("phone")
        if self.course:
            parts.append("course")
        return f"{'+'.join(parts)} (conv={self.conversation_id or '—'})"


def from_channel(
    channel: str,
    *,
    conversation_id: str = "",
    phone_number: object = "",
    email: object = "",
    name: object = "",
    course: object = "",
    lead_id: str = "",
) -> ChannelIdentity:
    """
    Build a normalised identity from raw channel data.

    Every field is passed through its sanitiser, so callers can hand over
    whatever the channel gave them — including Twilio's ``whatsapp:+1...``
    prefixed number or a name with an apostrophe.
    """
    return ChannelIdentity(
        channel=channel,
        conversation_id=str(conversation_id or "").strip(),
        phone_number=normalize_phone(phone_number),
        email=normalize_email(email),
        name=sanitize(name, max_length=_MAX_NAME, field="name"),
        course=sanitize(course, max_length=_MAX_COURSE, field="course"),
        lead_id=str(lead_id or "").strip(),
    )
