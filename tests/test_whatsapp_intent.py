"""
WhatsApp admission-intent detection tests.

Real-world phrasings (including the "I wan to take the admission in MBA"
typo seen in production) must hit the strong-keyword fast path and never
depend on the LLM confirmation call.
"""

import asyncio

from app import main as m


CASES = [
    # (message, expected_intent, expected_program)
    ("i wan to take the admission in mba", True, "MBA"),
    ("i want to take the admission in mba program", True, "MBA"),
    ("take admission for bca", True, "BCA"),
    ("I want to take admission", True, ""),
    ("just asking about the fees", False, ""),
    ("are you there", False, ""),
]


def test_strong_intent_keywords_catch_common_phrasings():
    for msg, intent, program in CASES:
        is_interested, detected = asyncio.run(
            m._detect_admission_intent_whatsapp(msg.lower())
        )
        assert is_interested is intent, msg
        assert detected == program, msg


LOCALIZED_CASES = [
    # (message, expected_program) — all hit the strong fast path, no LLM.
    ("मैं एम.कॉम में एडमिशन लेना चाहता हूँ", "M.Com"),
    ("मुझे एमबीए में प्रवेश लेना है", "MBA"),
    ("મને એમ.કોમમાં એડમિશન લેવું છે", "M.Com"),
    ("quiero inscribirme en M.Com", "M.Com"),
    ("je veux m'inscrire en M.Tech", "M.Tech"),
    ("voglio iscrivermi al corso MBA", "MBA"),
    ("quero me inscrever no curso de M.Com", "M.Com"),
    ("入学したいです。MBA", "MBA"),
    ("我想报名 M.Com", "M.Com"),
]


def test_localized_intent_keywords_catch_natural_phrasings():
    for msg, program in LOCALIZED_CASES:
        is_interested, detected = asyncio.run(
            m._detect_admission_intent_whatsapp(msg.lower())
        )
        assert is_interested is True, msg
        assert detected == program, msg


def test_medium_keyword_consults_the_classifier(monkeypatch):
    """A process question must reach the classifier, which can say no."""
    seen = []

    def fake_chat(**kwargs):
        seen.append(kwargs)
        return "no"

    import app.llm_backend as backend

    monkeypatch.setattr(backend, "chat", fake_chat)

    is_interested, detected = asyncio.run(
        m._detect_admission_intent_whatsapp("मैं प्रवेश प्रक्रिया जानना चाहता हूँ")
    )

    assert is_interested is False
    assert detected == ""
    assert seen, "a medium phrase must be confirmed by the classifier"


def test_medium_keyword_classifier_can_confirm(monkeypatch):
    import app.llm_backend as backend

    monkeypatch.setattr(backend, "chat", lambda **kwargs: "yes")

    is_interested, detected = asyncio.run(
        m._detect_admission_intent_whatsapp("मैं एडमिशन के लिए तैयार हूँ")
    )

    assert is_interested is True
    assert detected == ""


def test_question_shape_skips_the_classifier(monkeypatch):
    """A question mentioning 'apply' must not pay for an intent model call.

    These are the exact shapes that cost ~1.2-1.6 s per turn in production:
    the medium keyword ("申请") sent them to the classifier, which then said
    "no" because a question is not intent.
    """
    import app.llm_backend as backend

    calls = []
    monkeypatch.setattr(backend, "chat", lambda **kwargs: calls.append(kwargs) or "yes")

    for msg in (
        "申请MBA项目什么要求",
        "能列下具体的申请要求么？ 比如gpa, 语言成绩",
        "what are the requirements to apply for MBA?",
        "¿Cómo puedo aplicar al programa?",
        "MBA admission की प्रक्रिया क्या है",
    ):
        assert asyncio.run(m._detect_admission_intent_whatsapp(msg.lower())) == (False, "")

    assert calls == [], "a question must be answered, not classified"


def test_intent_statement_without_question_shape_still_reaches_the_classifier(monkeypatch):
    import app.llm_backend as backend

    calls = []
    monkeypatch.setattr(backend, "chat", lambda **kwargs: calls.append(kwargs) or "yes")

    assert asyncio.run(
        m._detect_admission_intent_whatsapp("i'm interested in the mba program")
    ) == (True, "MBA")
    assert calls, "an intent statement must still be confirmed"


def test_question_marks_and_words_are_recognised_across_scripts():
    from app.languages import looks_like_question

    for text in (
        "What are the fees?",
        "能列下具体的申请要求么？",
        "MBA admission की प्रक्रिया क्या है",
        "MBA માં પ્રવેશ કેવી રીતે લેવો?",
        "¿Cuál es la matrícula?",
        "Quel est le frais de scolarité ?",
        "Qual è la retta?",
        "授業料はいくらですか",
        "Qual é o valor da matrícula?",
    ):
        assert looks_like_question(text), text

    for text in (
        "I want to take admission",
        "मैं एमबीए में प्रवेश लेना है",
        # A danda ends a declarative Hindi sentence; it is not a question mark.
        "मैं एडमिशन लेना चाहता हूँ।",
        "I am interested in the MBA program",
        "谢谢",
    ):
        assert not looks_like_question(text), text


def test_localized_done_replies_are_recognised():
    from app.languages import is_done_reply

    assert is_done_reply("done")
    assert is_done_reply("हो गया")
    assert is_done_reply("सब भेज दिया।")
    assert is_done_reply("થઈ ગયું")
    assert is_done_reply("terminé")

    # A full sentence that merely contains a done-ish word must not generate
    # an offer behind the student's back.
    assert not is_done_reply("मेरा पूरा नाम आशा है")
    assert not is_done_reply("मेरी परीक्षा समाप्त हो गई है")
    assert not is_done_reply("tell me about mba")
