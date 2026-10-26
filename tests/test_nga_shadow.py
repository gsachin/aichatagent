"""Phase 7: shadow integration at the answer seam."""

from __future__ import annotations

import json

from app.agent.shadow import (
    ShadowRunner,
    reset_shadow_runner,
    shadow_enabled,
    submit_shadow,
)
from app.config import settings
from app.context_bundle import ContextBundle
from tests.nga_helpers import build_store, seed_page


def _static() -> ContextBundle:
    return ContextBundle(
        text="static profile",
        node_ids=("core_cards",),
        fact_ids=(),
        notice_ids=(),
        source_urls=("https://dspmuranchi.ac.in/",),
        kind="base",
    )


def _runner(tmp_path, **overrides) -> ShadowRunner:
    store, db = build_store(tmp_path)
    seed_page(store)
    values = dict(
        sample_rate=1.0,
        generate=False,
        log_queries=False,
        deadline_ms=2000,
        max_steps=4,
        max_chars=1000,
        max_workers=1,
        pii_guard=False,
    )
    values.update(overrides)
    return ShadowRunner(db, tmp_path / "shadow.jsonl", **values)


def test_run_logs_plan_execution_evidence_and_delta(tmp_path):
    runner = _runner(tmp_path)
    record = runner.run("hostel", _static())

    assert record["source"] == "answer_seam"
    assert record["query"] is None
    assert len(record["query_hash"]) == 16
    assert record["query_chars"] == len("hostel")
    assert record["shadow"]["plan"]["intent"] == "page_topic"
    assert record["shadow"]["plan"]["steps"][0]["tool"] == "site.search"
    assert "args" not in record["shadow"]["plan"]["steps"][0]
    assert record["shadow"]["execution"]["status"] == "ok"
    assert record["shadow"]["evidence"]["query"] is None
    assert record["shadow"]["evidence"]["items"]
    assert record["shadow"]["answer"] is None
    assert "cms.hostel" in record["delta"]["new_ids"]
    assert record["delta"]["static_count"] == 1

    lines = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["query_hash"] == record["query_hash"]


def test_log_queries_true_keeps_the_question_and_args(tmp_path):
    runner = _runner(tmp_path, log_queries=True)
    record = runner.run("hostel", _static())

    assert record["query"] == "hostel"
    assert record["shadow"]["evidence"]["query"] == "hostel"
    assert record["shadow"]["plan"]["steps"][0]["args"]["query"] == "hostel"


def test_submit_and_flush_writes_a_line(tmp_path):
    runner = _runner(tmp_path)

    assert runner.submit("hostel", _static()) is True
    runner.flush()
    runner.close()

    lines = (tmp_path / "shadow.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["shadow"]["plan"]["intent"] == "page_topic"


def test_sample_rate_zero_skips(tmp_path):
    runner = _runner(tmp_path, sample_rate=0.0)

    assert runner.submit("hostel", _static()) is False
    runner.close()
    assert not (tmp_path / "shadow.jsonl").exists()


def test_generate_option_uses_the_verifier(tmp_path):
    runner = _runner(
        tmp_path,
        generate=True,
        llm=lambda messages: (
            "The BA History annual fee is ₹3,410 (GEN)."
        ),
    )
    record = runner.run("What is the BA History fee?", _static())

    answer = record["shadow"]["answer"]
    assert answer is not None
    assert answer["status"] == "verified"
    assert "3,410" in answer["answer"]
    assert answer["citations"]


def test_shadow_enabled_requires_master_and_mode(override_settings):
    override_settings(NGA_ENABLED=True, NGA_MODE="shadow")
    assert shadow_enabled() is True
    override_settings(NGA_ENABLED=False, NGA_MODE="shadow")
    assert shadow_enabled() is False
    override_settings(NGA_ENABLED=True, NGA_MODE="off")
    assert shadow_enabled() is False


def test_submit_shadow_is_a_noop_when_disabled(override_settings):
    override_settings(NGA_ENABLED=False, NGA_MODE="off")
    assert submit_shadow("hostel", _static()) is False


def test_rag_seam_submits_but_returns_the_same_static_bundle(
    monkeypatch, override_settings
):
    from app.rag import _static_bundle_for_query, context_bundle_for_query

    override_settings(NGA_ENABLED=True, NGA_MODE="shadow")
    expected = _static_bundle_for_query("hostel")
    captured = []

    def fake_submit(query, bundle):
        captured.append((query, bundle))
        return True

    monkeypatch.setattr("app.agent.shadow.submit_shadow", fake_submit)
    actual = context_bundle_for_query("hostel")

    assert actual == expected
    assert captured == [("hostel", expected)]


def test_submit_shadow_writes_through_the_configured_runner(
    tmp_path, override_settings
):
    log_path = tmp_path / "configured_shadow.jsonl"
    override_settings(
        NGA_ENABLED=True,
        NGA_MODE="shadow",
        NGA_SHADOW_LOG_PATH=str(log_path),
        NGA_SHADOW_SAMPLE_RATE=1.0,
        NGA_SHADOW_MAX_WORKERS=1,
        NGA_SHADOW_MAX_STEPS=4,
        NGA_SHADOW_GENERATE=False,
        NGA_SHADOW_LOG_QUERIES=False,
    )
    reset_shadow_runner()
    try:
        assert submit_shadow("hostel", _static()) is True
        from app.agent.shadow import get_shadow_runner

        get_shadow_runner().flush()
        lines = log_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["source"] == "answer_seam"
    finally:
        reset_shadow_runner()
