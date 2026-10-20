"""The greeting warmup covers every language the deployment can select.

Assets are the first choice and are checked per language/direction, so the
warmup only synthesises when an asset is missing. It must still name the
foreign languages, or a deploy that skipped the asset build would leave their
first caller paying live synthesis with no boot-time signal.
"""

from __future__ import annotations


def test_warmup_covers_the_enabled_foreign_languages():
    import app.main as main
    from app.languages import configured_foreign_languages

    enabled = configured_foreign_languages()
    warmup = set(main._GREETING_WARMUP_LANGUAGES)

    assert {"en", "hi", "gu"} <= warmup
    assert enabled <= warmup, (
        "every enabled foreign language must be in the warmup fallback: "
        f"missing {sorted(enabled - warmup)}"
    )
