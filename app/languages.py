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


# ── Supported languages (one answer for every selector) ─────────────────────

def supported_languages() -> frozenset[str]:
    """Every language this deployment will speak.

    ``SUPPORTED_LANGUAGES`` is the operative list (it already contains the
    foreign codes on the live box); the union with ``FOREIGN_LANGUAGES`` keeps
    a deployment that enabled the feature but forgot to repeat the codes in
    ``SUPPORTED_LANGUAGES`` working. Both reads are lazy, like every other
    settings-backed helper here.
    """
    from app.config import settings

    configured = frozenset(
        normalize_language_code(part)
        for part in (settings.SUPPORTED_LANGUAGES or "").split(",")
        if part.strip()
    )
    return configured | configured_foreign_languages()


def is_supported_language(code: str | None) -> bool:
    """Is this code one this deployment can answer in?

    The six foreign codes are gated by ``FOREIGN_LANGUAGES`` even when they are
    also named in ``SUPPORTED_LANGUAGES``: listing a code must not re-enable a
    feature the operator turned off.
    """
    normalized = normalize_language_code(code)
    if normalized in DEFAULT_FOREIGN_LANGUAGES:
        return normalized in configured_foreign_languages()
    return normalized in supported_languages()


# ── Spoken language requests (voice) ────────────────────────────────────────

#: Names a caller may use when asking, mid-call, for another language. A
#: request SHAPE is required ("speak in X", "X mein baat", "X में बात", "X
#: please"), so a caller discussing Hindi as a subject is not misread as asking
#: to switch to it.
_LANGUAGE_NAMES: dict[str, tuple[str, ...]] = {
    "en": ("english", "अंग्रेज़ी", "अंग्रेजी", "અંગ્રેજી"),
    "hi": ("hindi", "हिंदी", "हिन्दी", "હિન્દી"),
    "gu": ("gujarati", "गुजराती", "ગુજરાતી"),
    "es": ("spanish", "español", "espanol"),
    "fr": ("french", "français", "francais"),
    "it": ("italian", "italiano"),
    "ja": ("japanese", "日本語", "にほんご"),
    "pt": ("portuguese", "português", "portugues"),
    "zh": ("chinese", "mandarin", "中文", "汉语", "漢語"),
}


def requested_language(text: str) -> str | None:
    """The language a caller explicitly asked to speak, else ``None``.

    This is the voice equivalent of :func:`language_from_command`: it exists so
    an explicit request ("मुझे हिंदी में बात करनी है") outranks per-utterance
    language ID, which on 8 kHz telephony labels a single loanword
    ("admission") as a different Indian language often enough to have flipped a
    live call to Gujarati at 0.63 confidence.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    low = raw.lower()

    for code, names in _LANGUAGE_NAMES.items():
        alternation = "|".join(re.escape(name) for name in names)
        # "speak/talk/answer ... in <name>"
        if re.search(
            rf"\b(?:speak|talk|answer|reply|respond|continue)\b"
            rf"[^.!?।]{{0,24}}(?:{alternation})\b",
            low,
        ):
            return code
        # "<name> please"
        if re.search(rf"(?:{alternation})\s*(?:please|pls)\b", low):
            return code
        # Hinglish: "<name> mein baat/bol/jawab"
        if re.search(
            rf"\b(?:{alternation})\b\s*(?:mein|me)\b"
            rf"[^.!?।]{{0,24}}\b(?:baat|bol|jawab|answer)\b",
            low,
        ):
            return code
        # "<name> chahiye"
        if re.search(rf"\b(?:{alternation})\b\s*(?:chahiye|चाहिए|જોઈએ)", low):
            return code

    # Native-script request shapes. The language name and the request verb are
    # both required; "हिंदी फीस" (a Hindi fees question) is not a request.
    for code, names in _LANGUAGE_NAMES.items():
        for name in names:
            if name.isascii():
                continue
            escaped = re.escape(name)
            if re.search(
                rf"{escaped}\s*(?:में|मे|માં|મા)\s*"
                rf"(?:बात|बोल|जवाब|उत्तर|વાત|બોલ|જવાબ)",
                raw,
            ):
                return code
            if re.search(rf"{escaped}\s*(?:बोलो|बोलिए|बोलती|બોલો|બોલીયે)", raw):
                return code
    return None


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
    # Full/native names only: bare "hi"/"gu" stay greetings, not commands.
    "hi": {"hindi", "हिंदी", "हिन्दी", "હિન્દી"},
    "gu": {"gujarati", "गुजराती", "ગુજરાતી"},
}

_EN_ALIASES = {"en", "english"}
_SLASH_CODES = {"es", "fr", "it", "ja", "pt", "zh", "hi", "gu", "en"}

#: Labels for the WhatsApp first-contact language menu. The core three are
#: always offered; the six foreign labels are appended only when
#: ``FOREIGN_LANGUAGES`` enables them. The parenthesised English name is for
#: readers of the menu; ``_LANGUAGE_NAMES`` supplies the bare names the reply
#: parser accepts.
WHATSAPP_MENU_LABELS = {
    "en": "English",
    "hi": "हिंदी (Hindi)",
    "gu": "ગુજરાતી (Gujarati)",
    "es": "Español (Spanish)",
    "fr": "Français (French)",
    "it": "Italiano (Italian)",
    "ja": "日本語 (Japanese)",
    "pt": "Português (Portuguese)",
    "zh": "中文 (Chinese)",
}

WHATSAPP_MENU_CORE_LANGUAGES = ("en", "hi", "gu")


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
    if token in _EN_ALIASES and normalize_language_code(current) not in ("", "en"):
        return "en"
    return None


# ── Question shape (WhatsApp routing) ──────────────────────────────────────

#: Characters that mark a question regardless of language. The full-width
#: forms matter: a Chinese/Japanese question ("能列下具体的申请要求么？") carries
#: ``？``, not ``?``, and the ASCII-only check sent it through the admission
#: intent classifier — an extra model call on every such turn.
#: The Devanagari danda (``।``) is deliberately absent: it ends declarative
#: Hindi sentences too, so treating it as a question would divert
#: "मैं एडमिशन लेना चाहता हूँ।" away from the admission flow.
_QUESTION_MARKS = ("?", "？", "¿")

#: Interrogative words per language. Deliberately conservative: only words
#: that are unambiguously questions in their language are listed. Ambiguous
#: Latin words ("que", "como", "come") are omitted because they collide with
#: ordinary words and would turn an intent statement into a knowledge question.
#: Non-Latin scripts have no such collision and are matched as substrings.
_QUESTION_WORDS: dict[str, tuple[str, ...]] = {
    "en": ("what", "how", "which", "when", "where", "who", "why",
           "how much", "how many"),
    "hi": ("क्या", "कैसे", "कैसा", "कौन", "कब", "कहाँ", "कहां", "कितना", "कितनी"),
    "gu": ("શું", "કેવી", "કેવું", "કયો", "કઈ", "ક્યારે", "ક્યાં", "કેટલું", "કેટલી"),
    "es": ("qué", "cómo", "cuál", "cuáles", "cuándo", "dónde", "cuánto", "cuánta"),
    "fr": ("quel", "quelle", "quels", "quelles", "comment", "quand", "où",
           "combien", "pourquoi"),
    "it": ("quale", "quali", "quando", "dove", "quanto", "quanta", "perché"),
    "ja": ("何", "どう", "どれ", "いつ", "どこ", "なぜ", "いくら", "ですか", "ますか"),
    "pt": ("qual", "quais", "quando", "onde", "quanto", "quanta", "por que"),
    "zh": ("什么", "怎麼", "怎么", "怎样", "哪", "何时", "為什麼", "为什么",
           "多少", "是否", "吗", "呢", "请问", "請問"),
}


def looks_like_question(text: str | None) -> bool:
    """True when *text* asks something, in any of the supported languages.

    Used to route a WhatsApp message straight to the knowledge base instead of
    spending a model call on admission-intent classification. A question is a
    question: "what are the requirements to apply for MBA?" is not intent to
    proceed, and the classifier's own prompt says so.

    The check is intentionally cheap and conservative — a false positive costs
    an informational answer (the student can still say "I want to take
    admission", which hits the strong keyword path), while a false negative
    costs ~1.2-1.6 s on every turn.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if any(mark in raw for mark in _QUESTION_MARKS):
        return True

    low = raw.lower()
    for words in _QUESTION_WORDS.values():
        for word in words:
            if word.isascii():
                if re.search(rf"\b{re.escape(word)}\b", low):
                    return True
            elif word in low:
                return True
    return False


# ── Localized admission-intent phrases (WhatsApp state machine) ─────────────
#
# The deterministic admission flow used to match English keywords only, so a
# Hindi/Gujarati (or Spanish/French/...) request for admission fell through to
# the knowledge base: no document prompt, no offer letter. STRONG phrases are
# unambiguous and skip the model; MEDIUM phrases only nominate a candidate and
# main.py's existing classifier confirms them, so a question about the
# admission *process* stays a question.
ADMISSION_INTENT_STRONG: dict[str, tuple[str, ...]] = {
    "hi": (
        "एडमिशन लेना चाहता", "एडमिशन लेना चाहती", "एडमिशन लेना चाहते",
        "एडमिशन लेनी है", "एडमिशन लेना है",
        "प्रवेश लेना चाहता", "प्रवेश लेना चाहती", "प्रवेश लेना चाहते",
        "प्रवेश लेना है", "प्रवेश लेनी है",
        "दाखिला लेना चाहता", "दाखिला लेना चाहती", "दाखिला लेना है",
        "एडमिशन करना चाहता", "एडमिशन करना चाहती",
        "प्रवेश करना चाहता", "प्रवेश करना चाहती",
        "एडमिशन चाहिए", "प्रवेश चाहिए", "दाखिला चाहिए",
        "एडमिशन में रुचि", "प्रवेश में रुचि",
        "मुझे एडमिशन लेना", "मुझे प्रवेश लेना",
        "एडमिशन के लिए आवेदन करना चाहता",
        "प्रवेश के लिए आवेदन करना चाहता",
    ),
    "gu": (
        "એડમિશન લેવું છે", "એડમિશન લેવો છે", "એડમિશન લેવી છે",
        "એડમિશન લેવા માટે", "એડમિશન કરવું છે", "એડમિશન કરવો છે",
        "પ્રવેશ લેવો છે", "પ્રવેશ લેવું છે", "પ્રવેશ કરવો છે",
        "દાખલો લેવો છે", "દાખલો લેવું છે",
        "એડમિશન જોઈએ", "પ્રવેશ જોઈએ", "દાખલો જોઈએ",
        "એડમિશનમાં રસ", "પ્રવેશમાં રસ",
        "એડમિશન માટે અરજી કરવી છે",
    ),
    "es": (
        "quiero inscribirme", "quiero matricularme", "quiero postularme",
        "quiero postular", "quiero tomar la admisión", "quiero tomar admisión",
        "quiero ingresar", "deseo inscribirme", "deseo matricularme",
        "me gustaría inscribirme", "me gustaría matricularme",
    ),
    "fr": (
        "je veux m'inscrire", "je souhaite m'inscrire",
        "je voudrais m'inscrire", "je veux postuler", "je souhaite postuler",
        "je voudrais postuler", "je veux être admis", "je souhaite être admis",
        "je veux être admise", "je souhaite être admise",
    ),
    "it": (
        "voglio iscrivermi", "vorrei iscrivermi", "desidero iscrivermi",
        "voglio immatricolarmi", "vorrei immatricolarmi", "voglio candidarmi",
    ),
    "ja": (
        "入学したい", "入学を希望", "出願したい", "申し込みたい",
        "申し込む", "応募したい",
    ),
    "pt": (
        "quero me inscrever", "quero inscrever-me", "quero matricular-me",
        "quero me matricular", "desejo me inscrever",
        "gostaria de me inscrever", "quero me candidatar",
        "quero candidatar-me",
    ),
    "zh": (
        "我想报名", "我要报名", "我想申请", "我要申请",
        "我想入学", "我要入学",
    ),
}

ADMISSION_INTENT_MEDIUM: dict[str, tuple[str, ...]] = {
    "hi": ("एडमिशन", "प्रवेश", "दाखिला", "आवेदन"),
    "gu": ("એડમિશન", "પ્રવેશ", "દાખલો", "અરજી"),
    "es": (
        "admisión", "admision", "inscripción", "inscripcion", "inscribirme",
        "matrícula", "matricula", "matricularme", "postular", "aplicar",
        "solicitud",
    ),
    "fr": (
        "admission", "inscription", "inscrire", "candidature", "postuler",
        "s'inscrire",
    ),
    "it": (
        "ammissione", "iscrizione", "iscrivermi", "immatricolazione",
        "immatricolarmi", "candidarmi",
    ),
    "ja": ("入学", "出願", "申し込み", "申し込む", "応募"),
    "pt": (
        "admissão", "admissao", "inscrição", "inscricao", "inscrever",
        "matrícula", "matricula", "matricular", "candidatar", "candidatura",
    ),
    "zh": ("报名", "申请", "入学", "录取", "招生"),
}


def admission_intent_phrases() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Every localized (strong, medium) phrase, flattened with order preserved."""
    def _flat(table: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            phrase for phrases in table.values() for phrase in phrases
        ))

    return _flat(ADMISSION_INTENT_STRONG), _flat(ADMISSION_INTENT_MEDIUM)


# ── Localized "all documents sent" replies ─────────────────────────────────
#
# The prompts still ask for the English word "done" (the deterministic token),
# but a student who answers naturally in their own language must not get stuck
# after uploading everything.
DONE_REPLIES: dict[str, tuple[str, ...]] = {
    "hi": (
        "हो गया", "हो गई", "हो गए", "पूरा हो गया", "पूरी हो गई",
        "सब भेज दिया", "सब भेज दिए", "दस्तावेज़ भेज दिए", "मैंने भेज दिया",
        "समाप्त", "खत्म",
    ),
    "gu": (
        "થઈ ગયું", "થઈ ગયો", "બધું મોકલી દીધું", "મોકલી દીધું",
        "પૂરું થઈ ગયું", "સમાપ્ત", "ખતમ",
    ),
    "es": (
        "listo", "ya está", "ya esta", "terminé", "termine", "he terminado",
        "ya terminé", "ya termine", "enviado", "ya envié",
    ),
    "fr": (
        "terminé", "termine", "fini", "c'est fait", "j'ai terminé",
        "j'ai fini", "envoyé",
    ),
    "it": ("fatto", "finito", "ho finito", "terminato", "inviato"),
    "ja": (
        "完了", "完了しました", "終わりました", "終了", "送りました",
        "全部送りました",
    ),
    "pt": (
        "pronto", "terminei", "acabei", "já enviei", "ja enviei",
        "enviei tudo",
    ),
    "zh": ("完成", "完成了", "好了", "已经发送", "都发完了", "发送完了"),
}

_REPLY_PUNCT = ".,!?;:。！？、，؛।"


def _reply_key(text: str | None) -> str:
    raw = (text or "").strip().lower().replace("’", "'")
    return " ".join(raw.strip(_REPLY_PUNCT + " ").split())


def is_done_reply(text: str | None) -> bool:
    """True when *text* signals that all the student's documents were sent.

    English keeps its historical exact tokens (``done``/``finish``/``finished``).
    A localized phrase is matched as a substring only when the whole message is
    short (at most four words) *and* the phrase is multi-word or non-Latin; a
    single ASCII word must be the whole message. That keeps Spanish ``listo``
    or Hindi ``समाप्त`` from firing inside a longer unrelated sentence while
    still accepting a natural short reply such as ``हो गया``.
    """
    compact = _reply_key(text)
    if not compact:
        return False
    if compact in ("done", "finish", "finished"):
        return True
    short = len(compact.split()) <= 4
    for phrases in DONE_REPLIES.values():
        for phrase in phrases:
            if compact == phrase:
                return True
            if short and (" " in phrase or any(ord(ch) > 127 for ch in phrase)):
                if phrase in compact:
                    return True
    return False


def whatsapp_language_menu_options() -> list[tuple[str, str, str]]:
    """The numbered options of the WhatsApp first-contact language menu.

    Returns ``[(digit, code, label), ...]``. English, Hindi and Gujarati are
    always present, in that order; the six foreign languages follow in
    ``DEFAULT_FOREIGN_LANGUAGES`` order, filtered by the configured feature
    gate. Numbering is derived from the resulting list, so it stays contiguous
    when the feature is partially enabled.
    """
    codes = list(WHATSAPP_MENU_CORE_LANGUAGES)
    enabled = configured_foreign_languages()
    codes.extend(
        code for code in DEFAULT_FOREIGN_LANGUAGES if code in enabled
    )
    return [
        (str(digit), code, WHATSAPP_MENU_LABELS[code])
        for digit, code in enumerate(codes, 1)
    ]


def language_from_menu_reply(
    text: str | None,
    options: list[tuple[str, str, str]] | None = None,
) -> str | None:
    """The language selected by a reply to the WhatsApp language menu.

    Whole-message only: a leading number (``2``, ``2.``, ``2 - Hindi``), an
    English/native language name, or a slash code. Bare ``hi``/``gu`` are
    deliberately rejected -- ``hi`` is the most common WhatsApp greeting -- and
    bare ``it`` remains an English pronoun. The result is always one of
    *options*, so a disabled foreign language cannot be chosen by name.
    """
    choices = options if options is not None else whatsapp_language_menu_options()
    by_code = {code for _, code, _ in choices}
    raw = (text or "").strip()
    if not raw:
        return None

    digit_match = re.match(r"^(\d{1,2})(?:[.)\-:\s]|$)", raw)
    if digit_match:
        for digit, code, _ in choices:
            if digit == digit_match.group(1):
                return code
        return None

    token = raw.strip().strip(".,!?;:。！？、，")
    if not token:
        return None

    if token.startswith("/"):
        token = token[1:].strip().lower()
        if token in by_code:
            return token
        for code, aliases in _COMMAND_ALIASES.items():
            if token in aliases and code in by_code:
                return code
        return "en" if token in _EN_ALIASES and "en" in by_code else None

    low = token.lower()
    for code in by_code:
        label = WHATSAPP_MENU_LABELS[code]
        if low == label.lower() or low == label.split(" (", 1)[0].lower():
            return code
        if any(low == name.lower() for name in _LANGUAGE_NAMES.get(code, ())):
            return code

    # A bare code is accepted only when unambiguous; hi/gu are greetings and
    # "it" is an English word.
    if low in by_code and low not in ("hi", "gu", "it"):
        return low
    return None
