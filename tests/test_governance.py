"""Pure checks behind the NGA release-governance gate."""

from __future__ import annotations

import json

from scripts.nga_governance import (
    catalogue_audit,
    evaluate_blockers,
    legacy_audit,
    load_register,
)


def test_catalogue_audit_counts_rows_without_a_reviewer():
    payload = {
        "programmes": [
            {"id": "p1", "status": "draft", "reviewed_by": None},
            {"id": "p2", "status": "page_derived", "reviewed_by": "Reviewer"},
        ]
    }

    audit = catalogue_audit(payload)

    assert audit["rows"] == 2
    assert audit["by_status"] == {"draft": 1, "page_derived": 1}
    assert audit["missing_reviewer"] == 1
    assert audit["missing_reviewer_ids"] == ["p1"]


def test_legacy_audit_requires_an_explicit_approval_entry():
    rows = [
        {
            "node_id": "legacy.a",
            "title": "A",
            "url": "https://oldsite.dspmuranchi.ac.in/A.aspx",
            "status": "ok",
        },
        {
            "node_id": "legacy.b",
            "title": "B",
            "url": "https://oldsite.dspmuranchi.ac.in/B.aspx",
            "status": "ok",
        },
    ]

    audit = legacy_audit(
        rows,
        allowed_hosts=("dspmuranchi.ac.in",),
        allowlist=["legacy.a"],
    )

    assert audit["count"] == 2
    assert audit["approved"] == 1
    assert audit["host_allowlisted"] == 0
    assert audit["unapproved"] == 1
    assert audit["nodes"][0]["approved"] is True
    assert audit["nodes"][1]["approved"] is False


def test_blockers_clear_only_when_the_decisions_and_content_are_signed():
    register = {"decisions": {"D4": {"status": "signed"}}}
    catalogue = {"rows": 1, "missing_reviewer": 0}
    legacy = {"unapproved": 0}

    blockers = evaluate_blockers(
        register=register,
        catalogue=catalogue,
        legacy=legacy,
        catalogue_enabled=True,
        include_draft=True,
        pg_lists_enabled=True,
    )

    assert blockers == []


def test_blockers_name_each_unsigned_surface():
    blockers = evaluate_blockers(
        register={"decisions": {"D4": {"status": "unsigned"}}},
        catalogue={"rows": 2, "missing_reviewer": 2},
        legacy={"unapproved": 1},
        catalogue_enabled=True,
        include_draft=True,
        pg_lists_enabled=True,
    )

    assert {item["id"] for item in blockers} == {
        "d4_pg_lists_unsigned",
        "catalogue_rows_unreviewed",
        "legacy_nodes_unapproved",
    }


def test_load_register_is_safe_for_a_missing_or_malformed_file(tmp_path):
    broken = tmp_path / "governance.json"
    broken.write_text("{not json", encoding="utf-8")

    assert load_register(tmp_path / "missing.json") == {}
    assert load_register(broken) == {}
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps({"decisions": {}}), encoding="utf-8")
    assert load_register(valid) == {"decisions": {}}
