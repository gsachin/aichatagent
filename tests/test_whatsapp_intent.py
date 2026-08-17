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
