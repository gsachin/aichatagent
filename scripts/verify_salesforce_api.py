#!/usr/bin/env python
"""
Phase 0.1 — Salesforce Admission API verification harness.

Answers the question asked in `salesforcer_api.txt`: "Could you please verify that the
APIs are working properly?" — and, in the same run, establishes exactly how the upstream
behaviours our integration plan calls R1-R6 actually manifest, so the plan's defensive
work can be scoped against evidence rather than source reading.

The API is treated as a FROZEN external contract (decision D1). Nothing in this script
assumes we may change it.

USAGE
    # Safe default: read-only probes. Writes nothing.
    python scripts/verify_salesforce_api.py

    # Full verification. Creates test records with a reserved test marker and deletes
    # them again in a finally block. Requires explicit opt-in.
    python scripts/verify_salesforce_api.py --write

    # Additionally probe the empty-identifier path (R2/R4). May leave an orphan record
    # in the CRM that this API cannot clean up (there is no GET /users/{id}). Off by
    # default; read the warning it prints before enabling.
    python scripts/verify_salesforce_api.py --write --include-destructive

    python scripts/verify_salesforce_api.py --base-url http://127.0.0.1:8098 --json

EXIT CODES
    0  harness ran to completion
    1  API unreachable / transport failure
    2  --fail-on-defect and at least one defect probe did not reproduce as documented

Test data convention (so anything left behind is identifiable):
    name  ZZ-VERIFY-<runid>            email verify+<runid>@example.com
    phone +1415555<4 digits>           (NANP fictional range, 555-0100..0199)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    print("FATAL: `requests` is not installed. Activate the repo venv first.", file=sys.stderr)
    raise SystemExit(1)

# Windows consoles default to cp1252 and choke on the box-drawing/separator glyphs below.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):  # pragma: no cover
        pass


DEFAULT_BASE_URL = "http://127.0.0.1:8098"
TEST_PREFIX = "ZZ-VERIFY"

# Paths we expect to exist, read from the API source at commit 272434d.
EXPECTED_PATHS = {
    "/",
    "/health",
    "/users/lookup-or-create",
    "/users/{user_id}",
    "/users/{user_id}/status",
    "/admissions",
    "/admissions/{application_id}",
    "/admissions/{application_no}/documents",
    "/admissions/{application_no}/documents/uploads",
    "/admissions/{application_no}/documents/uploads/{upload_id}/chunks",
    "/admissions/{application_no}/documents/uploads/{upload_id}/complete",
    "/admissions/documents/{document_id}",
    "/admissions/documents/{document_id}/content",
    "/admissions/documents/{document_id}/verification",
}


# ── picklist vocabularies ────────────────────────────────────────────────────
#
# Sentiment__c and Offer_Letter_Accepted__c are RESTRICTED PICKLISTS in Salesforce.
# An out-of-vocabulary value fails the PATCH with INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST
# — surfaced by this API as a 500, i.e. indistinguishable from a transient fault unless
# the detail string is parsed. Observed live on the dev org, 2026-09-11 (202 rows):
#     Sentiment__c            NURTURE · HOT · DISQUALIFIED · WARM · AT-RISK
#     Offer_Letter_Accepted__c  UNKNOWN · ACCEPTED · NOT_ACCEPTED
#
# NOTE the trap: Sentiment__c uses "AT-RISK" (hyphen) while the separate
# Lead_Category__c field uses "AT RISK" (space). The app's categorizer emits the
# hyphenated form, so it aligns with Sentiment__c.
CRM_SENTIMENT_VOCAB = {"NURTURE", "HOT", "DISQUALIFIED", "WARM", "AT-RISK"}
CRM_OFFER_ACCEPTED_VOCAB = {"UNKNOWN", "ACCEPTED", "NOT_ACCEPTED"}

# What the app actually has to offer (app/sentiment/categorizer.py:31-35).
APP_LEAD_CATEGORIES = ["Hot", "Warm", "Nurture", "At-Risk", "Disqualified"]
# ...and what a naive integration would send (app/sentiment/scorer.py:95, primary_emotion).
APP_RAW_EMOTIONS = ["excited", "interested", "neutral", "skeptical", "frustrated", "angry", "confused"]

# app/sentiment/categorizer.py → Sentiment__c.  All five are a clean .upper().
SENTIMENT_MAP = {c: c.upper() for c in APP_LEAD_CATEGORIES}


def map_offer_accepted(app_value: str) -> str:
    """app/main.py:1494,1500 emits 'accepted'/'rejected'; the CRM wants the latter spelled
    NOT_ACCEPTED (not 'REJECTED', not 'DECLINED')."""
    return {
        "accepted": "ACCEPTED",
        "rejected": "NOT_ACCEPTED",
    }.get(app_value.strip().lower(), "UNKNOWN")


def probe_vocabulary(run: Run) -> None:
    """Assert every app-produced value has an in-vocabulary CRM target (pure logic)."""
    unmapped = [c for c in APP_LEAD_CATEGORIES if SENTIMENT_MAP[c] not in CRM_SENTIMENT_VOCAB]
    if unmapped:
        run.add("vocab/sentiment", FAIL, f"no CRM target for app categor{'' if len(unmapped)==1 else 'ies'} {unmapped}")
    else:
        run.add(
            "vocab/sentiment", PASS,
            f"all {len(APP_LEAD_CATEGORIES)} categorizer outputs map into Sentiment__c "
            f"via .upper() → {sorted(SENTIMENT_MAP.values())}",
        )

    overlap = set(APP_RAW_EMOTIONS) & CRM_SENTIMENT_VOCAB
    run.add(
        "vocab/sentiment-mismatch", WARN,
        f"primary_emotion ({', '.join(APP_RAW_EMOTIONS)}) shares NO values with Sentiment__c — "
        f"writing primary_emotion directly would 500 on every call. Use the categorizer output.",
        risk="R16",
    )

    accepted = {map_offer_accepted(v) for v in ("accepted", "rejected")}
    if accepted <= CRM_OFFER_ACCEPTED_VOCAB:
        run.add(
            "vocab/offerAccepted", PASS,
            "app 'accepted'/'rejected' → ACCEPTED/NOT_ACCEPTED, both in vocabulary (answers D3)",
        )
    else:
        run.add("vocab/offerAccepted", FAIL, f"unmappable: {sorted(accepted - CRM_OFFER_ACCEPTED_VOCAB)}")


# ── result plumbing ──────────────────────────────────────────────────────────

PASS, FAIL, INFO, WARN = "PASS", "FAIL", "INFO", "WARN"


@dataclass
class Result:
    probe: str
    status: str
    detail: str
    risk: str = ""


@dataclass
class Run:
    results: list[Result] = field(default_factory=list)
    created_user_ids: list[str] = field(default_factory=list)
    deleted_user_ids: list[str] = field(default_factory=list)
    cleanup_failures: list[tuple[str, str]] = field(default_factory=list)

    def add(self, probe: str, status: str, detail: str, risk: str = "") -> None:
        self.results.append(Result(probe, status, detail, risk))
        if not ARGS.json:
            use_colour = sys.stdout.isatty()
            colour = {PASS: "\033[32m", FAIL: "\033[31m", WARN: "\033[33m", INFO: "\033[36m"}[status] if use_colour else ""
            reset = "\033[0m" if use_colour else ""
            risk_tag = f"  [{risk}]" if risk else ""
            print(f"  {colour}{status:<4}{reset}  {probe:<26}  {detail}{risk_tag}")

    @property
    def defects_reproduced(self) -> list[Result]:
        return [r for r in self.results if r.risk]


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if ARGS.api_key:
        h["X-API-Key"] = ARGS.api_key
    return h


def call(method: str, path: str, *, timeout: float | None = None, **kw) -> requests.Response:
    url = ARGS.base_url.rstrip("/") + path
    return requests.request(
        method, url, headers=headers(), timeout=timeout or ARGS.timeout, **kw
    )


def body_of(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:300]


# ── probes: read-only ────────────────────────────────────────────────────────

def probe_health(run: Run) -> None:
    try:
        resp = call("GET", "/health")
    except requests.RequestException as exc:
        raise SystemExit(f"FATAL: API unreachable at {ARGS.base_url} — {exc}")

    if resp.status_code == 200 and body_of(resp).get("status") == "ok":
        run.add("health", PASS, f"200 ok — {body_of(resp).get('service', '?')}")
        run.add(
            "health/sf-reachability", INFO,
            "endpoint does not touch Salesforce; a green health check does NOT mean the org is reachable",
        )
    else:
        run.add("health", FAIL, f"{resp.status_code} {body_of(resp)}")


def probe_openapi(run: Run) -> None:
    resp = call("GET", "/openapi.json")
    if resp.status_code != 200:
        run.add("openapi", WARN, f"{resp.status_code} — cannot check for contract drift")
        return

    paths = set(body_of(resp).get("paths", {}))
    missing = EXPECTED_PATHS - paths
    extra = paths - EXPECTED_PATHS
    if not missing and not extra:
        run.add("openapi/contract", PASS, f"all {len(EXPECTED_PATHS)} expected paths present, no drift")
    else:
        detail = []
        if missing:
            detail.append(f"missing={sorted(missing)}")
        if extra:
            detail.append(f"unexpected={sorted(extra)}")
        run.add("openapi/contract", WARN, "contract drift vs 272434d — " + "; ".join(detail))


def probe_404_path(run: Run) -> None:
    """PATCH to an unknown id must 404 and must not create anything."""
    fake = "a0XVERIFY000000000"
    resp = call("PATCH", f"/users/{fake}/status", json={"sentiment": "probe"})
    if resp.status_code == 404:
        run.add("status/404", PASS, "unknown user id returns 404 (no write)")
    elif resp.status_code == 500:
        run.add(
            "status/404", WARN,
            "unknown user id returns 500, not 404 — the ValueError branch is not reached; "
            "treat every non-2xx as retryable-unknown",
        )
    else:
        run.add("status/404", FAIL, f"unexpected {resp.status_code}: {body_of(resp)}")


def probe_admissions_r1(run: Run) -> None:
    """R1 — /users and /admissions share the Customer object."""
    resp = call("GET", "/admissions")
    if resp.status_code != 200:
        run.add("admissions/R1", WARN, f"{resp.status_code} — cannot assess object sharing")
        return

    records = body_of(resp)
    if not isinstance(records, list):
        run.add("admissions/R1", WARN, f"unexpected payload shape: {type(records).__name__}")
        return

    total = len(records)
    # `Name` is not in the repository's SELECT list, so Application_No__c is the only
    # usable discriminator: lookup-or-create mints "APP-<10 hex>" there, while
    # admissions carry a caller-supplied number.
    user_rows = [r for r in records if str(r.get("Application_No__c") or "").startswith("APP-")]
    testing_flagged = [r for r in records if r.get("Testing_Record__c")]
    run.add("admissions/R1", INFO, f"{total} Customer rows visible via /admissions")

    if user_rows:
        run.add(
            "admissions/R1/users-in-list", WARN,
            f"{len(user_rows)}/{total} row(s) carry an APP-* Application_No__c — created by "
            "lookup-or-create, yet returned by the admissions listing. The two are "
            "indistinguishable to any consumer of this endpoint.",
            risk="R1",
        )
    if testing_flagged and len(testing_flagged) != total:
        run.add(
            "admissions/R13", WARN,
            f"Testing_Record__c set on {len(testing_flagged)}/{total} rows and never filtered",
            risk="R13",
        )


# ── probes: write ────────────────────────────────────────────────────────────

def lookup_or_create(name: str, email: str, phone: str, conv: str, course: str | None = None):
    payload = {
        "name": name,
        "email": email,
        "phoneNumber": phone,
        "conversationId": conv,
    }
    if course is not None:
        payload["course"] = course
    return call("POST", "/users/lookup-or-create", json=payload)


def probe_create_and_hit(run: Run, runid: str) -> str | None:
    """Create path, then the hit path — the two calls the integration depends on most."""
    name = f"{TEST_PREFIX}-{runid}"
    email = f"verify+{runid}@example.com"
    phone = f"+1415555{int(runid[-4:], 16) % 10000:04d}"
    conv = f"conv-verify-{runid}"
    course = "Verify Program"

    resp = lookup_or_create(name, email, phone, conv, course)
    if resp.status_code != 200:
        run.add("lookup/create", FAIL, f"{resp.status_code} {body_of(resp)}")
        return None

    data = body_of(resp)
    user_id = data.get("userId")
    run.created_user_ids.append(user_id)
    run.add("lookup/create", PASS, f"created userId={user_id}")
    PROBE_STATE["user_id"] = user_id
    PROBE_STATE["email"] = email
    PROBE_STATE["phone"] = phone
    PROBE_STATE["name"] = name

    if data.get("course") != course:
        run.add("lookup/create/course", WARN, f"course not echoed: sent {course!r}, got {data.get('course')!r}")
    else:
        run.add("lookup/create/course", PASS, "course persisted on create")
    if data.get("conversationId") != conv:
        run.add("lookup/create/conv", WARN, f"conversationId not echoed: sent {conv!r}, got {data.get('conversationId')!r}")

    # Hit path — same identity, new conversationId.
    conv2 = f"conv-verify-{runid}-b"
    resp2 = lookup_or_create(name, email, phone, conv2, course)
    if resp2.status_code != 200:
        run.add("lookup/hit", FAIL, f"{resp2.status_code} {body_of(resp2)}")
        return user_id

    data2 = body_of(resp2)
    if data2.get("userId") != user_id:
        run.add(
            "lookup/hit/dedupe", FAIL,
            f"second call returned a DIFFERENT userId ({data2.get('userId')} vs {user_id}) — duplicates",
            risk="R5",
        )
        if data2.get("userId"):
            run.created_user_ids.append(data2["userId"])
    else:
        run.add("lookup/hit/dedupe", PASS, "same userId returned — no duplicate on repeat call")

    # R6 — does the hit path move Conversation_ID__c forward?
    returned_conv = data2.get("conversationId")
    if returned_conv == conv2:
        run.add("lookup/hit/conversation", PASS, "Conversation_ID__c updated to the new conversation")
    else:
        run.add(
            "lookup/hit/conversation", WARN,
            f"returned conversationId={returned_conv!r}, sent {conv2!r} — the new conversation is NOT linked",
            risk="R6",
        )
    return user_id


def probe_status(run: Run, user_id: str) -> None:
    """PATCH all three writable fields using IN-VOCABULARY values, then confirm persistence."""
    sentiment = "AT-RISK"                      # a real categorizer output, upper-cased
    accepted = map_offer_accepted("rejected")  # → NOT_ACCEPTED
    payload = {
        "sentiment": sentiment,
        "offerLetterReleased": True,
        "offerLetterAccepted": accepted,
    }

    resp = call("PATCH", f"/users/{user_id}/status", json=payload)
    if resp.status_code == 200:
        run.add(
            "status/patch", PASS,
            f"200 — all three fields accepted (sentiment={sentiment!r}, accepted={accepted!r})",
        )
    else:
        run.add("status/patch", FAIL, f"{resp.status_code} {body_of(resp)}")
        _diagnose_500(run, resp)
        return

    # Verify persistence by reading back through lookup-or-create (no GET /users/{id} exists).
    resp2 = lookup_or_create(
        PROBE_STATE["name"], PROBE_STATE["email"], PROBE_STATE["phone"],
        f"conv-verify-{runid_short()}-readback",
    )
    if resp2.status_code != 200:
        run.add("status/persisted", WARN, f"read-back failed: {resp2.status_code}")
        return

    d = body_of(resp2)
    if d.get("sentiment") == sentiment and d.get("offerLetterReleased") is True:
        run.add(
            "status/persisted", PASS,
            f"read back sentiment={sentiment!r}, offerLetterReleased=True, "
            f"offerLetterAccepted={d.get('offerLetterAccepted')!r}",
        )
    else:
        run.add(
            "status/persisted", WARN,
            f"read-back mismatch: sentiment={d.get('sentiment')!r} "
            f"released={d.get('offerLetterReleased')!r} accepted={d.get('offerLetterAccepted')!r}",
        )

    # Demonstrate why the mapping table is mandatory: an unmapped app value must be rejected.
    raw = APP_RAW_EMOTIONS[0]
    resp3 = call("PATCH", f"/users/{user_id}/status", json={"sentiment": raw})
    if resp3.status_code != 200:
        run.add(
            "status/raw-emotion-rejected", WARN,
            f"raw primary_emotion {raw!r} rejected ({resp3.status_code}) — confirms the "
            "CATEGORIZER output, not primary_emotion, is the field to send",
            risk="R16",
        )
    else:
        run.add("status/raw-emotion-rejected", INFO, f"{raw!r} was accepted — vocabulary is wider than observed")


def _diagnose_500(run: Run, resp: requests.Response) -> None:
    """A 500 here is ambiguous: transient fault, or a permanent validation error."""
    detail = str(body_of(resp))
    if "INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST" in detail:
        run.add(
            "status/500-classification", WARN,
            "500 is a PERMANENT validation error (restricted picklist), not a transient fault — "
            "the outbox must NOT retry these",
            risk="R4",
        )
    else:
        run.add(
            "status/500-classification", WARN,
            "500 with no picklist marker — cannot distinguish transient from permanent from the "
            "status code alone; parse `detail` before queueing a retry",
            risk="R4",
        )


def probe_destructive(run: Run, runid: str) -> None:
    """
    R2 + R4: the empty-identifier path.

    WARNING: if the API creates a record and then fails response validation (R4), the
    record exists but its id is never returned — and there is no GET /users/{id}, so this
    script cannot delete it. Anything left behind carries the ZZ-VERIFY marker.
    """
    run.add("destructive/preflight", WARN, "empty-identifier probe enabled — see module docstring")

    # (a) blank email, valid phone -> is an unrelated record matched? (R2)
    email_blank_phone = f"+1415555{(int(runid[-4:], 16) + 1) % 10000:04d}"
    resp = lookup_or_create(f"{TEST_PREFIX}-{runid}-blankmail", "", email_blank_phone, f"conv-{runid}-c")
    if resp.status_code == 200:
        d = body_of(resp)
        uid = d.get("userId")
        if uid:
            run.created_user_ids.append(uid)
        created = d.get("email") in ("", None) and d.get("phoneNumber") == email_blank_phone
        if created:
            run.add("R2/blank-email", INFO, "blank email created a new record (no spurious match this run)")
        else:
            run.add(
                "R2/blank-email", WARN,
                f"blank email matched an EXISTING record: userId={uid} email={d.get('email')!r} "
                f"phone={d.get('phoneNumber')!r} — an unrelated user can be returned",
                risk="R2",
            )
    elif resp.status_code == 500:
        run.add(
            "R2+R4/blank-email", WARN,
            "500 on a blank-email lookup — the record may have been created before response "
            "validation failed. A record with the ZZ-VERIFY marker may now be orphaned.",
            risk="R4",
        )
        _report_orphan(run, email_blank_phone)
    else:
        run.add("R2/blank-email", FAIL, f"{resp.status_code} {body_of(resp)}")

    # (b) blank email AND blank phone -> the worst case
    resp = lookup_or_create(f"{TEST_PREFIX}-{runid}-blankall", "", "", f"conv-{runid}-d")
    if resp.status_code == 200:
        d = body_of(resp)
        uid = d.get("userId")
        if uid:
            run.created_user_ids.append(uid)
        if uid and (d.get("email") or d.get("phoneNumber")):
            run.add(
                "R2/blank-both", WARN,
                f"a lookup with NO identifiers returned userId={uid} (email={d.get('email')!r}, "
                f"phone={d.get('phoneNumber')!r}) — arbitrary record match",
                risk="R2",
            )
        else:
            run.add("R2/blank-both", INFO, f"returned userId={uid} — inspect manually for orphan rows")
    elif resp.status_code == 500:
        run.add(
            "R2+R4/blank-both", WARN,
            "500 on an all-blank lookup — likely created then failed validation (R4). "
            "Orphan possible; no GET /users/{id} to clean it up.",
            risk="R4",
        )
        _report_orphan(run, "")
    else:
        run.add("R2/blank-both", FAIL, f"{resp.status_code} {body_of(resp)}")


def _report_orphan(run: Run, phone: str) -> None:
    """Try once more; if it now resolves, we can delete it. If not, it stays."""
    if not phone:
        run.add("orphan/cleanup", FAIL, "cannot locate orphan with no identifiers — needs manual CRM cleanup")
        return
    resp = lookup_or_create(f"{TEST_PREFIX}-orphan", "", phone, "conv-orphan-check")
    if resp.status_code == 200:
        uid = body_of(resp).get("userId")
        if uid:
            run.created_user_ids.append(uid)
            run.add("orphan/cleanup", INFO, f"orphan located as userId={uid}; queued for deletion")
            return
    run.add("orphan/cleanup", FAIL, f"orphan not locatable via phone {phone} — needs manual CRM cleanup")


def cleanup(run: Run) -> None:
    if not ARGS.write:
        return
    print()
    if not run.created_user_ids:
        run.add("cleanup", INFO, "nothing created")
        return

    for uid in dict.fromkeys(run.created_user_ids):
        if not uid:
            continue
        try:
            resp = call("DELETE", f"/users/{uid}")
            if resp.status_code in (200, 204):
                run.deleted_user_ids.append(uid)
            else:
                run.cleanup_failures.append((uid, f"{resp.status_code} {body_of(resp)}"))
        except requests.RequestException as exc:
            run.cleanup_failures.append((uid, str(exc)))

    if not run.cleanup_failures:
        run.add("cleanup", PASS, f"deleted all {len(run.deleted_user_ids)} test record(s)")
    else:
        run.add("cleanup", FAIL, f"{len(run.cleanup_failures)} record(s) NOT deleted — see summary")


# ── driver ───────────────────────────────────────────────────────────────────

PROBE_STATE: dict[str, str] = {}
ARGS: argparse.Namespace


def runid_short() -> str:
    return PROBE_STATE.get("runid", "0000")


def main() -> int:
    global ARGS
    ARGS = parse_args()

    runid = f"{int(time.time()) % 100000:05d}"
    PROBE_STATE["runid"] = runid
    run = Run()

    if not ARGS.json:
        mode = "WRITE" if ARGS.write else "READ-ONLY"
        if ARGS.include_destructive:
            mode += " + DESTRUCTIVE"
        print(f"\nSalesforce Admission API verification — {mode}")
        print(f"  base URL  {ARGS.base_url}")
        print(f"  run id    {runid}   (test marker: {TEST_PREFIX}-{runid})\n")

    probe_health(run)
    probe_openapi(run)
    probe_404_path(run)
    probe_admissions_r1(run)
    probe_vocabulary(run)

    if ARGS.write:
        if not ARGS.json:
            print()
        user_id = probe_create_and_hit(run, runid)
        if user_id:
            probe_status(run, user_id)
        if ARGS.include_destructive:
            probe_destructive(run, runid)
    elif not ARGS.json:
        print()
        run.add("write-probes", INFO, "skipped — re-run with --write to exercise create/hit/status")

    cleanup(run)

    if ARGS.json:
        print(json.dumps(
            {
                "base_url": ARGS.base_url,
                "run_id": runid,
                "mode": "write" if ARGS.write else "read-only",
                "results": [r.__dict__ for r in run.results],
                "created_user_ids": run.created_user_ids,
                "deleted_user_ids": run.deleted_user_ids,
                "cleanup_failures": run.cleanup_failures,
            },
            indent=2,
        ))
        return 0

    # ── summary ──
    print("\n" + "─" * 72)
    defects = run.defects_reproduced
    if defects:
        print(f"\nUPSTREAM BEHAVIOURS OBSERVED ({len(defects)}):")
        for d in defects:
            print(f"  [{d.risk}] {d.probe}")
            print(f"        {d.detail}")
    else:
        print("\nNo documented upstream defects reproduced on this run.")

    if run.cleanup_failures:
        print("\nCLEANUP FAILURES — manual CRM action required:")
        for uid, err in run.cleanup_failures:
            print(f"  {uid}: {err}")

    failed = [r for r in run.results if r.status == FAIL]
    print(f"\n{len(run.results)} probes · {len(failed)} FAIL · {len(defects)} defect(s) reproduced\n")
    return 2 if (ARGS.fail_on_defect and defects) else 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Verify the Salesforce Admission API against the integration plan's expectations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--base-url", default=os.environ.get("CRM_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--api-key", default=os.environ.get("CRM_API_KEY", ""),
                   help="sent as X-API-Key; the API does not require one today (R8)")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--write", action="store_true",
                   help="run the create/hit/status probes. Creates test records and deletes them again.")
    p.add_argument("--include-destructive", action="store_true",
                   help="also probe empty-identifier lookups. May leave an orphan record; see docstring.")
    p.add_argument("--fail-on-defect", action="store_true",
                   help="exit 2 if any documented upstream defect reproduces (for CI)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args()
    if args.include_destructive and not args.write:
        p.error("--include-destructive requires --write")
    return args


if __name__ == "__main__":
    sys.exit(main())
