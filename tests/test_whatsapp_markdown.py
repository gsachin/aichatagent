"""
WhatsApp text-chat formatting tests.

Covers the Markdown -> plain-text strip applied at the WhatsApp webhook
reply choke point (LLM RAG answers and canned state-machine messages
alike) and the XML escaping in _whatsapp_chat_rag.
"""

import asyncio

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


def test_whatsapp_chat_rag_escapes_xml_then_strip_is_safe(monkeypatch):
    import app.pipeline as pl

    monkeypatch.setattr(
        pl, "run_rag_query_sync",
        lambda q, mode="voice": "**Fees** are $18,500 & <competitive>",
    )
    out = asyncio.run(m._whatsapp_chat_rag("what are the fees?"))
    # XML-escaped for TwiML...
    assert out == "**Fees** are $18,500 &amp; &lt;competitive&gt;"
    # ...and the webhook's markdown strip keeps the escapes intact.
    assert m._strip_markdown(out) == "Fees are $18,500 &amp; &lt;competitive&gt;"
