"""Deterministic conversational replies for non-knowledge turns.

This is the Day-0 guard, not the full dialogue-act model. It keeps greetings,
thanks, capability questions and meta requests out of retrieval/deep search.
The WP3 query frame replaces it with a typed dialogue act and multilingual
replies.
"""

from __future__ import annotations

import re

_GREETING_RE = re.compile(
    r"^\s*(?:hi|hello|hey|namaste|namaskar|good\s+(?:morning|afternoon|evening)|"
    r"greetings|नमस्ते|नमस्कार|નમસ્તે|કેમ\s+છો)\b",
    re.IGNORECASE,
)
_THANKS_RE = re.compile(
    r"\b(?:thanks|thank\s+you|dhanyavad|धन्यवाद|આભાર)\b",
    re.IGNORECASE,
)
_BYE_RE = re.compile(
    r"^\s*(?:bye|goodbye|see\s+you|exit|close|धन्यवाद|આવજો)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_BACKCHANNEL_RE = re.compile(
    r"^\s*(?:ok|okay|hmm+|mm-?h+m?|right|got\s+it|i\s+see)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_META_EXACT_RE = re.compile(
    r"^\s*(?:"
    r"who\s+are\s+you|what\s+are\s+you|what\s+can\s+you\s+do|"
    r"what\s+do\s+you\s+do|how\s+do\s+you\s+work|what\s+can\s+i\s+ask|"
    r"what\s+is\s+this|"
    r"are\s+you\s+(?:a\s+)?(?:bot|robot|human|ai)"
    r")\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_META_SEARCH_RE = re.compile(
    r"^\s*(?:can\s+you\s+)?(?:search|check)\s+(?:deep|deep\s+search)"
    r"\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_META_INTENT_RE = re.compile(
    r"\bdo\s+you\s+understand\b.*\b(?:intent|mean|program|programme)\b",
    re.IGNORECASE,
)


def conversational_reply(query: str) -> str | None:
    """Return a deterministic reply for a conversational turn, or ``None``.

    A knowledge question must return ``None`` even if it starts with a
    greeting ("hello, what is the fee?"). Only a query whose whole text is a
    conversational turn is handled here.
    """
    text = re.sub(r"\s+", " ", (query or "").strip())
    if not text:
        return None

    # "hello, what is the fee?" is a knowledge question, not a greeting.
    if _GREETING_RE.match(text) and len(text.split()) <= 4:
        return (
            "Hello! I’m the DSPMU admissions assistant. Ask me about "
            "programmes, admissions, fees, dates, notices, hostel, library or "
            "contacts."
        )
    if _BYE_RE.match(text):
        return "Goodbye! If you need admissions help later, I’m here."
    if _THANKS_RE.search(text) and len(text.split()) <= 6:
        return "You’re welcome. What else would you like to know about DSPMU?"
    if _BACKCHANNEL_RE.match(text):
        return "Okay. What would you like to know about DSPMU?"
    if _META_SEARCH_RE.match(text):
        return (
            "Yes. If the indexed pages don’t contain the answer, I can "
            "search the official DSPMU website and cite what I find. "
            "What should I look up?"
        )
    if _META_INTENT_RE.search(text):
        return (
            "Yes — “program” means an academic programme at DSPMU. If you "
            "mean a specific level (UG, PG or PhD), tell me and I’ll use "
            "that."
        )
    if _META_EXACT_RE.match(text):
        return (
            "I answer questions about DSPMU from its public website. I can "
            "look up programmes, admissions, fees, dates, notices, "
            "departments and contacts. What would you like to know?"
        )
    return None


__all__ = ["conversational_reply"]
