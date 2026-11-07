"""IVR selection: digits 1-4 unchanged, digit 9 opens the six-language menu."""

from __future__ import annotations

import pytest

CALLER = "+14155550133"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_first_menu_offers_the_second_level_without_changing_1_to_4(client):
    text = client.get("/twilio/voice", params={"From": CALLER}).text
    assert "Press 1 for English." in text
    assert "Press 2 for Hindi." in text
    assert "Press 3 for Gujarati." in text
    assert "Press 4 to continue in the default language." in text
    assert "press 9" in text.lower()


def test_digit_9_returns_the_foreign_menu(client):
    text = client.get(
        "/twilio/voice/connect", params={"Digits": "9", "From": CALLER}
    ).text
    assert "Press 1 for Spanish." in text
    assert "Press 2 for French." in text
    assert "Press 3 for Italian." in text
    assert "Press 4 for Japanese." in text
    assert "Press 5 for Portuguese." in text
    assert "Press 6 for Chinese." in text
    assert "stage=foreign" in text
    assert "<Connect>" in text  # timeout fallback still connects


@pytest.mark.parametrize(
    "digit,code",
    [("1", "es"), ("2", "fr"), ("3", "it"), ("4", "ja"), ("5", "pt"), ("6", "zh")],
)
def test_foreign_digit_mapping(client, digit, code):
    text = client.get(
        "/twilio/voice/connect",
        params={"Digits": digit, "stage": "foreign", "From": CALLER},
    ).text
    assert f'<Parameter name="language" value="{code}" />' in text


@pytest.mark.parametrize(
    "digit,code",
    [("1", "en"), ("2", "hi"), ("3", "gu"), ("4", "en"), ("9", "en")],
)
def test_first_level_digits_keep_their_old_meaning(client, digit, code):
    text = client.get(
        "/twilio/voice/connect", params={"Digits": digit, "From": CALLER}
    ).text
    assert f'<Parameter name="language" value="{code}" />' in text


def test_foreign_menu_timeout_keeps_the_pinned_language(client):
    text = client.get(
        "/twilio/voice/connect",
        params={"Digits": "", "stage": "foreign", "language": "hi"},
    ).text
    assert '<Parameter name="language" value="hi" />' in text


def test_disabling_the_feature_restores_the_old_first_menu(client, override_settings):
    override_settings(FOREIGN_LANGUAGES="")

    text = client.get("/twilio/voice", params={"From": CALLER}).text
    assert "press 9" not in text.lower()
    # Digit 9 falls back to the default language, exactly as before.
    text = client.get(
        "/twilio/voice/connect", params={"Digits": "9", "From": CALLER}
    ).text
    assert "Press 1 for Spanish." not in text
    assert '<Parameter name="language" value="en" />' in text
