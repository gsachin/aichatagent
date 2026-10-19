"""Language codes, normalization and the foreign-language feature gate.

Why this module exists
----------------------
The same code arrives in several shapes: Twilio pins ``es-MX``, WhatsApp may
send ``pt_BR``, Whisper reports ``cmn``, and the Streamlit selector uses plain
``zh``. Downstream, Kokoro voices and phoneme sets are keyed by the base code,
so an unnormalized ``es-MX`` silently falls back to the English voice. One
normalization rule, applied at every boundary, is what keeps that from
happening.

The six non-Indian languages are also gated by one setting::

    FOREIGN_LANGUAGES=es,fr,it,ja,pt,zh    # empty disables the feature

Every selector, command, asset choice and STT route consults
:func:`configured_foreign_languages`. Setting it to an empty string restores the
pre-foreign behaviour without a code change.

This module deliberately does not read settings at import time. The parsers are
pure and the settings-backed helpers read lazily, so tests can override the
setting without reloading the process.
"""

from __future__ import annotations

import re
from functools import lru_cache

#: The six languages this feature adds. Order is the IVR order: Spanish,
#: French, Italian, Japanese, Portuguese, Chinese.
DEFAULT_FOREIGN_LANGUAGES = ("es", "fr", "it", "ja", "pt", "zh")

CJK_LANGUAGES = frozenset({"ja", "zh"})
LATIN_FOREIGN_LANGUAGES = frozenset({"es", "fr", "it", "pt"})

#: Hiragana, katakana, CJK unified ideographs and compatibility ideographs.
_CJK_RE = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿豈-﫿]"
)

#: Region/script aliases that mean Chinese for our purposes. ``cmn`` is the
#: code Whisper reports for Mandarin and what espeak uses for the phoneme set.
_BASE_ALIASES = {
    "cmn": "zh",
    "yue": "zh",
}


def normalize_language_code(raw: str | None) -> str:
    """Return the base code used by every language map.

    ``es-MX``, ``es_MX`` and ``es`` all become ``es``; ``pt-BR`` becomes
    ``pt``; ``cmn`` becomes ``zh``. Unknown codes are returned lowercased so
    the existing pass-through behaviour (an unknown code reaches the model as
    ``Answer in <code>``) is preserved.
    """
    code = (raw or "").strip().lower().replace("_", "-")
    if not code:
        return ""
    base = code.split("-", 1)[0]
    return _BASE_ALIASES.get(base, base)


@lru_cache(maxsize=8)
def _parse_foreign(raw: str) -> frozenset[str]:
    return frozenset(
        normalize_language_code(part)
        for part in (raw or "").split(",")
        if part.strip()
    )


def configured_foreign_languages() -> frozenset[str]:
    """The enabled foreign languages, from ``FOREIGN_LANGUAGES``.

    An empty value disables the feature. The cache is keyed by the raw setting,
    so an override in a test is visible immediately.
    """
    from app.config import settings

    return _parse_foreign((settings.FOREIGN_LANGUAGES or "").strip())


def is_foreign_code(code: str | None) -> bool:
    return normalize_language_code(code) in configured_foreign_languages()


def resolve_answer_language(
    session_language: str | None,
    form_language: str | None = None,
    default: str = "en",
) -> str:
    """Pick the answer language for a chat turn.

    The persisted conversation language wins over a per-message form field, so
    an in-chat command is not undone by a channel that also sends a language.
    """
    return (
        normalize_language_code(session_language)
        or normalize_language_code(form_language)
        or normalize_language_code(default)
    )


def contains_cjk(text: str | None) -> bool:
    return bool(_CJK_RE.search(text or ""))


def min_transcript_chars(language: str | None, default: int = 3) -> int:
    """Shortest meaningful transcript for a language.

    Japanese and Chinese answer questions with one or two characters ("是"),
    and Spanish/Italian with "sí"/"sì"; the English three-character noise gate
    would discard them. Every other language keeps the caller's default.
    """
    code = normalize_language_code(language)
    if code in CJK_LANGUAGES:
        return 1
    if code in LATIN_FOREIGN_LANGUAGES:
        return 2
    return default


def min_clause_chars(language: str | None, default: int = 12) -> int:
    """Shortest clause the streaming cutter may emit.

    A complete CJK answer can be three characters ("はい。"); the 12-character
    Latin/Indic minimum would make ``flush()`` return nothing and the caller
    would hear silence. Non-CJK languages keep the existing minimum.
    """
    return 1 if normalize_language_code(language) in CJK_LANGUAGES else default


# ── WhatsApp / chat language commands ───────────────────────────────────────

#: Whole-message commands. Bare two-letter codes are accepted for the six
#: because they are unambiguous; bare ``it`` is deliberately absent (it is an
#: English pronoun), so Italian requires ``/it``, ``Italian`` or ``italiano``.
_COMMAND_ALIASES = {
    "es": {"es", "spanish", "español", "espanol"},
    "fr": {"fr", "french", "français", "francais"},
    "it": {"italian", "italiano"},
    "ja": {"ja", "japanese", "日本語", "にほんご"},
    "pt": {"pt", "portuguese", "português", "portugues"},
    "zh": {"zh", "chinese", "中文", "汉语", "漢語"},
}

_EN_ALIASES = {"en", "english"}
_SLASH_CODES = {"es", "fr", "it", "ja", "pt", "zh", "en"}


def language_from_command(text: str, current: str = "") -> str | None:
    """Return the language a whole-message command selects, else ``None``.

    The match is whole-message on purpose. A student whose first message is
    "Spanish" is far more likely to be choosing a language than stating a name,
    but ``Italian`` mid-sentence must never hijack a question. "English" is
    only a command when the thread is already foreign — otherwise it remains a
    possible answer to the name question.

    Slash forms (``/es``) are always commands. Bare ``hi``/``gu`` are not
    treated as commands here; their selection stays on the existing paths.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    slash = raw.startswith("/")
    token = raw[1:] if slash else raw
    token = token.strip().strip(".,!?;:。！？、，").lower()
    if not token:
        return None

    if slash:
        if token in _SLASH_CODES:
            return token
        for code, aliases in _COMMAND_ALIASES.items():
            if token in aliases:
                return code
        if token in _EN_ALIASES:
            return "en"
        return None

    # Bare commands: one token only, so "I speak Spanish" is not a command.
    if " " in token or "\t" in token:
        return None
    for code, aliases in _COMMAND_ALIASES.items():
        if token in aliases:
            return code
    if token in _EN_ALIASES and normalize_language_code(current) in DEFAULT_FOREIGN_LANGUAGES:
        return "en"
    return None
