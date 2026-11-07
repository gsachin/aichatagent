"""Conversational turns do not enter retrieval or deep search."""

from __future__ import annotations

from app.agent.conversation import conversational_reply
from app.agent.text import build_text_answer, render_text_answer


def test_greeting_is_conversational():
    reply = conversational_reply("hello")
    assert reply is not None
    assert "DSPMU" in reply


def test_capability_and_meta_questions_are_conversational():
    for query in (
        "who are you",
        "what can you do",
        "can you search deep",
        "do you understand when I ask about program what is my intent of ask?",
        "thanks",
    ):
        assert conversational_reply(query) is not None, query


def test_knowledge_questions_are_not_intercepted():
    for query in (
        "hello, what is the fee for BA History?",
        "what can you do with a BA History degree?",
        "can you check deep search to find university program",
        "what programmes are offered?",
    ):
        assert conversational_reply(query) is None, query


def test_build_text_answer_returns_conversation_without_calling_the_llm():
    def must_not_call(_messages):
        raise AssertionError("conversational turn must not call the LLM")

    result = build_text_answer("hello", llm=must_not_call)
    assert result is not None
    assert result.status == "conversational"
    assert result.answer_type == "answer"
    assert "DSPMU" in render_text_answer(result)


def test_meta_request_is_answered_conversationally():
    def must_not_call(_messages):
        raise AssertionError("meta turn must not call the LLM")

    result = build_text_answer("can you search deep", llm=must_not_call)
    assert result is not None
    assert result.status == "conversational"
    assert "official DSPMU website" in result.answer
