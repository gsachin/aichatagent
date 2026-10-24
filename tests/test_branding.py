"""The deployment brand is substituted at the output boundary.

The legacy translation tables still contain Meridian strings. These tests pin
the substitution itself and the example environment, not those templates: the
suite's conftest deliberately pins Meridian so the archived fixtures remain
meaningful.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.branding import brand_text, brand_value
from app.whatsapp_text import whatsapp_text

ROOT = Path(__file__).resolve().parents[1]


def test_brand_text_replaces_name_email_and_phone(override_settings):
    override_settings(
        COMPANY_NAME="Example University",
        COMPANY_SHORT_NAME="Example",
        ADMISSIONS_EMAIL="admissions@example.edu",
        ADMISSIONS_PHONE="+91 11111 22222",
    )

    assert brand_text("Meridian University does not offer it.") == (
        "Example University does not offer it."
    )
    assert brand_text("Ask Meridian admissions.") == "Ask Example admissions."
    assert brand_text("Email admissions@meridian.edu.") == (
        "Email admissions@example.edu."
    )
    assert brand_text("Call +1 (555) 204-7890.") == (
        "Call +91 11111 22222."
    )


def test_brand_text_leaves_unrelated_text_alone(override_settings):
    override_settings(
        COMPANY_NAME="Example University",
        COMPANY_SHORT_NAME="Example",
        ADMISSIONS_EMAIL="admissions@example.edu",
        ADMISSIONS_PHONE="+91 11111 22222",
    )
    text = "The student's name is Asha and her marks are 88."
    assert brand_text(text) == text


def test_brand_text_replaces_legacy_programme_examples(override_settings):
    override_settings(
        COMPANY_NAME="Example University",
        COMPANY_SHORT_NAME="Example",
        ADMISSIONS_EMAIL="admissions@example.edu",
        ADMISSIONS_PHONE="+91 11111 22222",
        PROGRAM_PROFILE="dspmu",
    )
    assert brand_text(
        "Which programme? (e.g., B.Tech Computer Science, MBA, M.Tech)"
    ) == "Which programme? (e.g., BA Political Science, B.Sc. Physics, B.Com.)"
    assert brand_text("I want B.Tech Computer Science") == (
        "I want BA Political Science"
    )


def test_brand_value_walks_nested_tables(override_settings):
    override_settings(
        COMPANY_NAME="Example University",
        COMPANY_SHORT_NAME="Example",
        ADMISSIONS_EMAIL="admissions@example.edu",
        ADMISSIONS_PHONE="+91 11111 22222",
    )
    table = {
        "es": ["Meridian University", ("Meridian", {"email": "admissions@meridian.edu"})],
    }
    assert brand_value(table) == {
        "es": ["Example University", ("Example", {"email": "admissions@example.edu"})],
    }


def test_whatsapp_text_brands_the_template_before_formatting(override_settings):
    override_settings(
        COMPANY_NAME="Example University",
        COMPANY_SHORT_NAME="Example",
        ADMISSIONS_EMAIL="admissions@example.edu",
        ADMISSIONS_PHONE="+91 11111 22222",
    )
    rendered = whatsapp_text(
        "greeting_known_name",
        "en",
        "Hello {name}! I'm the Meridian University admissions assistant.",
        name="Meridian",
    )
    assert rendered == (
        "Hello Meridian! I'm the Example University admissions assistant."
    )

    translated = whatsapp_text(
        "greeting_known_name",
        "es",
        "Hello {name}! I'm the Meridian University admissions assistant.",
        name="Asha",
    )
    assert "Example University" in translated
    assert "Meridian University" not in translated


def test_env_example_is_branded_for_dspmu():
    text = ROOT.joinpath(".env.example").read_text(encoding="utf-8")
    assert "COMPANY_NAME=Dr. Shyama Prasad Mukherjee University" in text
    assert "COMPANY_SHORT_NAME=DSPMU" in text
    assert "ADMISSIONS_EMAIL=registrar@dspmuranchi.ac.in" in text
    assert "ADMISSIONS_PHONE=+91 930 455 261" in text
    assert "PROGRAM_PROFILE=dspmu" in text
    assert "Meridian" not in text


def test_import_time_tables_are_branded_in_a_fresh_process():
    """The refusal and closing constants are built at import; prove it works."""
    env = os.environ.copy()
    env.update(
        COMPANY_NAME="Example University",
        COMPANY_SHORT_NAME="Example",
        ADMISSIONS_EMAIL="admissions@example.edu",
        ADMISSIONS_PHONE="+91 11111 22222",
    )
    code = (
        "import json;"
        "from app.voice_grounding import UNOFFERED_REFUSALS, ESCALATION_REFUSALS;"
        "from app.localized_text import CLOSING_REPLIES;"
        "print(json.dumps({"
        "'unoffered': UNOFFERED_REFUSALS[0],"
        "'escalation': ESCALATION_REFUSALS[0],"
        "'closing': CLOSING_REPLIES['es'],"
        "}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert "Example University" in payload["unoffered"]
    assert "admissions@example.edu" in payload["escalation"]
    assert "Example University" in payload["closing"]
    assert "Meridian" not in json.dumps(payload)
