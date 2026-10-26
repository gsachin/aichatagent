"""One place for every user-visible name, email address and phone number.

The translation tables now carry the DSPMU brand directly. ``brand_text`` is
kept as the boundary hook so callers do not need to know whether a future
deployment needs substitution.
"""

from __future__ import annotations

from typing import TypeVar

from app.config import settings

T = TypeVar("T")


def brand_text(text: str) -> str:
    return text


def brand_value(value: T) -> T:
    """Recursively apply :func:`brand_text` to a nested table."""
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
