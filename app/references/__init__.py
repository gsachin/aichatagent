"""Reference formatting and voice→WhatsApp delivery (Phase 12)."""

from app.references.formatting import (
    Reference,
    answer_summary,
    build_voice_reference_message,
    normalize_phone,
    phone_match_key,
    references_from_citations,
    render_chat_references,
    render_references,
    render_whatsapp_references,
    split_whatsapp_answer,
    whatsapp_address,
)

__all__ = [
    "Reference",
    "answer_summary",
    "build_voice_reference_message",
    "normalize_phone",
    "phone_match_key",
    "references_from_citations",
    "render_chat_references",
    "render_references",
    "render_whatsapp_references",
    "split_whatsapp_answer",
    "whatsapp_address",
]
