"""Dashboard Acceptance page: the frozen runner's latest report."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from app.config import settings
from app.dashboard.acceptance import load_report, summarize
from app.dashboard.pages import api_call


def _readiness() -> None:
    """Render the same readiness facts the API exposes."""
    health = api_call("GET", "/health/nga") or {}
    if not isinstance(health, dict) or not health:
        st.caption("Readiness unavailable — the backend did not answer.")
        return
    mode = health.get("mode") or {}
    serve_docs = health.get("documents") or {}
    index_docs = (health.get("index") or {}).get("documents") or {}
    status = health.get("status") or "unknown"
    columns = st.columns(4)
    with columns[0]:
        st.metric("Readiness", status.title())
    with columns[1]:
        st.metric("Mode", str(mode.get("nga_mode") or "off"))
    with columns[2]:
        st.metric(
            "Serving pending",
            int(serve_docs.get("pending") or 0),
            help="Documents still pending in the serving database.",
        )
    with columns[3]:
        st.metric(
            "Index pending",
            int(index_docs.get("pending") or 0),
            help="Documents still pending in the staged index database.",
        )
    st.caption(
        " | ".join(
            [
                f"serve: `{health.get('serve_db', '?')}`",
                f"index: `{health.get('index_db', '?')}`",
                f"split: {health.get('db_paths_split')}",
                f"chunks: {health.get('chunks')}",
                f"embeddings: {health.get('embeddings')}",
            ]
        )
    )
    warnings = health.get("warnings") or []
    if warnings:
        st.warning("Readiness warnings: " + ", ".join(map(str, warnings)))


def render() -> None:
    st.title("✅ Acceptance")
    st.caption(
        "Frozen behavioural acceptance set. The report is written by "
        "`scripts/run_acceptance.py`; this page only reads it."
    )

    report_path = Path(settings.NGA_ACCEPTANCE_REPORT_PATH)
    report = load_report(report_path)
    if report is None:
        st.warning(f"No acceptance report found at `{report_path}`.")
        st.markdown("Generate a baseline on a serving snapshot:")
        st.code(
            ".venv/bin/python scripts/run_acceptance.py --llm live "
            "--no-offline --runs 3 "
            f"--report {settings.NGA_ACCEPTANCE_REPORT_PATH}",
            language="bash",
        )
        st.markdown("---")
        st.subheader("Live readiness")
        _readiness()
        return

    summary = summarize(report)
    if summary["report_ok"]:
        st.success(
            f"All {summary['cases']} tuned cases passed across "
            f"{summary['runs']} run(s)."
        )
    else:
        st.error(
            f"{summary['cases_failed']} case(s) failed. "
            "Open the failing cases below before shipping."
        )

    first = st.columns(5)
    with first[0]:
        st.metric(
            "Cases",
            f"{summary['cases_passed']}/{summary['cases']}",
            help="A case passes only when every variant, channel and repeat passes.",
        )
    with first[1]:
        st.metric(
            "Turns",
            f"{summary['turns_passed']}/{summary['turns']}",
            help="One utterance/channel/repeat observation.",
        )
    with first[2]:
        st.metric(
            "p95 latency",
            f"{summary['p95_ms']:.0f} ms",
            help="95th percentile wall time across every observed turn.",
        )
    with first[3]:
        st.metric(
            "False refusals",
            summary["false_refusals"],
            help="Expected an answer, received a refusal, clarification or error.",
        )
    with first[4]:
        st.metric(
            "Hand-offs",
            summary["handoffs"],
            help="Official URL/contact hand-off instead of a quoted answer.",
        )

    second = st.columns(4)
    with second[0]:
        st.metric(
            "Fact turns",
            f"{summary['fact_turns'] - summary['fact_failures']}/"
            f"{summary['fact_turns']}",
            help="Turns with at least one required critical fact.",
        )
    with second[1]:
        st.metric(
            "Negative-cache flags",
            summary["negative_cache_flags"],
            help="Answered turns that still had a negative cache entry.",
        )
    with second[2]:
        st.metric("Model mode", str(summary["llm_mode"] or "none"))
    with second[3]:
        st.metric("Runs", summary["runs"] or 0)

    third = st.columns(5)
    with third[0]:
        st.metric(
            "Cache hits",
            f"{summary['cache_hits_passed']}/{summary['cache_hits']}",
            help="Cache-hit turns that passed all assertions.",
        )
    with third[1]:
        st.metric(
            "Follow-ups",
            f"{summary['followup_resolved']}/{summary['followup_turns']}",
            help="Follow-up turns that resolved and kept the previous topic.",
        )
    with third[2]:
        st.metric(
            "Fallbacks",
            summary["fallbacks"],
            help="Turns that returned no agentic result and used a fallback.",
        )
    with third[3]:
        st.metric(
            "Held-out",
            (
                f"Run ({summary['heldout_cases']})"
                if summary["heldout_cases"] else "Not run"
            ),
            help="Held-out cases are release-only and must not be used while tuning.",
        )
    with third[4]:
        st.metric(
            "Adversarial",
            (
                f"Run ({summary['adversarial_cases']})"
                if summary["adversarial_cases"] else "Not run"
            ),
            help="Injection, PII, stale-date and hallucination cases.",
        )

    fourth = st.columns(4)
    with fourth[0]:
        st.metric(
            "Inadequate evidence",
            f"{summary['inadequate_turns']}/{summary['retrieval_turns']}",
            help="Turns whose initial retrieval bundle failed the adequacy gate.",
        )
    with fourth[1]:
        st.metric(
            "Inadequate rate",
            f"{summary['inadequate_rate']:.0%}",
            help="Inadequate bundles divided by turns where adequacy was assessed.",
        )
    with fourth[2]:
        st.metric(
            "Builder answers",
            summary["builder_turns"],
            help="Turns answered by a deterministic critical-fact builder.",
        )
    with fourth[3]:
        st.metric(
            "Builder declines",
            summary["builder_decline_turns"],
            help="Turns where an applicable deterministic builder declined; "
            "see the reason table below.",
        )

    state = report.get("data_state") or {}
    st.caption(
        " | ".join(
            [
                f"generated: {summary['generated_at'] or '?'}",
                f"source: `{summary['source_db'] or '?'}`",
                f"snapshot: {summary['snapshot']}",
                f"graph: `{str(state.get('graph_hash') or '?')[:12]}`",
                f"chunks: {state.get('chunk_count', '?')}",
                f"cases: `{str(state.get('case_digest') or '?')[:12]}`",
            ]
        )
    )
    st.markdown("---")

    st.subheader("Hand-off reasons")
    reasons = summary["handoff_reasons"]
    if reasons:
        st.table(
            [
                {"Reason": reason, "Turns": count}
                for reason, count in reasons.items()
            ]
        )
    else:
        st.caption("No hand-offs in this report.")
    st.markdown("---")

    st.subheader("Blocked reasons")
    blocked = summary["blocked_reasons"]
    if blocked:
        st.table(
            [
                {"Reason": reason, "Turns": count}
                for reason, count in blocked.items()
            ]
        )
    else:
        st.caption("No refusals, hand-offs or errors in this report.")
    st.markdown("---")

    st.subheader("Deterministic builders")
    builders = summary["builder_counts"]
    if builders:
        st.table(
            [
                {"Builder": builder, "Turns": count}
                for builder, count in builders.items()
            ]
        )
    else:
        st.caption("No deterministic builder produced an answer in this report.")
    st.markdown("---")

    st.subheader("Builder declines")
    declines = summary["builder_decline_reasons"]
    if declines:
        st.table(
            [
                {"Builder:reason": reason, "Turns": count}
                for reason, count in declines.items()
            ]
        )
    else:
        st.caption("No deterministic builder declined in this report.")
    st.markdown("---")

    st.subheader("Failing cases")
    if summary["failed_cases"]:
        for case in summary["failed_cases"]:
            with st.expander(
                f"{case['case_id']} — {case['group'] or 'uncategorised'}"
            ):
                for failure in case["failures"] or ["(no detail)"]:
                    st.write(f"- {failure}")
    else:
        st.caption("None in this report.")
    st.markdown("---")

    st.subheader("Live readiness")
    _readiness()
    st.markdown("---")
    st.download_button(
        "Download report JSON",
        data=json.dumps(report, indent=2, ensure_ascii=False),
        file_name="acceptance_report.json",
        mime="application/json",
    )


__all__ = ["render"]
