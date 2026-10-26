"""The WhatsApp deterministic-prompt translation table.

English literals live at the call sites; this table only supplies the eight
selectable languages. The tests pin completeness, placeholder parity, the
English/unknown fallback and the length budget.
"""

from __future__ import annotations

import string

import pytest

from app.whatsapp_text import (
    OFFER_FIELD_LABELS,
    OFFER_LIST_JOIN,
    WHATSAPP_TEXT,
    WHATSAPP_TEXT_KEYS,
    WHATSAPP_TEXT_LANGUAGES,
    whatsapp_offer_fields,
    whatsapp_text,
)

#: The exact English templates used at the call sites. Also the placeholder
#: contract every translation must preserve.
ENGLISH_DEFAULTS = {
    "media_audio_unsupported": (
        "I can't listen to voice notes. Please type your question here in the "
        "chat and I'll answer right away!"
    ),
    "media_document_received": (
        "📄 Got your document! I'm processing it now. If you have more "
        "documents, send them too, or type 'done' when you're finished."
    ),
    "media_unknown_type": (
        "I received your file but I can only accept photos (transcripts, IDs) "
        "and PDFs. Please try sending it as an image or PDF."
    ),
    "empty_message": "Hello! Send me a question about DSPMU admissions.",
    "greeting_known_name": (
        "Hello {name}! 👋 I'm the Dr. Shyama Prasad Mukherjee University admissions assistant. "
        "What would you like to know?"
    ),
    "greeting_unknown_name": (
        "Hello! 👋 I'm the Dr. Shyama Prasad Mukherjee University admissions assistant. What's "
        "your name?"
    ),
    "name_corrected_ask_email": (
        "Thanks — I've corrected your name to {name}. What's your email "
        "address?"
    ),
    "name_corrected_help": (
        "Thanks — I've corrected your name to {name}. How can I help with your "
        "admission?"
    ),
    "name_change_prompt": (
        "Of course — what should I change your name to? (You can just reply "
        "with the name.)"
    ),
    "name_saved_help": (
        "Thanks — your name is now {name}. How can I help with your admission?"
    ),
    "name_not_caught": (
        "Sorry, I didn't catch a name there. What should I change it to?"
    ),
    "update_name_prompt": "Sure — what should I change your name to?",
    "name_captured_ask_email": (
        "Thanks {name}! What's your email address? I'll use it to send you "
        "program details and follow up."
    ),
    "name_captured_profile_updated": (
        "Thanks {name}! I've updated your profile. How can I help you with "
        "DSPMU admissions?"
    ),
    "ask_name_missing": "Hi! Before I help you, could you tell me your name?",
    "email_saved_with_name": "Thanks {name}! I've saved your email.",
    "email_saved_no_name": "Thanks — I've saved your email.",
    "update_email_saved": "Thanks — I've updated your email address.",
    "update_email_prompt": "Sure — what's the correct email address?",
    "email_captured_ask_name": "Got your email! And what's your name?",
    "email_captured_help": (
        "Thanks! I've saved your email. How can I help you with admissions "
        "today?"
    ),
    "email_missing_ask": (
        "Hi {name}! Could you share your email address? I'll use it to send "
        "you program details and follow up later."
    ),
    "program_detected": (
        "**{program}** — got it! 🎓\n\nTo proceed with your admission, say: "
        "'I want to take admission' and I'll guide you through the document "
        "upload process."
    ),
    "program_choice": (
        "**{program}** — great choice! 🎓\n\nTo proceed with your admission, "
        "say: 'I want to take admission' and I'll guide you through the "
        "document upload process."
    ),
    "program_ask_awaiting": (
        "Which program would you like? (e.g. B.Tech Computer Science, MBA, "
        "M.Tech, BCA)"
    ),
    "program_ask_interested": (
        "Which program are you interested in? (e.g., B.Tech Computer Science, "
        "MBA, M.Tech, BCA)"
    ),
    "program_ask_update": (
        "Sure — which program would you like instead? (e.g. B.Tech Computer "
        "Science, MBA, M.Tech, BCA)"
    ),
    "admission_docs_prompt": (
        "Great! To process your admission for *{program}*, please upload the "
        "following documents:\n\n📄 Transcript / Mark Sheet\n🆔 ID Proof "
        "(Passport, Aadhaar, or Driver's License)\n📝 Any additional "
        "certificates (optional)\n\nJust send clear photos or PDFs right here "
        "in WhatsApp. Type 'done' when you've sent everything."
    ),
    "update_field_prompt": (
        "No problem! What would you like to update? Your name, email, or "
        "program interest?"
    ),
    "update_field_unknown_choice": (
        "You can update your name, email, or program interest — which one?"
    ),
    "offer_accepted": (
        "🎉 Congratulations {name}! Your offer for *{program}* has been "
        "accepted. You'll receive a payment link shortly to confirm your seat."
    ),
    "offer_declined": (
        "Understood, {name}. Your offer for *{program}* has been declined. If "
        "you change your mind or want to explore other programs, just let me "
        "know!"
    ),
    "offer_not_found": (
        "I couldn't find a recent offer letter for you. How can I help with "
        "your admission?"
    ),
    "yes_confirmed_program": (
        "Great {name}! You're confirmed for *{program}*. To proceed with "
        "admission, say: 'I want to take admission'"
    ),
    "offer_missing_fields": (
        "Before I can generate your offer letter, I still need: {fields}."
    ),
    "offer_no_documents": (
        "I don't see any documents from you yet. Please send clear photos or "
        "PDFs of your transcript/marksheet and ID proof, then type 'done'."
    ),
    "offer_success": (
        "🎓 Great news, {name}! Your offer letter for {program} is ready — "
        "I've sent it here on WhatsApp and emailed you a copy. Reply 'accept' "
        "or 'decline' when you're ready."
    ),
    "offer_failure": (
        "Hmm, I couldn't generate your offer letter right now. Please try "
        "again in a moment."
    ),
    "rag_failure": (
        "Sorry, I couldn't process your question. Please try again."
    ),
    "safety_fallback": (
        "I have you as {name}. How can I help with DSPMU admissions?"
    ),
}

DUMMY = {"name": "Asha", "program": "MBA", "fields": "your full name"}


def _placeholders(template: str) -> set[str]:
    return {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name
    }


def test_key_inventory_matches_the_english_defaults():
    assert set(WHATSAPP_TEXT_KEYS) == set(ENGLISH_DEFAULTS)
    assert len(WHATSAPP_TEXT_KEYS) == 40


def test_every_selectable_language_has_every_key():
    for language in WHATSAPP_TEXT_LANGUAGES:
        missing = set(WHATSAPP_TEXT_KEYS) - set(WHATSAPP_TEXT[language])
        assert not missing, f"{language} missing {sorted(missing)}"


def test_english_is_not_in_the_translation_table():
    assert "en" not in WHATSAPP_TEXT


def test_translations_keep_the_english_placeholders():
    for language in WHATSAPP_TEXT_LANGUAGES:
        for key in WHATSAPP_TEXT_KEYS:
            expected = _placeholders(ENGLISH_DEFAULTS[key])
            actual = _placeholders(WHATSAPP_TEXT[language][key])
            assert actual == expected, (language, key, actual, expected)


def test_every_translation_renders_without_braces():
    for language in WHATSAPP_TEXT_LANGUAGES:
        for key in WHATSAPP_TEXT_KEYS:
            rendered = WHATSAPP_TEXT[language][key].format(**DUMMY)
            assert "{" not in rendered and "}" not in rendered, (language, key)


def test_english_and_unknown_languages_return_the_default():
    for key in WHATSAPP_TEXT_KEYS:
        default = ENGLISH_DEFAULTS[key].format(**DUMMY)
        for language in ("en", "", "de", "xx-YY"):
            assert whatsapp_text(key, language, default) == default


def test_translated_prompts_are_selected_and_formatted():
    greeting = whatsapp_text(
        "greeting_known_name", "hi", ENGLISH_DEFAULTS["greeting_known_name"],
        name="Asha",
    )
    assert "नमस्ते" in greeting and "Asha" in greeting
    assert "Hello" not in greeting


def test_regional_and_alias_codes_normalize():
    assert whatsapp_text(
        "email_saved_no_name", "pt-BR", ENGLISH_DEFAULTS["email_saved_no_name"]
    ) == WHATSAPP_TEXT["pt"]["email_saved_no_name"]
    assert whatsapp_text(
        "email_saved_no_name", "cmn", ENGLISH_DEFAULTS["email_saved_no_name"]
    ) == WHATSAPP_TEXT["zh"]["email_saved_no_name"]


def test_missing_key_falls_back_to_english():
    # A key absent from the table but present at a call site must never raise.
    assert whatsapp_text(
        "not_a_real_key", "hi", "English default"
    ) == "English default"


def test_malformed_translation_falls_back_to_english(monkeypatch):
    monkeypatch.setitem(
        WHATSAPP_TEXT["hi"], "greeting_known_name", "{oops}"
    )
    assert whatsapp_text(
        "greeting_known_name", "hi", ENGLISH_DEFAULTS["greeting_known_name"],
        name="Asha",
    ) == "Hello Asha! 👋 I'm the Dr. Shyama Prasad Mukherjee University admissions assistant. " \
         "What would you like to know?"


def test_every_rendered_message_fits_the_whatsapp_budget():
    for language in WHATSAPP_TEXT_LANGUAGES:
        for key in WHATSAPP_TEXT_KEYS:
            rendered = WHATSAPP_TEXT[language][key].format(**DUMMY)
            assert len(rendered) < 1600, (language, key, len(rendered))


def test_offer_field_labels_cover_every_readiness_field():
    fields = {"name", "email", "phone_number", "program_interest",
              "document", "lead"}
    for language in WHATSAPP_TEXT_LANGUAGES:
        assert fields <= set(OFFER_FIELD_LABELS[language])
        separator, final = OFFER_LIST_JOIN[language]
        assert isinstance(separator, str) and isinstance(final, str)


def test_offer_missing_fields_english_delegates_to_the_existing_helper(
    monkeypatch,
):
    import app.offers.service as offers

    monkeypatch.setattr(
        offers, "missing_fields_text", lambda missing: "STUB:" + ",".join(missing)
    )
    assert whatsapp_offer_fields(["name", "email"], "en") == "STUB:name,email"
    assert whatsapp_offer_fields(["name", "email"], "") == "STUB:name,email"


def test_offer_missing_fields_are_localized():
    for language in WHATSAPP_TEXT_LANGUAGES:
        text = whatsapp_offer_fields(["name", "document"], language)
        assert "your full name" not in text
        assert OFFER_FIELD_LABELS[language]["name"] in text
        assert OFFER_FIELD_LABELS[language]["document"] in text


def test_offer_missing_fields_join_naturally():
    assert whatsapp_offer_fields(["name", "email"], "hi") == (
        "आपका पूरा नाम और आपका ईमेल पता"
    )
    assert whatsapp_offer_fields(["name", "email", "document"], "es") == (
        "su nombre completo, su correo electrónico y al menos un documento "
        "(expediente, identificación o boletín de notas)"
    )
    assert whatsapp_offer_fields(["name", "email"], "zh") == (
        "您的全名和您的电子邮箱"
    )


def test_offer_missing_fields_empty():
    assert whatsapp_offer_fields([], "hi") == ""
    assert whatsapp_offer_fields([], "en") == ""
