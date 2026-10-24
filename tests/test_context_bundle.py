"""ContextBundle and CONTEXT_MODE=site tests.

The prompt split gives the model a compact corpus and the guard a full entity
corpus. These tests pin the seam that keeps the two from drifting: the bundle
text is the prompt text, the sidecar metadata is attached only when it matches
the configured DOC_PATHS, and site mode and doc mode share the same bundle
until the selective router replaces it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSPMU = ROOT / "content/dspmu"
PROMPT_PATHS = (
    "content/dspmu/core_cards.md,"
    "content/dspmu/site_map.md,"
    "content/dspmu/recent_notices.md"
)
MANIFEST = "content/dspmu/prompt_manifest.json"

from app import doc_context  # noqa: E402
from app.rag import (  # noqa: E402
    context_bundle_for_query,
    context_for_query,
    last_context_bundle,
)

pytestmark = pytest.mark.usefixtures("_clear_doc_cache")


@pytest.fixture(autouse=True)
def _clear_doc_cache():
    doc_context.clear_cache()
    yield
    doc_context.clear_cache()


def _require() -> None:
    for name in (
        "core_cards.md",
        "site_map.md",
        "recent_notices.md",
        "prompt_manifest.json",
    ):
        if not (DSPMU / name).exists():
            pytest.skip("generated DSPMU prompt files are missing; run the build")


def _activate(override_settings, mode: str = "site") -> None:
    _require()
    override_settings(
        CONTEXT_MODE=mode,
        DOC_PATHS=PROMPT_PATHS,
        DOC_MAX_TOKENS=4000,
        CONTEXT_MANIFEST_PATH=MANIFEST,
    )
    doc_context.clear_cache()


def test_document_bundle_reads_prompt_manifest(override_settings):
    _activate(override_settings)
    bundle = doc_context.document_bundle()
    assert bundle.text
    assert "Political Science" in bundle.text
    assert bundle.node_ids == ("core_cards", "site_map", "recent_notices")
    assert "fee.ug.political-science.2026.gen" in bundle.fact_ids
    assert bundle.source_urls
    assert bundle.valid_until == "2027-06-30"


def test_site_and_doc_modes_share_the_same_bundle_text(override_settings):
    _activate(override_settings, "site")
    site = context_bundle_for_query("What is the fee?")
    site_text = context_for_query("What is the fee?")

    _activate(override_settings, "doc")
    doc = context_bundle_for_query("What is the fee?")

    assert site.text == doc.text == site_text
    assert site.fact_ids == doc.fact_ids
    assert site.node_ids == doc.node_ids


def test_off_mode_returns_an_empty_bundle(override_settings):
    _activate(override_settings, "off")
    bundle = context_bundle_for_query("What is the fee?")
    assert bundle.text == ""
    assert context_for_query("What is the fee?") == ""
    assert last_context_bundle() is None


def test_last_context_bundle_falls_back_to_the_configured_prompt(override_settings):
    _activate(override_settings, "site")
    bundle = last_context_bundle()
    assert bundle is not None
    assert bundle.text == doc_context.document_context()
    assert bundle.fact_ids


def test_a_stale_manifest_does_not_attach_provenance_to_other_text(
    tmp_path: Path, override_settings
):
    path = tmp_path / "other.md"
    path.write_text("A different prompt.", encoding="utf-8")
    override_settings(
        CONTEXT_MODE="site",
        DOC_PATHS=str(path),
        DOC_MAX_TOKENS=1000,
        CONTEXT_MANIFEST_PATH=MANIFEST,
    )
    doc_context.clear_cache()

    bundle = doc_context.document_bundle()
    assert bundle.text == "A different prompt."
    assert bundle.fact_ids == ()
    assert bundle.node_ids == ()
    assert bundle.valid_until is None


def test_voice_guard_evidence_comes_from_the_bundle(override_settings):
    _activate(override_settings, "site")
    from app.voice_handler import VoiceCallSession

    call = VoiceCallSession.__new__(VoiceCallSession)
    call._grounding_evidence = []
    call.note_turn_context()
    assert call._grounding_evidence
    assert call._grounding_evidence[0] == doc_context.document_context()


def test_manifest_json_is_well_formed():
    _require()
    manifest = json.loads((DSPMU / "prompt_manifest.json").read_text(encoding="utf-8"))
    assert manifest["prompt_order"] == [
        "core_cards.md",
        "site_map.md",
        "recent_notices.md",
    ]
    assert manifest["node_ids"] == ["core_cards", "site_map", "recent_notices"]
    assert manifest["fact_ids"]
    assert manifest["source_urls"]
