#!/usr/bin/env python
"""
Gate 4 — prove the offer-letter document upload against the real admission API.

    python scripts/verify_offer_document_upload.py
    python scripts/verify_offer_document_upload.py --describe-only
    python scripts/verify_offer_document_upload.py --keep --json

WHAT THIS PROVES, AND WHY IT IS A SEPARATE SCRIPT
-------------------------------------------------
``scripts/verify_salesforce_api.py`` is read-only by default and deletes
everything it creates. This gate cannot honour either promise: the upload chain
is three writes, and the ContentVersion it creates **cannot be deleted through
this API** — there is no delete route for a document. Mixing that into the other
harness would quietly break its stated contract, so it lives here, states its
residue, and leaves ``scripts/verify_salesforce_api.py`` alone.

The chain under test is the one the app actually runs:

    1. resolve    GET  /admissions/{userId}          -> Application_No__c
    2. list       GET  /admissions/{app}/documents   -> duplicate pre-check
    3. init       POST /admissions/{app}/documents/uploads
    4. chunk      POST .../uploads/{id}/chunks       (1-based, raw bytes)
    5. complete   POST .../uploads/{id}/complete     -> document_id

Steps 1 and 2-5 run through ``app.crm.documents`` itself, with a real
``CrmClient`` pointed at the live API and the database accessors stubbed in
memory — so this exercises the real resolver, the real path allowlist, the real
chunker and the real error mapping, without needing Postgres.

It also answers the one question the documentation could not: whether
``Document_Type__c`` / ``Source__c`` are restricted picklists. ``--describe``
reads the field metadata straight from Salesforce (org credentials are read from
the API's own .env) and prints the vocabulary, so ``CRM_OFFER_DOCUMENT_TYPE``
can be set from evidence rather than guessed.

RESIDUE
    Deleted:  the test user (DELETE /users/{userId}).
    Kept:     one ContentVersion per completed upload, linked to that user via
              Application__c. The API has no route to delete it, and deleting the
              user may orphan it. The script prints every id it created and how to
              remove them by hand in Salesforce.

EXIT CODES
    0  every clause passed (warnings allowed)
    1  the API was unreachable / transport failure
    2  at least one clause failed
"""

from __future__ import annotations

import argparse
import asyncio
import json as jsonlib
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import httpx
    import requests
except ImportError:  # pragma: no cover
    print("FATAL: run this with the repo venv (requests + httpx are required).", file=sys.stderr)
    raise SystemExit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Windows consoles default to cp1252 and choke on the glyphs below.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):  # pragma: no cover
        pass


DEFAULT_BASE_URL = "http://127.0.0.1:8098"
DEFAULT_API_ENV = Path(r"D:\project\salesforce\salesforce-admission-api\.env")
TEST_PREFIX = "ZZ-VERIFY"
SALESFORCE_API_VERSION = "v62.0"

PASS, FAIL, INFO, WARN = "PASS", "FAIL", "INFO", "WARN"


# ── result plumbing (same shape as verify_salesforce_api.py) ─────────────────

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
    # (document_id, application_no) — the pairing matters for the residue report,
    # since a run can leave documents on more than one student's record.
    created_document_ids: list[tuple[str, str]] = field(default_factory=list)
    residue_notes: list[str] = field(default_factory=list)

    def add(self, probe: str, status: str, detail: str, risk: str = "") -> None:
        self.results.append(Result(probe, status, detail, risk))
        if not ARGS.json:
            colour = {
                PASS: "\033[32m", FAIL: "\033[31m", WARN: "\033[33m", INFO: "\033[36m"
            }[status] if sys.stdout.isatty() else ""
            reset = "\033[0m" if sys.stdout.isatty() else ""
            risk_tag = f"  [{risk}]" if risk else ""
            print(f"  {colour}{status:<4}{reset}  {probe:<28}  {detail}{risk_tag}")

    @property
    def failed(self) -> list[Result]:
        return [r for r in self.results if r.status == FAIL]


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def headers() -> dict[str, str]:
    h = {"Accept": "application/json"}
    if ARGS.api_key:
        h["X-API-Key"] = ARGS.api_key
    return h


def call(method: str, path: str, *, timeout: float | None = None, **kw) -> requests.Response:
    return requests.request(
        method, ARGS.base_url.rstrip("/") + path,
        headers=headers(), timeout=timeout or ARGS.timeout, **kw
    )


def body_of(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text[:300]


# ── Salesforce metadata (read-only) ──────────────────────────────────────────

def read_api_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def salesforce_token(env: dict[str, str]) -> tuple[str, str]:
    """(instance_url, access_token) via client_credentials, or ("", "")."""
    domain = env.get("SALESFORCE_DOMAIN", "").rstrip("/")
    client_id = env.get("SALESFORCE_CLIENT_ID", "")
    secret = env.get("SALESFORCE_CLIENT_SECRET", "")
    if not (domain and client_id and secret):
        return "", ""
    try:
        resp = requests.post(
            f"{domain}/services/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": secret,
            },
            timeout=ARGS.timeout,
        )
        if resp.status_code != 200:
            return "", ""
        payload = resp.json()
        return payload.get("instance_url", ""), payload.get("access_token", "")
    except Exception:
        return "", ""


def describe_field(instance: str, token: str, field_name: str) -> dict | None:
    url = (
        f"{instance}/services/data/{SALESFORCE_API_VERSION}"
        f"/sobjects/ContentVersion/describe"
    )
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=ARGS.timeout)
        if resp.status_code != 200:
            return None
        for field_meta in resp.json().get("fields", []):
            if field_meta.get("name") == field_name:
                return field_meta
    except Exception:
        return None
    return None


def probe_picklists(run: Run) -> None:
    """
    The unknown the docs could not settle: is Document_Type__c / Source__c a
    restricted picklist? A value outside a restricted picklist fails as a 500
    (R16), which is a configuration problem, not a transient one.
    """
    env = read_api_env(Path(ARGS.api_env))
    instance, token = salesforce_token(env)
    if not (instance and token):
        run.add(
            "describe", WARN,
            f"could not read Salesforce metadata ({ARGS.api_env}) — "
            f"the picklist vocabulary stays unverified",
            risk="R16",
        )
        return

    for field_name, setting in (
        ("Document_Type__c", "CRM_OFFER_DOCUMENT_TYPE"),
        ("Source__c", "CRM_OFFER_DOCUMENT_SOURCE"),
    ):
        meta = describe_field(instance, token, field_name)
        if meta is None:
            run.add("describe", WARN, f"{field_name}: not found on ContentVersion")
            continue

        values = [v.get("value") for v in (meta.get("picklistValues") or [])]
        restricted = bool(meta.get("restrictedPicklist"))
        # Read the app's own default rather than restating it here — a check that
        # hard-codes what it is checking drifts the moment the setting changes.
        from app.config import settings

        ours = getattr(settings, setting)

        if not values:
            run.add(
                "describe", PASS,
                f"{field_name} is free text (no picklist) — {ours!r} will be accepted",
            )
        elif restricted and ours not in values:
            run.add(
                "describe", FAIL,
                f"{field_name} is a RESTRICTED picklist and {ours!r} is not in it. "
                f"Allowed: {values}. Set {setting} in .env.",
                risk="R16",
            )
        elif restricted:
            run.add("describe", PASS, f"{field_name} restricted, {ours!r} is allowed")
        else:
            run.add(
                "describe", PASS,
                f"{field_name} unrestricted; org values: {values}",
            )


# ── the chain ────────────────────────────────────────────────────────────────

class RecordingTransport(httpx.AsyncBaseTransport):
    """Records every request the app's own client makes, then forwards it."""

    def __init__(self, calls: list[tuple[str, str]]) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.calls = calls

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        return await self.inner.handle_async_request(request)


@dataclass
class Recording:
    """The shared request log, plus a handle on the stubbed lead's cached number."""

    calls: list[tuple[str, str]] = field(default_factory=list)
    set_application_no: Any = None

    def paths(self, suffix: str = "") -> list[str]:
        return [p for _, p in self.calls if p.endswith(suffix)]


def install_app_modules(run: Run, user_id: str) -> RecordingTransport:
    """
    Point ``app.crm.documents`` at the live API with in-memory database stubs.

    The database accessors are functions on the model modules, and documents.py
    imports them inside each call — so patching the module attributes is enough,
    and no Postgres is involved.
    """
    from app.config import settings

    object.__setattr__(settings, "CRM_ENABLED", True)
    object.__setattr__(settings, "CRM_OFFER_UPLOAD_ENABLED", True)
    object.__setattr__(settings, "CRM_BASE_URL", ARGS.base_url)
    object.__setattr__(settings, "CRM_UPLOAD_TIMEOUT_S", max(30.0, ARGS.timeout))

    from app.crm import documents as docs
    from app.crm.client import CrmClient
    from app.leads import models as leads_models
    from app.offers import models as offers_models

    # One client per event loop, exactly as app/crm/client.py:get_client() does
    # and for the same reason: an httpx.AsyncClient's connection pool is bound to
    # the loop that created it, and the scheduled upload runs on a different loop
    # from the direct call below. A single shared client would fail the second
    # time with an opaque closed-loop error — which is what this harness hit, and
    # what the app's loop-aware get_client() exists to prevent.
    recording = Recording()
    recording.set_application_no = lambda value: state.update({"application_no": value})
    clients: dict[Any, CrmClient] = {}

    def client_for_loop() -> CrmClient:
        loop = asyncio.get_running_loop()
        if loop not in clients:
            clients[loop] = CrmClient(
                ARGS.base_url,
                api_key=ARGS.api_key,
                connect_timeout=settings.CRM_TIMEOUT_CONNECT_S,
                read_timeout=max(30.0, ARGS.timeout),
                max_retries=settings.CRM_MAX_RETRIES,
                transport=RecordingTransport(recording.calls),
            )
        return clients[loop]

    state = {"application_no": "", "uploaded": {}}

    async def get_lead_crm_user_id(lead_id):
        return user_id

    async def get_lead_crm_application_no(lead_id):
        return state["application_no"]

    async def set_lead_crm_application_no(lead_id, application_no):
        state["application_no"] = application_no or ""
        return True

    async def get_offer_crm_upload(offer_id):
        return state["uploaded"].get(
            offer_id, {"document_id": "", "status": "", "uploaded_at": None}
        )

    async def set_offer_crm_upload(offer_id, *, document_id="", status=""):
        current = state["uploaded"].setdefault(
            offer_id, {"document_id": "", "status": "", "uploaded_at": None}
        )
        if document_id:
            current["document_id"] = document_id
        if status:
            current["status"] = status
        return True

    docs.get_client = client_for_loop
    leads_models.get_lead_crm_user_id = get_lead_crm_user_id
    leads_models.get_lead_crm_application_no = get_lead_crm_application_no
    leads_models.set_lead_crm_application_no = set_lead_crm_application_no
    offers_models.get_offer_crm_upload = get_offer_crm_upload
    offers_models.set_offer_crm_upload = set_offer_crm_upload

    run.add("setup", INFO, "app.crm.documents wired to the live API with stubbed DB")
    return recording


def build_test_pdf(offer_id: str, run: Run) -> Path:
    """Build a real PDF with the app's own generator — real bytes, real size."""
    from app.offers.pdf import build_offer_pdf

    out_dir = REPO_ROOT / "data" / "gate4"
    offer = {
        "id": offer_id,
        "program": "Gate 4 Verification Programme",
        "offer_date": "2026-09-12",
        "valid_until": "2026-12-12",
        "terms": "",
    }
    lead = {"name": f"{TEST_PREFIX} Student", "program_interest": "Gate 4 Verification"}
    course = {"name": "Gate 4 Verification Programme", "duration": "2 years", "fees": "N/A", "intake": "Fall"}
    path = build_offer_pdf(lead, course, offer, out_dir)
    run.add("pdf", INFO, f"built {path.name} ({path.stat().st_size} bytes)")
    return path


def create_test_user(run: Run, runid: str, *, suffix: str = "") -> str:
    # The phone number must differ per student: lookup-or-create matches on
    # `Email__c = X OR Phone__c = Y`, so reusing one returns the *first* record
    # whatever email is sent — which silently turns "a second student" into the
    # same student. Kept inside the NANP fictional range 555-0100..0199.
    phone = f"+1415555{100 + (int(runid[:4], 16) + len(suffix) * 7) % 100:04d}"
    resp = call("POST", "/users/lookup-or-create", json={
        "name": f"{TEST_PREFIX}-{runid}{suffix}",
        "email": f"verify+{runid}{suffix}@example.com",
        "phoneNumber": phone,
        "conversationId": f"gate4-{runid}{suffix}",
        "course": "Gate 4 Verification",
    })
    if resp.status_code != 200:
        run.add("create-user", FAIL, f"{resp.status_code}: {body_of(resp)}")
        return ""
    user_id = str((body_of(resp) or {}).get("userId") or "")
    if not user_id:
        run.add("create-user", FAIL, f"no userId in {body_of(resp)!r}")
        return ""
    run.created_user_ids.append(user_id)
    run.add("create-user", PASS, f"userId {user_id}")
    return user_id


def main() -> int:
    run = Run()
    runid = uuid.uuid4().hex[:8]

    print(f"\nGate 4 — offer-letter document upload  (run {runid})")
    print(f"base url: {ARGS.base_url}\n")

    # ── preflight ────────────────────────────────────────────────────────
    try:
        health = call("GET", "/health")
    except Exception as exc:
        print(f"FATAL: cannot reach {ARGS.base_url} — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if health.status_code != 200:
        print(f"FATAL: /health returned {health.status_code}", file=sys.stderr)
        return 1
    run.add("health", PASS, f"{body_of(health)}")

    probe_picklists(run)

    if ARGS.describe_only:
        _summary(run)
        return 2 if run.failed else 0

    # ── create + resolve ─────────────────────────────────────────────────
    user_id = create_test_user(run, runid)
    if not user_id:
        _summary(run)
        return 2

    resp = call("GET", f"/admissions/{user_id}")
    record = body_of(resp)
    application_no = ""
    if resp.status_code == 200 and isinstance(record, dict):
        application_no = str(record.get("Application_No__c") or "")
    if application_no:
        run.add("resolve", PASS, f"userId -> application_no {application_no}")
    else:
        run.add(
            "resolve", FAIL,
            f"{resp.status_code}: no Application_No__c for {user_id} — the document "
            f"routes cannot address this record",
        )
        _summary(run)
        return 2

    # The decision that forced the resolver, re-proved on every run.
    resp = call("GET", f"/admissions/{user_id}/documents")
    if resp.status_code in (400, 404):
        run.add(
            "resolve/userId-is-not-app-no", PASS,
            f"documents route rejects the userId ({resp.status_code}) — the "
            f"resolver is required, not decorative",
        )
    else:
        run.add(
            "resolve/userId-is-not-app-no", WARN,
            f"documents route answered {resp.status_code} for the userId — the "
            f"API may now accept record Ids; re-read the resolver",
        )

    # ── the real chain, through app.crm.documents ────────────────────────
    recording = install_app_modules(run, user_id)
    from app.crm import documents as docs

    offer_id = f"{runid}-0000-0000-0000-000000000000"
    pdf_path = build_test_pdf(offer_id, run)

    document_id = asyncio.run(docs.upload_offer_document(
        lead_id="gate4", offer_id=offer_id, pdf_path=str(pdf_path)
    ))
    if document_id:
        run.created_document_ids.append((document_id, application_no))
        run.add("upload", PASS, f"document_id {document_id}")
    else:
        run.add("upload", FAIL, "upload_offer_document returned no document id — see the log above")

    # ── duplicate proof: the money test ──────────────────────────────────
    before = len(recording.paths("/uploads"))
    second = asyncio.run(docs.upload_offer_document(
        lead_id="gate4", offer_id=offer_id, pdf_path=str(pdf_path)
    ))
    after = len(recording.paths("/uploads"))

    if second == document_id and after == before:
        run.add(
            "duplicate/re-run", PASS,
            "a second run made no upload request and returned the same document",
        )
    else:
        run.add(
            "duplicate/re-run", FAIL,
            f"second run returned {second!r} and made {after - before} extra upload "
            f"request(s) — a second ContentVersion may exist",
        )

    # ── fire-and-forget: the path production actually takes ──────────────
    # The offer service calls schedule_offer_upload, not upload_offer_document.
    # Opt-in because it necessarily leaves a second document behind.
    if ARGS.include_schedule:
        from app.config import settings

        # A *second* student, not a second offer for the first: a repeat offer for
        # the same application is supposed to be suppressed by the duplicate
        # check, so reusing this one would prove the opposite of what is intended.
        sched_user = create_test_user(run, runid, suffix="s")
        sched_app = ""
        if sched_user:
            resp = call("GET", f"/admissions/{sched_user}")
            if resp.status_code == 200 and isinstance(body_of(resp), dict):
                sched_app = str(body_of(resp).get("Application_No__c") or "")
        if not sched_app:
            run.add("schedule", FAIL, "could not prepare a second test student")
        else:
            recording.set_application_no(sched_app)

        second_offer = f"{runid}-1111-0000-0000-000000000000"
        second_pdf = build_test_pdf(second_offer, run)
        before_task = len(recording.paths("/uploads"))
        before_complete = len(recording.paths("/complete"))

        async def _drive():
            # Called from inside a coroutine, which is how production reaches it
            # (the offer service always runs in the app's event loop); from sync
            # code schedule_offer_upload declines by design, having no loop to
            # create the task on.
            docs.schedule_offer_upload(
                lead_id="gate4", offer_id=second_offer, pdf_path=str(second_pdf)
            )
            for _ in range(200):
                if len(recording.paths("/uploads")) > before_task:
                    await asyncio.sleep(1.0)  # let the rest of the chain finish
                    break
                await asyncio.sleep(0.1)

        asyncio.run(_drive())
        ran = len(recording.paths("/uploads")) > before_task
        finished = len(recording.paths("/complete")) > before_complete
        if ran and finished:
            run.add("schedule", PASS, "the background task ran the whole chain unattended")
        elif ran:
            run.add("schedule", WARN, "the task started but did not complete")
        else:
            run.add("schedule", FAIL, "the scheduled upload never left the process")

        if sched_app:
            resp = call("GET", f"/admissions/{sched_app}/documents")
            if resp.status_code == 200 and isinstance(body_of(resp), list):
                for doc in body_of(resp):
                    if isinstance(doc, dict) and str(doc.get("file_name") or "").startswith("Offer_Letter_"):
                        run.created_document_ids.append(
                            (str(doc.get("document_id")), sched_app)
                        )

    # ── what the CRM actually holds ──────────────────────────────────────
    resp = call("GET", f"/admissions/{application_no}/documents")
    listed = body_of(resp)
    ours = []
    if resp.status_code == 200 and isinstance(listed, list):
        from app.config import settings

        wanted = settings.CRM_OFFER_DOCUMENT_TYPE
        ours = [d for d in listed if isinstance(d, dict) and d.get("document_type") == wanted]
        run.add("crm/list", INFO, f"{len(listed)} document(s) on {application_no}, {len(ours)} offer letter(s)")
        if len(ours) > 1:
            run.add("crm/list", FAIL, f"{len(ours)} offer letters — expected at most one", risk="dup")
        else:
            run.add("crm/list", PASS, "no duplicate offer letter")
    else:
        run.add("crm/list", WARN, f"{resp.status_code}: {listed}")

    # ── residue ──────────────────────────────────────────────────────────
    if not ARGS.keep and run.created_user_ids:
        for uid in run.created_user_ids:
            resp = call("DELETE", f"/users/{uid}")
            if resp.status_code < 400:
                run.deleted_user_ids.append(uid)
                run.add("cleanup/user", PASS, f"deleted {uid}")
            else:
                run.add("cleanup/user", WARN, f"{resp.status_code} deleting {uid}")

    for doc_id, doc_app in run.created_document_ids:
        run.residue_notes.append(
            f"ContentVersion {doc_id} remains on application {doc_app} — this "
            f"API has no delete route for documents. Remove it in Salesforce "
            f"(instance {SALESFORCE_API_VERSION} REST: DELETE "
            f"/services/data/{SALESFORCE_API_VERSION}/sobjects/ContentVersion/{doc_id}) "
            f"or leave it as evidence on a {TEST_PREFIX} record."
        )

    _summary(run)
    return 2 if run.failed else 0


def _summary(run: Run) -> None:
    if ARGS.json:
        print(jsonlib.dumps({
            "results": [r.__dict__ for r in run.results],
            "created_user_ids": run.created_user_ids,
            "deleted_user_ids": run.deleted_user_ids,
            "created_document_ids": run.created_document_ids,
            "residue": run.residue_notes,
        }, indent=2))
        return

    counts: dict[str, int] = {}
    for r in run.results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(
        f"\n  {counts.get(PASS, 0)} passed · {counts.get(WARN, 0)} warned · "
        f"{counts.get(FAIL, 0)} failed · {counts.get(INFO, 0)} info"
    )

    if run.residue_notes:
        print("\n  RESIDUE (cannot be removed through this API):")
        for note in run.residue_notes:
            print(f"    - {note}")

    if run.failed:
        print("\n  FAILED:")
        for r in run.failed:
            print(f"    - {r.probe}: {r.detail}")
    print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Gate 4 — verify the offer-letter document upload end to end.",
    )
    parser.add_argument("--base-url", default=os.environ.get("CRM_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default=os.environ.get("CRM_API_KEY", ""))
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="per-request timeout; the default is deliberately longer than "
                             "the app's 5s read timeout, which /complete does not fit in")
    parser.add_argument("--api-env", default=str(DEFAULT_API_ENV),
                        help="the admission API's .env, used only to read Salesforce field "
                             "metadata for the picklist check")
    parser.add_argument("--describe-only", action="store_true",
                        help="check the Salesforce picklists and stop — creates nothing")
    parser.add_argument("--keep", action="store_true",
                        help="do not delete the test user (the document stays either way)")
    parser.add_argument("--include-schedule", action="store_true",
                        help="also exercise schedule_offer_upload — the fire-and-forget path "
                             "production uses. Leaves a second ContentVersion behind.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


ARGS = parse_args()

if __name__ == "__main__":
    raise SystemExit(main())
