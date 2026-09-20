"""
WhatsApp text-chat formatting tests.

Covers the Markdown -> plain-text strip applied at the WhatsApp webhook
reply choke point (LLM RAG answers and canned state-machine messages
alike) and the XML escaping applied once, at the TwiML boundary.
"""

import asyncio
from xml.sax.saxutils import escape

from app import main as m


def test_strip_markdown_bold_italic_headers():
    md = "# MBA Program\n\n**Tuition:** $18,500 per year and _includes_ materials."
    out = m._strip_markdown(md)
    assert out == "MBA Program\n\nTuition: $18,500 per year and includes materials."


def test_strip_markdown_bullets_and_hr():
    md = "- 2-year program\n- Fall intake\n\n---\n\nApply today"
    out = m._strip_markdown(md)
    assert out == "• 2-year program\n• Fall intake\n\nApply today"


def test_strip_markdown_links_and_code():
    out = m._strip_markdown("See [programs](https://meridian.test/p) and use `OTP` code")
    assert out == "See programs (https://meridian.test/p) and use OTP code"


def test_strip_markdown_table():
    md = "| Item | Cost |\n| --- | --- |\n| Tuition | $18,500 |"
    out = m._strip_markdown(md)
    assert "---" not in out
    assert "Item | Cost" in out
    assert "Tuition | $18,500" in out


def test_strip_markdown_plain_text_unchanged():
    plain = "Thanks! Your offer for MBA has been accepted. 🎉"
    assert m._strip_markdown(plain) == plain


def test_whatsapp_chat_rag_passes_history_and_profile(monkeypatch):
    import app.pipeline as pl

    seen = {}

    def fake(q, mode="voice", retrieval_query=None, history=None, profile=None):
        seen.update(
            q=q, mode=mode, retrieval_query=retrieval_query,
            history=history, profile=profile,
        )
        return "**Fees** are $18,500 & <competitive>"

    monkeypatch.setattr(pl, "run_rag_query_sync", fake)
    out = asyncio.run(
        m._whatsapp_chat_rag(
            "what are the fees?",
            history=["User: hi", "Assistant: hello"],
            profile={"Name": "Karthik", "Email": "", "Program of interest": "M.Tech"},
            program="M.Tech",
        )
    )

    # The helper returns raw text now; escaping belongs to the XML boundary so
    # that it happens once, after the markdown pass, for every branch.
    assert out == "**Fees** are $18,500 & <competitive>"
    assert m._strip_markdown(out) == "Fees are $18,500 & <competitive>"

    # The chat leg is no longer stateless.
    assert seen["history"] == ["User: hi", "Assistant: hello"]
    assert seen["profile"]["Name"] == "Karthik"
    # A question that names no program is retrieved against the one on file.
    assert seen["retrieval_query"] == "what are the fees? M.Tech"


def test_retrieval_query_keeps_a_program_the_question_names(monkeypatch):
    """A question naming its own program must not be seeded with another."""
    import app.pipeline as pl

    seen = {}

    def fake(q, mode="voice", retrieval_query=None, history=None, profile=None):
        seen["retrieval_query"] = retrieval_query
        return "ok"

    monkeypatch.setattr(pl, "run_rag_query_sync", fake)
    asyncio.run(m._whatsapp_chat_rag("what about MBA fees?", program="M.Tech"))
    assert seen["retrieval_query"] == "what about MBA fees?"


def test_answers_echoing_user_text_stay_valid_xml():
    """
    The TwiML must survive a name or program holding XML metacharacters.

    Only the RAG branch used to escape its own output, so a state-machine
    answer echoing a student back at themselves — "Thanks Tom & Jerry!" —
    produced malformed TwiML that Twilio rejected outright.
    """
    import xml.etree.ElementTree as ET

    answer = "Thanks Tom & Jerry! Your admission for *B.Tech <CS>* is noted."
    # The webhook strips Markdown first and escapes last; the XML round-trip
    # must hand back exactly the text that was sent.
    stripped = m._strip_markdown(answer)
    xml = m.WHATSAPP_TWIML_TEMPLATE.format(answer=escape(stripped))

    assert "&amp;" in xml and "&lt;" in xml
    parsed = ET.fromstring(xml).findtext("Message")  # malformed XML raises here
    assert parsed == stripped
