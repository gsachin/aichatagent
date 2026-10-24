"""One place for every user-visible name, email address and phone number.

The legacy Meridian strings stay in the translation tables as templates. They
are translated or script-specific, and rewriting all of them into format
placeholders would make every table harder to read. Instead the deployment
brand is substituted at the boundary, where text leaves the application.

Tests pin the legacy values in ``tests/conftest.py``, which makes ``brand_text``
a no-op for the existing fixtures. A real deployment sets the DSPMU values in
``.env`` and every surface changes together.
"""

from __future__ import annotations

from typing import TypeVar

from app.config import settings

T = TypeVar("T")

#: The literal values the legacy tables use. Substitution is longest-first so
#: "Meridian University" never becomes "DSPMU University".
_LEGACY_COMPANY = "Meridian University"
_LEGACY_EMAIL = "admissions@meridian.edu"
_LEGACY_PHONE = "+1 (555) 204-7890"
_LEGACY_SHORT = "Meridian"

#: Example programme lists in the legacy onboarding prompts. They are prompts,
#: not facts: the DSPMU catalogue has no B.Tech or MBA entry until the PG list
#: is verified, so the examples must not suggest one.
_LEGACY_PROGRAM_EXAMPLES = (
    (
        "B.Tech Computer Science, MBA, M.Tech, BCA",
        "BA Political Science, B.Sc. Physics, B.Com., BBA",
    ),
    (
        "B.Tech Computer Science, MBA, M.Tech",
        "BA Political Science, B.Sc. Physics, B.Com.",
    ),
    (
        "B.Tech Computer Science, MBA",
        "BA Political Science, B.Sc. Physics",
    ),
    # Japanese and Chinese use the ideographic comma.
    (
        "B.Tech Computer Science、MBA、M.Tech、BCA",
        "BA Political Science、B.Sc. Physics、B.Com.、BBA",
    ),
    (
        "B.Tech Computer Science、MBA、M.Tech",
        "BA Political Science、B.Sc. Physics、B.Com.",
    ),
    ("B.Tech Computer Science、MBA", "BA Political Science、B.Sc. Physics"),
    ("B.Tech Computer Science", "BA Political Science"),
)


def brand_text(text: str) -> str:
    """Return *text* with the legacy Meridian brand replaced by settings.

    Only the known brand tokens are replaced. Caller-supplied values (a
    student's name, an email address they typed) are formatted into the
    template after this runs, so they are never rewritten by accident.
    """
    if not text:
        return text

    company = settings.COMPANY_NAME or _LEGACY_COMPANY
    short = settings.COMPANY_SHORT_NAME or company
    text = (
        text.replace(_LEGACY_COMPANY, company)
        .replace(_LEGACY_EMAIL, settings.ADMISSIONS_EMAIL or _LEGACY_EMAIL)
        .replace(_LEGACY_PHONE, settings.ADMISSIONS_PHONE or _LEGACY_PHONE)
        .replace(_LEGACY_SHORT, short)
    )
    if (settings.PROGRAM_PROFILE or "").strip().lower() == "dspmu":
        for legacy, replacement in _LEGACY_PROGRAM_EXAMPLES:
            text = text.replace(legacy, replacement)
    return text


def brand_value(value: T) -> T:
    """Recursively brand strings in a nested table (dict/list/tuple)."""
    if isinstance(value, str):
        return brand_text(value)  # type: ignore[return-value]
    if isinstance(value, dict):
        return {key: brand_value(item) for key, item in value.items()}  # type: ignore[return-value]
    if isinstance(value, list):
        return [brand_value(item) for item in value]  # type: ignore[return-value]
    if isinstance(value, tuple):
        return tuple(brand_value(item) for item in value)  # type: ignore[return-value]
    return value


def admissions_contact() -> tuple[str, str]:
    """The published admissions email and phone, in display form."""
    return settings.ADMISSIONS_EMAIL, settings.ADMISSIONS_PHONE
