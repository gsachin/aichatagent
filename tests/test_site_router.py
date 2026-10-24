"""The deterministic selective router used by CONTEXT_MODE=site.

These tests pin the safety property first: a route can only narrow the prompt,
and an unrecognised query keeps the full compact bundle. The rest pin the
specific selections and their provenance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import doc_context, site_router
from app.rag import clear_turn_context, context_bundle_for_query, query_rag
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]
DSPMU = ROOT / "content/dspmu"
PROMPT_PATHS = (
    "content/dspmu/core_cards.md,"
    "content/dspmu/site_map.md,"
    "content/dspmu/recent_notices.md"
)


@pytest.fixture(autouse=True)
def _clear_caches():
    doc_context.clear_cache()
    site_router.clear_cache()
    clear_turn_context()
    yield
    doc_context.clear_cache()
    site_router.clear_cache()
    clear_turn_context()


def _activate(override_settings) -> None:
    for name in (
        "core_cards.md",
        "site_map.md",
        "recent_notices.md",
        "facts.json",
        "notices.jsonl",
        "prompt_manifest.json",
    ):
        if not (DSPMU / name).exists():
            pytest.skip("generated DSPMU router files are missing; run the build")
    override_settings(
        CONTEXT_MODE="site",
        DOC_PATHS=PROMPT_PATHS,
        DOC_MAX_TOKENS=4000,
        CONTEXT_MANIFEST_PATH="content/dspmu/prompt_manifest.json",
        SITE_FACTS_PATH="content/dspmu/facts.json",
        SITE_NOTICES_PATH="content/dspmu/notices.jsonl",
        SITE_NOTICE_DB_PATH="content/dspmu/notices.sqlite3",
        SITE_NODES_DIR="content/dspmu/nodes",
        SITE_ROUTER_MAX_NOTICES=5,
    )
    doc_context.clear_cache()
    site_router.clear_cache()


@pytest.mark.parametrize(
    "question",
    [
        "What is the MBA fee?",
        "Tell me about M.Com",
        "PG programme list",
        "M.Sc eligibility",
        "postgraduate seats",
        "master's degree details",
        "एमबीए की फीस",
        "સ્નાતકોત્તર કાર્યક્રમ",
    ],
)
def test_pg_programme_and_fee_questions_get_a_deterministic_boundary(
    override_settings, question
):
    _activate(override_settings)

    answer = site_router.pg_boundary_answer(question)

    assert answer is not None
    assert "latest details" in answer
    assert settings.ADMISSIONS_EMAIL in answer
    assert settings.ADMISSIONS_PHONE in answer


@pytest.mark.parametrize(
    "question",
    [
        "PG last date",
        "PG admission process",
        "What is the registrar email?",
    ],
)
def test_pg_dates_and_admission_process_are_not_fee_boundaries(
    override_settings, question
):
    _activate(override_settings)

    assert site_router.pg_boundary_answer(question) is None


def test_the_pg_boundary_is_a_route_context_block(override_settings):
    _activate(override_settings)

    bundle = site_router.route_query("What is the MBA fee?")

    assert "PG programme and fee boundary" in bundle.text
    assert bundle.fact_ids == ()
    assert bundle.node_ids == ("core_cards",)
    assert "MBA fee" not in bundle.text

    from app.voice_grounding import enforce_grounding, is_grounded

    answer = site_router.pg_boundary_answer("What is the MBA fee?")
    assert answer is not None
    assert is_grounded(answer, bundle.text)
    assert enforce_grounding(answer, bundle.text) == answer


def test_query_rag_short_circuits_the_pg_boundary(override_settings):
    _activate(override_settings)

    answer = query_rag("What is the MBA fee?", mode="chat")

    assert answer is not None
    assert "latest details" in answer


def test_streaming_uses_the_raw_question_for_routing(override_settings, monkeypatch):
    _activate(override_settings)
    import asyncio

    import app.llm_backend as llm_backend
    from app.rag import last_context_bundle, query_rag_stream

    async def fake_generate_stream(*args, **kwargs):
        yield "ok"

    monkeypatch.setattr(llm_backend, "generate_stream", fake_generate_stream)

    async def collect():
        items = [
            item
            async for item in query_rag_stream(
                "SYSTEM PROMPT\nStudent's question: fee for BA Political Science",
                retrieval_query="What is the fee for BA Political Science?",
            )
        ]
        return items, last_context_bundle()

    items, bundle = asyncio.run(collect())
    assert items == ["ok"]
    assert bundle is not None
    assert "Programme fee fact" in bundle.text


def test_the_streaming_path_also_short_circuits_the_pg_boundary(override_settings):
    _activate(override_settings)
    import asyncio

    from app.rag import query_rag_stream

    async def collect():
        return [
            item
            async for item in query_rag_stream(
                "SYSTEM PROMPT\nStudent's question: What is the MBA fee?",
                retrieval_query="What is the MBA fee?",
            )
        ]

    items = asyncio.run(collect())

    assert items
    assert isinstance(items[0], str)
    assert "latest details" in items[0]


def test_a_fee_question_selects_the_exact_programme_fact(override_settings):
    _activate(override_settings)

    bundle = site_router.route_query(
        "What is the annual fee for BA Political Science?"
    )

    assert "Programme fee fact" in bundle.text
    assert "₹3,410" in bundle.text
    assert "₹3,185" in bundle.text
    assert "fee.ug.political-science.2026.gen" in bundle.fact_ids
    assert bundle.node_ids == ("core_cards",)
    assert bundle.notice_ids == ()
    assert bundle.valid_until == "2027-06-30"
    assert bundle.text != doc_context.document_context()


def test_a_hindi_fee_question_selects_the_same_fact(override_settings):
    _activate(override_settings)

    bundle = site_router.route_query("बी.ए राजनीति विज्ञान की फीस")

    assert "fee.ug.political-science.2026.gen" in bundle.fact_ids
    assert "₹3,410" in bundle.text


def test_a_long_tail_fee_is_added_even_when_the_core_table_omits_it(
    override_settings,
):
    _activate(override_settings)
    assert "Nagpuri" not in doc_context.document_context()

    bundle = site_router.route_query("What is the annual fee for BA Nagpuri?")

    assert "Nagpuri" in bundle.text
    assert "₹4,410" in bundle.text
    assert "₹3,985" in bundle.text
    assert "fee.ug.nagpuri.2026.gen" in bundle.fact_ids


def test_a_fee_question_without_a_programme_keeps_the_core_cards(
    override_settings,
):
    _activate(override_settings)

    bundle = site_router.route_query("What are the fees?")

    assert "Programme fee fact" not in bundle.text
    assert "₹3,170" in bundle.text
    assert bundle.fact_ids == ()
    assert bundle.node_ids == ("core_cards",)


def test_documents_dates_and_contact_stay_in_the_core_bundle(override_settings):
    _activate(override_settings)

    for question, expected in (
        ("What documents do I need for admission?", "Aadhar Card"),
        ("When is the last date for PG admission?", "25.10.2026"),
        ("What is the registrar email?", "registrar@dspmuranchi.ac.in"),
    ):
        bundle = site_router.route_query(question)
        assert expected in bundle.text, question
        assert bundle.node_ids == ("core_cards",), question


def test_an_about_question_selects_the_about_node(override_settings):
    _activate(override_settings)

    bundle = site_router.route_query("What is the history of DSPMU?")

    assert "page-about" in bundle.node_ids
    assert "1839" in bundle.text
    assert "https://dspmuranchi.ac.in/cms/about-the-university" in bundle.source_urls


def test_a_vision_question_selects_the_vision_node(override_settings):
    _activate(override_settings)

    bundle = site_router.route_query("What is the vision and mission?")

    assert "page-vision" in bundle.node_ids
    assert "Nurturing Excellence" in bundle.text


def test_a_notice_question_selects_non_pii_title_matches(override_settings):
    _activate(override_settings)

    bundle = site_router.route_query("Any notice about the PhD programme?")

    assert bundle.notice_ids
    assert "Fill Application Form for Ph.D. Programme" in bundle.text
    assert "recent_notices" in bundle.node_ids
    assert "## Matching non-PII notices" in bundle.text


def test_router_falls_back_to_jsonl_when_the_notice_store_is_missing(
    override_settings, tmp_path
):
    _activate(override_settings)
    override_settings(SITE_NOTICE_DB_PATH=str(tmp_path / "missing.sqlite3"))

    bundle = site_router.route_query("Any notice about the PhD programme?")

    assert "Fill Application Form for Ph.D. Programme" in bundle.text
    assert bundle.notice_ids


def test_core_cards_carry_the_pg_boundary():
    text = (DSPMU / "core_cards.md").read_text(encoding="utf-8")
    assert (
        "PG programme lists, fees, seats and eligibility details are out of scope"
        in text
    )


def test_the_generated_fts_store_is_available():
    from app.notice_store import search_notices

    db = DSPMU / "notices.sqlite3"
    if not db.exists():
        pytest.skip("generated notices.sqlite3 is missing; run the build")
    rows = search_notices("PhD", db_path=db)
    assert rows is not None
    assert any("Ph.D." in row["title"] for row in rows)


def test_a_result_request_is_a_boundary_not_a_notice_lookup(override_settings):
    _activate(override_settings)

    bundle = site_router.route_query("Show me the merit list")

    assert bundle.notice_ids == ()
    assert bundle.node_ids == ("core_cards",)
    assert "Boundaries" in bundle.text


def test_a_syllabus_question_selects_matching_syllabus_lines(override_settings):
    _activate(override_settings)

    bundle = site_router.route_query(
        "What is the syllabus for BA Political Science?"
    )

    assert "page-syllabus" in bundle.node_ids
    assert "Political Science" in bundle.text
    assert len(bundle.text) < len(doc_context.document_context())


def test_an_unknown_query_falls_back_to_the_full_compact_bundle(override_settings):
    _activate(override_settings)

    bundle = site_router.route_query("hello there")

    assert bundle.text == doc_context.document_context()
    assert bundle.node_ids == ("core_cards", "site_map", "recent_notices")


def test_doc_mode_bypasses_the_router(override_settings):
    _activate(override_settings)
    override_settings(CONTEXT_MODE="doc")

    bundle = context_bundle_for_query("What is the annual fee for BA Political Science?")

    assert bundle.text == doc_context.document_context()
    assert "Programme fee fact" not in bundle.text


def test_binding_the_route_makes_it_the_guard_evidence(override_settings):
    _activate(override_settings)
    from app.rag import bind_context_bundle, last_context_bundle

    bundle = site_router.route_query(
        "What is the annual fee for BA Political Science?"
    )
    bind_context_bundle(bundle)

    assert last_context_bundle() == bundle
    assert last_context_bundle().text == bundle.text


def test_pipeline_text_path_binds_the_same_bundle(override_settings):
    _activate(override_settings)
    from app.pipeline import build_rag_prompt_parts
    from app.rag import last_context_bundle

    system, _user = build_rag_prompt_parts(
        "What is the annual fee for BA Political Science?"
    )

    assert "Programme fee fact" in system
    bundle = last_context_bundle()
    assert bundle is not None
    assert "Programme fee fact" in bundle.text
    assert bundle.fact_ids


def test_every_route_fits_the_document_token_budget(override_settings):
    _activate(override_settings)

    for question in (
        "What is the annual fee for BA Political Science?",
        "What are the fees?",
        "What documents do I need for admission?",
        "When is the last date for PG admission?",
        "What is the registrar email?",
        "What is the history of DSPMU?",
        "What is the vision and mission?",
        "How many departments are there?",
        "Any notice about the PhD programme?",
        "Show me the merit list",
        "What is the syllabus for BA Political Science?",
        "hello there",
    ):
        bundle = site_router.route_query(question)
        assert doc_context._count_tokens(bundle.text) <= 4000, question
