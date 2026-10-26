"""Deterministic reference formatting for chat, WhatsApp and voice delivery.

References are built from the evidence citations, never from model output.
The snippet is an excerpt of the evidence span and the URL is the exact
evidence URL. No channel-specific rendering calls a model.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

WHATSAPP_REFERENCE_MARKER = "\n\nSources:\n"
WHATSAPP_MESSAGE_LIMIT = 1600
DEFAULT_SNIPPET_CHARS = 240
DEFAULT_REFERENCE_LIMIT = 3


@dataclass(frozen=True)
class Reference:
    node_id: str
    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
        }


def _field(citation, name: str, default: str = "") -> str:
    if isinstance(citation, dict):
        value = citation.get(name, default)
    else:
        value = getattr(citation, name, default)
    return str(value or default)


def trim(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def normalize_phone(value: str) -> str:
    """Strip a Twilio ``whatsapp:`` prefix and keep E.164 punctuation."""
    text = str(value or "").strip()
    text = re.sub(r"^whatsapp:\s*", "", text, flags=re.IGNORECASE)
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    if text.startswith("+"):
        return "+" + digits
    if len(digits) == 10:
        return digits
    if len(digits) > 10:
        return "+" + digits
    return digits


def phone_match_key(value: str) -> str:
    """The last ten digits, used for a country-code-tolerant lookup."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:]


def phone_last4(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-4:]


def phone_hash(value: str, pepper: str = "") -> str:
    normalized = normalize_phone(value) or str(value or "").strip()
    return hashlib.sha256(f"{pepper}:{normalized}".encode("utf-8")).hexdigest()


def whatsapp_address(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.lower().startswith("whatsapp:") else f"whatsapp:{text}"


def references_from_citations(
    citations,
    *,
    limit: int = DEFAULT_REFERENCE_LIMIT,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> tuple[Reference, ...]:
    """Deduplicate citations and keep only exact, clickable references."""
    out: list[Reference] = []
    seen: set[str] = set()
    for citation in citations or ():
        url = _field(citation, "url").strip()
        if not url:
            continue
        node_id = _field(citation, "node_id").strip()
        key = node_id or url
        if key in seen:
            continue
        seen.add(key)
        title = _field(citation, "title").strip() or url
        snippet = trim(_field(citation, "snippet"), max(0, int(snippet_chars)))
        out.append(
            Reference(
                node_id=node_id or url,
                title=title[:240],
                url=url,
                snippet=snippet,
            )
        )
        if len(out) >= max(0, int(limit)):
            break
    return tuple(out)


def render_chat_references(references) -> str:
    if not references:
        return ""
    lines = ["**Sources**"]
    for reference in references:
        line = f"- [{reference.title}]({reference.url})"
        if reference.snippet:
            line += f' — "{reference.snippet}"'
        lines.append(line)
    return "\n".join(lines)


def render_whatsapp_references(references) -> str:
    if not references:
        return ""
    lines = ["Sources:"]
    for reference in references:
        lines.append(reference.title)
        lines.append(reference.url)
        if reference.snippet:
            lines.append(f'"{reference.snippet}"')
        lines.append("")
    return "\n".join(lines).rstrip()


def render_references(citations, *, channel: str = "chat", **kwargs) -> str:
    references = references_from_citations(citations, **kwargs)
    if not references:
        return ""
    if channel == "whatsapp":
        return render_whatsapp_references(references)
    if channel == "voice":
        return ""
    return render_chat_references(references)


def split_whatsapp_answer(
    text: str,
    *,
    limit: int = WHATSAPP_MESSAGE_LIMIT,
) -> tuple[str, str]:
    """Split an over-limit answer from its trailing reference block.

    Returns ``(body, references)``. When the answer fits, ``references`` is
    empty and the text is unchanged. URLs in the reference block are never
    truncated; the body is the only part that may be shortened.
    """
    value = text or ""
    if len(value) <= limit:
        return value, ""
    marker = value.find(WHATSAPP_REFERENCE_MARKER)
    if marker < 0:
        return trim(value, limit), ""
    body = value[:marker].rstrip()
    references = value[marker + len(WHATSAPP_REFERENCE_MARKER):].strip()
    if len(body) > limit:
        body = trim(body, limit)
    if len(references) > limit:
        references = references[: max(0, limit - 1)].rstrip() + "…"
    return body, references


def answer_summary(text: str, limit: int = 300) -> str:
    return trim(text, max(0, int(limit)))


def build_voice_reference_message(
    question: str,
    summary: str,
    references,
    *,
    limit: int = WHATSAPP_MESSAGE_LIMIT,
) -> str:
    """The WhatsApp message sent after a cited call turn."""
    references = tuple(references or ())
    if not references:
        return ""
    context = trim(f'"{question}" — "{summary}"', 300)
    header = "DSPMU reference from your call\n\nCall context: " + context
    footer = "Reply STOP to opt out."
    blocks: list[str] = []
    for reference in references:
        blocks.append(
            f"Source: {reference.title}\n{reference.url}\n"
            + (f'"{reference.snippet}"' if reference.snippet else "")
        )
    body = header
    for block in blocks:
        candidate = f"{body}\n\n{block}"
        if len(candidate) + len(footer) + 2 > limit:
            break
        body = candidate
    if body == header:
        return ""
    return f"{body}\n\n{footer}"
