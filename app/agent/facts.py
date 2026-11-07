"""Read-only access to the signed-off fact layer.

The facts file is the authority for fees, dates, contacts and named university
leaders. The graph and live pages supplement it; they do not override it.
Fee records are currently marked ``draft`` with ``approved_by: null``; a
leadership record carries its own verification status and approver.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from app.programs import DSPMU_PROGRAM_ALIASES, detect_program

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FACTS_PATH = "content/dspmu/facts.json"

_STOPWORDS = frozenset(
    "bachelor of arts science commerce administration in the and for".split()
)


def _tokens(text: str) -> set[str]:
    return {
        re.sub(r"s$", "", token) if len(token) > 3 else token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if token not in _STOPWORDS
    }


def _contains_phrase(text: str, phrase: str) -> bool:
    """Match a leader alias without letting ``vc`` match inside another word."""
    phrase = (phrase or "").strip().lower()
    if not phrase:
        return False
    if re.fullmatch(r"[a-z0-9]+", phrase):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])",
                text,
            )
        )
    return phrase in text


class FactStore:
    """Load ``facts.json`` once per process and match fees/contacts/leaders."""

    def __init__(self, path: str | Path = DEFAULT_FACTS_PATH) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            self.path = PROJECT_ROOT / self.path
        self._lock = threading.Lock()
        self._cache: tuple[tuple[int, int], dict] | None = None

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def load(self) -> dict:
        if not self.available:
            return {}
        try:
            stat = self.path.stat()
        except OSError:
            return {}
        identity = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if self._cache is not None and self._cache[0] == identity:
                return self._cache[1]
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        data = data if isinstance(data, dict) else {}
        with self._lock:
            self._cache = (identity, data)
        return data

    def fees_for_query(self, query: str) -> list[dict]:
        data = self.load()
        fees = data.get("fees") or []
        programme = detect_program((query or "").lower(), DSPMU_PROGRAM_ALIASES)
        tokens = _tokens(query)
        matches: list[dict] = []
        for fact in fees:
            if not isinstance(fact, dict):
                continue
            subject = _tokens(str(fact.get("subject") or ""))
            degree = _tokens(str(fact.get("degree") or ""))
            programme_tokens = _tokens(programme) if programme else set()
            if programme_tokens and (
                (subject and subject <= programme_tokens)
                or (degree and degree <= programme_tokens)
            ):
                matches.append(fact)
            elif not programme and subject and subject <= tokens:
                matches.append(fact)
        return matches[:3]

    def contacts(self) -> dict:
        university = self.load().get("university") or {}
        return {
            "name": str(university.get("name") or ""),
            "phone": str(university.get("phone") or ""),
            "emails": list(university.get("emails") or []),
            "address": str(university.get("address") or ""),
            "website": str(university.get("website") or ""),
        }

    def leadership_for_query(self, query: str) -> list[dict]:
        """Signed-off people facts matching a role/alias in the query.

        The current VC is not returned for an explicitly "former" question;
        that question belongs to the official former-VC page, not the current
        fact.
        """
        leadership = self.load().get("leadership") or {}
        if not isinstance(leadership, dict):
            return []
        text = (query or "").strip().lower()
        if not text:
            return []
        former = bool(
            re.search(r"(?<![a-z])(?:former|late|ex)(?![a-z])", text)
        )
        matches: list[dict] = []
        for fact in leadership.values():
            if not isinstance(fact, dict):
                continue
            role = str(fact.get("role") or "").strip().lower()
            aliases = [
                str(alias).strip().lower()
                for alias in (fact.get("aliases") or [])
            ]
            terms = [role, *aliases]
            if not any(_contains_phrase(text, term) for term in terms):
                continue
            if former and "former" not in role:
                continue
            matches.append(fact)
        return matches[:2]

    @staticmethod
    def leadership_text(fact: dict) -> str:
        role = str(fact.get("role") or "University leader")
        name = str(fact.get("name") or "not published")
        return (
            f"{role}: {name}. Verified "
            f"{fact.get('verified_at') or 'not verified'}; status "
            f"{fact.get('status') or 'draft'}."
        )

    @staticmethod
    def leadership_snippet(fact: dict) -> str:
        return (
            f"{fact.get('role') or 'University leader'}: "
            f"{fact.get('name') or 'not published'} "
            f"(status: {fact.get('status') or 'draft'})."
        )

    def valid_until(self) -> str:
        return str(self.load().get("valid_until") or "")

    def generated_at(self) -> str:
        return str(self.load().get("generated_at") or "")

    @staticmethod
    def fee_text(fact: dict) -> str:
        programme = str(fact.get("programme") or "Programme")
        return (
            f"{programme}: annual fee ₹{fact.get('annual_fee_gen', 0):,} (GEN); "
            f"₹{fact.get('annual_fee_reserved', 0):,} (ST/SC/BC I & II); "
            f"semester course fee ₹{fact.get('semester_course_fee_gen', 0):,} "
            f"(GEN); valid {fact.get('valid_from') or '?'} to "
            f"{fact.get('valid_until') or '?'}; status "
            f"{fact.get('status') or 'draft'}."
        )

    @staticmethod
    def fee_snippet(fact: dict) -> str:
        return (
            f"{fact.get('programme') or 'Programme'}: annual ₹"
            f"{fact.get('annual_fee_gen', 0):,} (GEN), ₹"
            f"{fact.get('annual_fee_reserved', 0):,} "
            f"(ST/SC/BC I & II); status {fact.get('status') or 'draft'}."
        )

    @staticmethod
    def contact_text(contacts: dict) -> str:
        emails = ", ".join(contacts.get("emails") or [])
        return (
            f"{contacts.get('name') or 'University'}: "
            f"{contacts.get('phone') or 'phone not published'}; "
            f"{emails or 'email not published'}; "
            f"{contacts.get('address') or 'address not published'}; "
            f"{contacts.get('website') or ''}"
        ).strip()

    def admissions(self) -> dict:
        admissions = self.load().get("admissions") or {}
        return admissions if isinstance(admissions, dict) else {}

    @staticmethod
    def admission_text(admissions: dict) -> str:
        portal = str(admissions.get("portal") or "").strip()
        note = str(admissions.get("note") or "").strip()
        parts = []
        if portal:
            parts.append(f"Admissions portal: {portal}.")
        if note:
            parts.append(note)
        return " ".join(parts) or "Admissions information is published online."
