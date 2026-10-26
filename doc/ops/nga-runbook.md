# NGA operations runbook — refresh worker, alerts and rollback

Phase 10 operational procedures for the Navigation-Grounded Agent (NGA).
The design set is in `doc/sdlc/nga/`; this file is the on-call companion.
The durable conversation stores and the measured turn-event write cost are in
`doc/ops/conversation-storage.md`.

The refresh worker is a **separate background process**. It never runs on the
answer path and never writes during a live call or chat turn.

---

## 1. Process map

| Process | Entry point | Purpose | Required flags |
|---|---|---|---|
| API (FastAPI) | `app/main.py` | Chat, WhatsApp, voice, Streamlit | `NGA_ENABLED` for any NGA answer path |
| Refresh worker | `scripts/nga_scheduler.py` | Periodic page refresh, PDF index, report, alerts | `NGA_SCHEDULER_ENABLED=true` and `--allow-network` |
| One-off refresh | `scripts/nga_refresh.py` | Manual bounded page refresh | `--allow-network` |
| Document index | `scripts/nga_documents.py` | Discover, fetch, extract PDFs; OCR review queue | `NGA_PDF_INDEX_ENABLED=true` and `--allow-network` |
| Graph compiler | `scripts/nga_compile.py` | Rebuild the navigation graph | network optional |
| Status check | `scripts/nga_scheduler.py --status` | Offline report and alerts | none |

The API and the worker share the SQLite graph file. The worker is the only
writer during a refresh cycle; an advisory file lock in
`NGA_REFRESH_LOCK_PATH` enforces one worker.

### Files

| Path | Contents | Retention |
|---|---|---|
| `content/dspmu/nga_graph.sqlite3` | Graph, page bodies, extracts, freshness, events | rebuildable, back it up |
| `content/dspmu/nga_coverage.json` | Last compile coverage report | keep |
| `content/dspmu/nga_refresh_report.json` | Latest refresh run (atomic) | keep |
| `logs/nga_refresh.jsonl` | Run history used by alert rules | 30 days recommended |
| `logs/nga_alerts.jsonl` | Alert events | 90 days recommended |
| `content/dspmu/nga_refresh.lock` | Advisory lock (empty when idle) | never edit |

---

## 2. Preconditions

Do not enable the worker until all of these are true:

- The university's written permission for automated access is on file.
- `robots.txt` and any Content-Signal policy have been reviewed for the
  allowlisted hosts.
- The fact layer has a named reviewer; fees remain `status: draft` until
  signed off.
- A document reviewer is named for OCR output. The reviewer, not the model,
  decides when OCR text becomes answerable.
- An operator owns the alert log and has an escalation path.
- `NGA_ENABLED` has passed the Phase 13 canary. The worker may run earlier in
  shadow mode, but no answer path reads its content until `NGA_ENABLED=true`.

---

## 3. Start and stop

### Configuration

```dotenv
NGA_SCHEDULER_ENABLED=true
NGA_REFRESH_INTERVAL_S=900
NGA_REFRESH_JITTER_S=60
NGA_REFRESH_MAX_CYCLES=0
NGA_REFRESH_LIMIT=10
NGA_FETCH_DELAY_S=0.25
NGA_REFRESH_REPORT_PATH=content/dspmu/nga_refresh_report.json
NGA_REFRESH_LOG_PATH=logs/nga_refresh.jsonl
NGA_ALERT_LOG_PATH=logs/nga_alerts.jsonl
NGA_REFRESH_LOCK_PATH=content/dspmu/nga_refresh.lock

# Background PDF/OCR index
NGA_PDF_INDEX_ENABLED=true
NGA_TTL_DOCUMENT_S=604800
NGA_PDF_REFRESH_LIMIT=5
NGA_PDF_MAX_BYTES=10000000
NGA_PDF_MAX_PAGES=40
NGA_PDF_MIN_CHARS=80
NGA_PDF_OCR_ENABLED=true
NGA_PDF_OCR_MAX_PAGES=8
NGA_PDF_OCR_LANGS=
NGA_PDF_FAILURE_BACKOFF_S=3600
```

The scheduler refuses to start when `NGA_SCHEDULER_ENABLED=false`, even if
`NGA_ENABLED=true`. `--force` bypasses that guard for a controlled one-off and
should not be used by the service unit. `NGA_PDF_INDEX_ENABLED=false` keeps
document indexing off on the same worker; `--no-documents` skips it for one
run.

### Manual

```bash
# One cycle, foreground, with output
.venv/bin/python scripts/nga_scheduler.py --allow-network --once

# Two cycles, then exit
.venv/bin/python scripts/nga_scheduler.py --allow-network --max-cycles 2

# Continuous (default), stop with Ctrl-C or SIGTERM
.venv/bin/python scripts/nga_scheduler.py --allow-network

# Document index without the page worker
.venv/bin/python scripts/nga_documents.py --status
.venv/bin/python scripts/nga_documents.py --discover-only
.venv/bin/python scripts/nga_documents.py --allow-network --limit 3

# Review OCR text before it becomes answerable
.venv/bin/python scripts/nga_documents.py --list-review
.venv/bin/python scripts/nga_documents.py \
  --review doc.<id> --reviewer "registrar office"
```

### systemd

```ini
[Unit]
Description=DSPMU NGA refresh scheduler
After=network-online.target

[Service]
WorkingDirectory=/opt/aichatagent
EnvironmentFile=/opt/aichatagent/.env
ExecStart=/opt/aichatagent/.venv/bin/python scripts/nga_scheduler.py --allow-network
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now nga-refresh
sudo systemctl stop nga-refresh
journalctl -u nga-refresh -n 100 --no-pager
```

### launchd (macOS)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ac.dspmu.nga-refresh</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/aichatagent/.venv/bin/python</string>
    <string>/opt/aichatagent/scripts/nga_scheduler.py</string>
    <string>--allow-network</string>
  </array>
  <key>WorkingDirectory</key><string>/opt/aichatagent</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/opt/aichatagent/logs/nga_scheduler.out</string>
  <key>StandardErrorPath</key><string>/opt/aichatagent/logs/nga_scheduler.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/ac.dspmu.nga-refresh.plist
launchctl unload ~/Library/LaunchAgents/ac.dspmu.nga-refresh.plist
```

### Stop the whole NGA path

1. Stop the worker (`systemctl stop nga-refresh` or `launchctl unload`).
2. Set `NGA_SCHEDULER_ENABLED=false` and `NGA_PDF_INDEX_ENABLED=false`.
3. Set `NGA_ENABLED=false` and `NGA_MODE=off` in the API environment.
4. Restart the API.

With `NGA_ENABLED=false`, `app/rag.py` reproduces the static bundle path; no
code rollback is required for an answer-path emergency.

---

## 4. Daily checks

```bash
# The operator-facing summary. Exit code 1 means a critical alert.
.venv/bin/python scripts/nga_scheduler.py --status

# Machine-readable
.venv/bin/python scripts/nga_scheduler.py --status --json

# Last five runs
tail -n 5 logs/nga_refresh.jsonl | jq -c '{
  finished_at, checked: .stats.checked, changed: .stats.changed,
  errors: .stats.errors, due: .freshness.due_now, stale: .freshness.stale
}'

# Critical alerts only
jq -c 'select(.severity == "critical")' logs/nga_alerts.jsonl

# Document index and OCR review queue
.venv/bin/python scripts/nga_documents.py --status
.venv/bin/python scripts/nga_documents.py --list-review

# Voice→WhatsApp reference delivery log
.venv/bin/python scripts/nga_references.py --status
```

Weekly:

- Confirm the newest `graph.compiled_at` is within `NGA_ALERT_GRAPH_STALE_HOURS`.
- Confirm `freshness.never_checked` is trending down, not up.
- Clear or triage the OCR review queue (`review_pending`).
- Spot-check one cited answer against the live page, including one PDF-derived
  answer.
- Review `coverage.warnings` after any compile.
- Check disk space under `content/dspmu/` and `logs/`.

---

## 5. Alert catalog

| Code | Severity | Meaning | First action |
|---|---|---|---|
| `coverage_below_gate` | critical | Sitemap coverage is below `NGA_COVERAGE_MIN` | Inspect `nga_coverage.json`; if the sitemap shrank unexpectedly, verify the sitemap and recompile |
| `coverage_drop` | warning | Coverage fell at least `NGA_ALERT_COVERAGE_DROP` against the previous compile | Compare `missing_sitemap_urls`; check for a site restructure |
| `section_missing` | warning | A top-level navigation section disappeared from the new compile | Compare `top_level_sections`; if the university renamed it, accept and update aliases |
| `graph_missing` | critical | No compiled graph exists | Run `scripts/nga_compile.py`; verify the DB path |
| `graph_stale` | warning | Graph older than `NGA_ALERT_GRAPH_STALE_HOURS` | Recompile in a maintenance window, then refresh bodies |
| `refresh_failure_streak` | critical | `NGA_ALERT_RUN_FAILURE_STREAK` consecutive runs had page errors | Check site reachability and DNS; check robots; see R1 |
| `node_failure_streak` | warning | Pages failed `NGA_ALERT_NODE_FAILURE_STREAK` fetches in a row | Inspect `nga_fetch_state.error` for those nodes |
| `refresh_error_rate` | warning | At least 50% of checked pages failed in one run | Usually the site or network; see R1 |
| `empty_shell_spike` | warning | Many pages extracted below `NGA_MIN_PAGE_CHARS` | The site may have changed its HTML shell; see R5 |
| `soft_404_spike` | warning | Many pages return the site's soft-404 page | The site may have moved pages; see R5 |
| `stale_backlog` | warning | At least `NGA_ALERT_STALE_NODES` pages are overdue past the grace period | Raise the run limit or interval; see R4 |
| `pii_guard_mismatch` | warning | Compiled guard setting differs from the runtime flag | Recompile with the intended flag or change `NGA_PII_GUARD_ENABLED`; see R6 |
| `document_error_rate` | warning | At least 50% of checked PDFs failed fetch or extraction | Check the URLs and PDF validity; see R13 |
| `document_review_backlog` | warning | At least `NGA_ALERT_REVIEW_BACKLOG` OCR documents await review | Assign a reviewer; unreviewed text is not answerable; see R13 |
| `reference_send_failures` | warning | At least `NGA_ALERT_REFERENCE_FAILURES` WhatsApp reference sends failed in the window | Check Twilio credentials, sender, recipient and delivery log; see R14 |
| `reference_template_required` | warning | At least `NGA_ALERT_TEMPLATE_REQUIRED` sends needed an approved template outside 24 h | Get the approved template SID configured; see R14 |

Alerts are deterministic and evaluated once per run. A repeating alert remains
in the log until the condition clears; there is no built-in dedupe. Ship the
JSONL to the external monitor and dedupe by `code` there.

Example monitor wiring (file-based, no new network egress):

```bash
tail -F logs/nga_alerts.jsonl | jq -c 'select(.severity == "critical")'
```

---

## 6. Incident runbooks

### R1 — Refresh runs are failing

**Symptoms:** `refresh_failure_streak`, `refresh_error_rate`, many
`status: error` entries.

1. Run a single status check: `scripts/nga_scheduler.py --status`.
2. Test reachability without the worker:
   ```bash
   curl -I https://dspmuranchi.ac.in/
   curl -I https://dspmuranchi.ac.in/sitemap.xml
   ```
3. If the site is down, stop the worker or let backoff run. The last good
   content remains answerable; there is no destructive action.
4. If the site is up but fetches fail, check `nga_fetch_state.error`:
   ```bash
   sqlite3 content/dspmu/nga_graph.sqlite3 \
     "SELECT node_id, failure_count, http_status, error
      FROM nga_fetch_state WHERE failure_count > 0
      ORDER BY failure_count DESC LIMIT 20;"
   ```
5. A `403`/`429` means the request rate or user agent is unwelcome. Stop the
   worker, contact the university, and do not raise the rate. Never use a
   stealth user agent.
6. A redirect or certificate error means the final URL failed allowlist or TLS
   revalidation. Keep both hosts in `NGA_ALLOWED_HOSTS` and do not disable
   verification.

### R2 — Coverage dropped or a section disappeared

1. Read `content/dspmu/nga_coverage.json`:
   - `missing_sitemap_urls`
   - `coverage_pct`, `min_coverage`, `meets_gate`
   - `warnings`
2. Check whether the sitemap itself changed:
   ```bash
   curl -s https://dspmuranchi.ac.in/sitemap.xml | head -40
   ```
3. If pages were removed by the university, coverage is expected to change;
   record the change and recompile so the graph reflects the site.
4. If the sitemap is truncated (fewer URLs than the previous compile), do not
   overwrite the graph. Fetch it again later and compare. The previous graph
   keeps serving.
5. If the drop is caused by the PII guard being enabled, confirm that with the
   product owner before changing it back (see R6).

### R3 — Graph is stale

The graph captures navigation and metadata; bodies are refreshed separately.
An old graph means new pages are invisible even if the body fetch is healthy.

1. Prefer a maintenance window; a compile **replaces the graph and clears
   page bodies and freshness state** (see R10).
2. Back up first:
   ```bash
   sqlite3 content/dspmu/nga_graph.sqlite3 \
     ".backup 'content/dspmu/nga_graph.$(date +%Y%m%dT%H%M%S).sqlite3'"
   ```
3. Stop the worker.
4. Compile:
   ```bash
   .venv/bin/python scripts/nga_compile.py
   ```
5. Re-warm page bodies:
   ```bash
   .venv/bin/python scripts/nga_scheduler.py --allow-network --once --limit 50
   ```
   Repeat (or let the worker run) until `freshness.never_checked` reaches zero.
   The default limit is 10 per cycle; at 15-minute intervals that is about 960
   pages/day, which covers the current 308 HTML pages quickly.
6. Restart the worker.

### R4 — Stale backlog

The worker fetches `NGA_REFRESH_LIMIT` pages per cycle. A backlog means the
cycle interval, the limit, or the TTL is mismatched.

1. Calculate capacity: `limit × (3600 / interval_s)` pages per hour.
2. Compare with the HTML page count in `--status`.
3. Raise `NGA_REFRESH_LIMIT` gradually (25–50) or shorten the interval.
   Respect `NGA_FETCH_DELAY_S`; the worker is single-threaded by design.
4. Do not lower the TTL to make numbers look better. Freshness is bounded by
   what the university's site tolerates.

### R5 — Empty shells or soft 404s

A page that extracts below `NGA_MIN_PAGE_CHARS` is recorded as `empty_shell`
and is not answerable. A page that returns the site's "not found" body is
`soft_404`.

1. Open two or three affected URLs in a browser and with `curl`.
2. If the site changed its markup, re-run the extractor tests and inspect
   `app/sitegraph/extract.py` against a saved page.
3. If the university moved pages, wait for a sitemap update and recompile.
4. Never lower `NGA_MIN_PAGE_CHARS` to force a page into the index; a shell
   answerable is worse than a clean refusal.

### R6 — PII guard mismatch

`content/dspmu/nga_graph.sqlite3` stores the guard setting used at compile
time. The runtime reads `NGA_PII_GUARD_ENABLED`.

1. Confirm the product decision: the 2026-10-24 default is `false` because the
   chat is part of the public website.
2. If the runtime flag changed, run the compiler with the matching flag:
   ```bash
   .venv/bin/python scripts/nga_compile.py          # reads the flag
   ```
3. If the compile was made with the wrong flag, recompile; do not edit the DB.
4. After recompile, re-warm bodies (R10).
5. Documents content-blocked under the old setting stay blocked after the
   runtime flag changes. A recompile clears them; re-index with the intended
   guard setting.

### R7 — Lock is held but no worker is running

The lock is an advisory `flock`; the kernel releases it when the process dies.
A non-empty lock file with no holder is harmless.

1. Check for a live process:
   ```bash
   cat content/dspmu/nga_refresh.lock
   ps -p "$(sed -n 's/^pid=\([0-9]*\).*/\1/p' content/dspmu/nga_refresh.lock)"
   ```
2. If the PID is gone or belongs to another service, do **not** delete the file
   while a worker might be writing. Stop the service, confirm no process, then
   remove it.
3. Never run two workers to "catch up". The second one skips.

### R8 — Graph database is corrupt or missing

The graph is rebuildable from the site; the event log is the durable audit.

1. Stop the API worker and the scheduler.
2. Preserve the broken file:
   ```bash
   mv content/dspmu/nga_graph.sqlite3 \
      content/dspmu/nga_graph.corrupt.$(date +%Y%m%dT%H%M%S).sqlite3
   ```
3. Rebuild:
   ```bash
   .venv/bin/python scripts/nga_compile.py
   .venv/bin/python scripts/nga_scheduler.py --allow-network --once --limit 50
   ```
4. If a backup exists, restore it before rebuilding:
   ```bash
   sqlite3 content/dspmu/nga_graph.sqlite3 \
     ".restore 'content/dspmu/nga_graph.<timestamp>.sqlite3'"
   ```
5. Restart the API and verify `scripts/nga_scheduler.py --status`.

### R9 — Rollback

| Change | Rollback |
|---|---|
| Answer path misbehaving | `NGA_ENABLED=false`, `NGA_MODE=off`; restart API. Static behaviour returns |
| Worker misbehaving | `systemctl stop nga-refresh`; set `NGA_SCHEDULER_ENABLED=false` |
| Bad graph | Stop worker; restore the last backup; see R8 |
| Bad code | `git revert` the deploy commit; redeploy; the graph format is additive |
| Bad fact | Revert `content/dspmu/facts.json`; a fee remains blocked if unverified |

A kill switch is deliberately independent of the graph: with `NGA_ENABLED=false`
the static bundle is used even if the worker is still running.

### R10 — Recompile warning

`GraphStore.replace_graph` deletes and reinserts `nga_node`; the foreign keys
cascade to `nga_page_version`, `nga_fetch_state` and `nga_document`, and the
body FTS table is cleared. A compile therefore returns every page to "never
fetched" and discards extracted document text, including approvals.

Always:

1. Back up.
2. Compile in a maintenance window.
3. Re-warm pages and documents immediately with a larger `--limit`.
4. Verify `site.search` and `site.open` on two known pages and one PDF.
5. Re-review OCR documents if the re-warm regenerates OCR text.

A compile is safe for the answer path (readers see either the old or the new
graph), but answers that depended on page or document bodies will be
metadata-only until the re-warm completes.

### R11 — Fact correction from the university

1. Edit `content/dspmu/facts.json`; keep `source_url`, `valid_from`,
   `valid_until`, `approved_by`, and the document hash.
2. Fees/dates remain `status: draft` until the registrar's sign-off is recorded.
   The verifier blocks unsupported amounts either way.
3. Run the DSPMU grounding checks and the NGA answer tests.
4. Record the correction in the evaluation set; an unanswered case becomes a
   new graph/index gap, not a prompt tweak.

### R12 — Conversation-event retention

`NGA_EVENT_RETENTION_DAYS` is applied by `GraphStore.purge_turn_events`; there
is no automatic purge scheduler yet.

1. Confirm the retention policy with the privacy owner.
2. Run the purge from a maintenance job:
   ```bash
   .venv/bin/python - <<'PY'
   from datetime import datetime, timedelta, timezone
   from app.config import settings
   from app.sitegraph.store import GraphStore
   before = (datetime.now(timezone.utc) -
             timedelta(days=settings.NGA_EVENT_RETENTION_DAYS))
   store = GraphStore(settings.NGA_GRAPH_SQLITE_PATH)
   print(store.purge_turn_events(before.strftime("%Y-%m-%dT%H:%M:%SZ")))
   PY
   ```
3. Purge is idempotent; it never removes graph content.

Reference delivery rows have their own retention:

```bash
.venv/bin/python scripts/nga_references.py --status
.venv/bin/python scripts/nga_references.py --purge
```

`NGA_REFERENCE_LOG_RETENTION_DAYS` defaults to 30. The delivery service also
purges on process start, but the explicit command is the reliable maintenance
job. Full transcripts in Postgres `conversations` follow the CRM/leads
retention policy, not these NGA settings.

### R13 — PDF extraction failures or an OCR review backlog

**Symptoms:** `document_error_rate`, `document_review_backlog`, documents stuck
in `pending` or `error`.

1. Read the summary and the failing rows:
   ```bash
   .venv/bin/python scripts/nga_documents.py --status
   sqlite3 content/dspmu/nga_graph.sqlite3 \
     "SELECT node_id, url, extraction_status, needs_review, error,
             pages, size_bytes FROM nga_document
      WHERE extraction_status IN ('error','empty') OR needs_review = 1
      ORDER BY extraction_status, node_id LIMIT 30;"
   ```
2. A `FetchError` (`HTTP 403/404`, content type, magic byte) is a source or
   policy problem. Do not bypass the allowlist or the PDF magic check. Check
   the link on the page; a moved PDF should be re-linked by the university.
3. A password-protected or corrupt PDF yields an extraction error. Record it
   and exclude it from expectations; retries use exponential backoff.
4. `empty` means fewer than `NGA_PDF_MIN_CHARS` characters were found and OCR
   did not recover text. Do not lower the floor to force a document in.
5. An OCR backlog is a human task, not a model task:
   ```bash
   .venv/bin/python scripts/nga_documents.py --list-review
   # Open the source URL, compare the text, then:
   .venv/bin/python scripts/nga_documents.py \
     --review doc.<id> --reviewer "<name>"
   ```
   Approval indexes the stored text and records the reviewer name and time.
   Rejecting a bad OCR result requires re-extraction with a better source or
   leaving it unapproved; there is no delete command, and an unapproved
   document remains metadata-only.
6. If OCR is not wanted at all, set `NGA_PDF_OCR_ENABLED=false`; scanned PDFs
   then become `empty` instead of entering review.

### R14 — Voice→WhatsApp reference delivery failures

**Symptoms:** `reference_send_failures`, `reference_template_required`, or a
registered caller reports no WhatsApp follow-up.

1. Inspect the delivery log; it stores only a phone hash, last four digits and
   the reference URL, never the message body:
   ```bash
   .venv/bin/python scripts/nga_references.py --status
   .venv/bin/python scripts/nga_references.py --list --limit 20
   ```
2. `failed`: check Twilio credentials, sender number, and the `error` column.
   Retries are bounded by `NGA_REFERENCE_RETRY_MAX`; an exhausted row is final.
3. `template_required`: the lead is outside the 24-hour customer-service
   window. Configure an approved template SID in `NGA_WHATSAPP_TEMPLATE_SID`.
   The template must expose `{{1}}`, which receives the rendered message.
4. `skipped` with `no_lead`: the caller's number has no lead. Do not create a
   lead just to make delivery pass; registration is a consent signal.
5. `skipped` with `opted_out`: STOP is authoritative. Never override it; only
   the caller's START clears it.
6. `skipped` with `call_cap` or duplicate means the per-call limit or the
   `(call_sid, node_id)` uniqueness rule worked as designed.
7. Verify the 24-hour window from Postgres:
   ```sql
   SELECT MAX(created_at) FROM conversations
   WHERE lead_id = '<lead-id>' AND channel = 'whatsapp';
   ```
8. The delivery queue and a `call_end` pending batch are in memory. A restart
   loses queued work; completed attempts remain in the log. If this matters,
   switch to `turn` mode or add a durable outbox in Phase 13.

---

## 7. Backup and restore

The graph and the event log are a single SQLite file. Use the SQLite backup
API, not `cp`, while the API is running.

```bash
mkdir -p backups
sqlite3 content/dspmu/nga_graph.sqlite3 \
  ".backup 'backups/nga_graph.$(date +%Y%m%dT%H%M%S).sqlite3'"
```

Test a restore on a copy monthly:

```bash
cp backups/nga_graph.<timestamp>.sqlite3 /tmp/nga-restore-test.sqlite3
.venv/bin/python scripts/nga_scheduler.py --status \
  --db /tmp/nga-restore-test.sqlite3 \
  --report-path /tmp/no-report.json \
  --run-log /tmp/no-runs.jsonl
```

`content/dspmu/nga_coverage.json` and `content/dspmu/facts.json` are small and
should be backed up with the repository.

---

## 8. Change management

- One worker per graph; the lock is advisory, not a scheduler.
- Alert thresholds are configuration, not code. Record any change and why.
- `NGA_ROBOTS_RESPECT=true` is not optional. A university request to crawl
  differently is handled through written permission and a config review, not a
  code bypass.
- The user agent must carry a contact address; keep it current.
- No new outbound service is introduced by this phase. Alerts are local JSONL;
  ship them with the existing log pipeline.

---

## 9. Known limits

- A compile clears page bodies, freshness and extracted documents; see R10.
- The document index auto-indexes PDFs linked from already-indexed pages. A
  sitemap PDF with no page link needs an explicit
  `scripts/nga_documents.py --node-id ... --force`. It does not crawl every
  attachment on the site.
- Document extraction keeps one current text per PDF, not a version history.
  A failed refresh preserves the last good text.
- OCR text is never answerable until a named reviewer approves it; the
  scheduler itself never approves. Unreviewed text is stored in the graph DB
  but is not exposed by any tool, report or alert; protect DB access like the
  event log.
- Voice→WhatsApp delivery is in-memory queued. A process restart drops queued
  or `call_end`-pending references; completed attempts remain in
  `nga_reference_delivery`.
- Full transcripts live in Postgres `conversations`; `nga_turn_event` is the
  NGA structured memory, not a replacement for the transcript. Voice turns
  currently write the transcript but not an NGA event. See
  `doc/ops/conversation-storage.md`.
- The scheduler does not recompile the graph automatically. `graph_stale`
  prompts an operator instead.
- The alert log has no built-in dedupe, delivery retry or paging integration.
- Live text channels still do not write `nga_turn_event`; dashboard metrics
  for turns arrive with the channel wiring phase.
- The scheduler and the API share one SQLite writer; for a multi-host
  deployment, move the graph to Postgres before running more than one writer.
