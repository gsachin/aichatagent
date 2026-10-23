# Demo Freeze — Status Report

**Report date:** 2026-10-23 (updated after the same-port validation run)
**Demo folder:** `D:\project\UniversityBotDemoAPI`
**Development source:** `D:\project\universityDemo`
**Runtime source commit:** `07083ae` — feat(chat): language selection at chat start, localized WhatsApp replies
**Manifest source commit:** the latest clean commit at sync time (the validation run used `da75f59`); the 138 runtime files are unchanged from `07083ae`
**Reference docs:** `DEMO_FREEZE_PLAN.md`, `DEMO_FREEZE_VALIDATION.md`, `README_DEMO.md` (inside the demo folder)

---

## At a glance

| Area | Status |
|---|---|
| Demo folder populated | **DONE** — 138 managed files, 6.69 MB |
| Sync engine + tests | **DONE** — 12/12 passing |
| Seed safety guard | **DONE** |
| Demo venv | **DONE** — 7.57 GB |
| Demo database + seed data | **DONE** — verified 2026-10-23 |
| Same-port runtime validation | **DONE** — 37 PASS / 0 BLOCKED / 0 WARN |
| Demo `.env` secrets | **DONE** — all six set and validated |
| Live integration checks | **DONE** — LLM, Streamlit chain, Sarvam TTS/STT, Twilio API, CRM read, dashboard |
| Live Twilio/WhatsApp rehearsal | **PARTIAL** — Twilio API and voice webhook verified; no real call or message sent |
| Dev tooling committed + clean sync | **DONE** — tooling commit `6fafc3b`; manifest `status: clean`, `source_tracked_dirty=false` |
| Demo git repository | **PENDING** — yours to create |

---

## 1. Where the demo lives

```
D:\project\UniversityBotDemoAPI
```

The development project stays at `D:\project\universityDemo`. The demo is a standalone copy: it has its own `.env`, its own `.venv`, its own database (`admissions_demo`), and no git repository yet.

Demo root contents:

| Item | Purpose |
|---|---|
| `app/`, `app.py`, `dashboard.py` | Runtime — byte-identical to commit `07083ae` |
| `content/` | Meridian knowledge base |
| `requirements.txt` | Pinned runtime dependencies |
| `.env` | Demo configuration — secrets currently blank |
| `env.demo.example` | Template with no secrets |
| `.demo_manifest.json` | 138 managed files, source commit, integrity hashes |
| `.demo_directory` | Marks this folder as a demo target |
| `DEMO_VERSION.txt` | Source commit, sync time, file count, status |
| `.gitignore` | Excludes `.env`, `.venv/`, `data/`, `logs/`, runtime state |
| `README_DEMO.md` | Setup, start/stop, troubleshooting |
| `demo_setup.ps1`, `demo_start.ps1`, `demo_stop.ps1`, `demo_seed.ps1`, `demo_twilio.ps1` | Demo support scripts |
| `.venv/` | Demo virtual environment (7.57 GB) |

Excluded from the demo: `tests/`, `doc/`, `models/`, `data/`, `logs/`, `__pycache__/`, development `.env`, development git history.

---

## 2. What is done

### 2.1 Development-side tooling

| Deliverable | Location | State |
|---|---|---|
| Sync engine + manifest + audit | `scripts/sync_demo.py` | Working |
| Sync tests | `tests/test_sync_demo.py` | **12 passed** (verified 2026-10-23) |
| Validator | `scripts/validate_demo.py` | Working; supports `--check-http` and `--report` |
| Demo templates | `scripts/demo/` | Setup/start/stop/seed/Twilio scripts |
| Seed guard | `scripts/seed_demo_data.py` | Refuses non-`_demo` DBs, requires `--yes`, uses a transaction |
| Plan | `DEMO_FREEZE_PLAN.md` | Finalized |
| Validation report | `DEMO_FREEZE_VALIDATION.md` | Final run 2026-10-23: 37 PASS / 0 BLOCKED / 0 WARN |
| Status report | `DEMO_FREEZE_STATUS.md` | This document |

### 2.2 Demo tree

- **138 managed files, 6.69 MB** (excluding `.venv`, `logs`, `data`, caches).
- Manifest status: **clean**; source commit `07083ae`.
- All 138 manifest hashes match the development source.
- `tests/`, `doc/`, `models/`, `tts_service/`, and Chroma directories confirmed absent.
- `app/`, `app.py`, `dashboard.py` are byte-identical to the client-approved commit.

### 2.3 Demo runtime environment

- Demo venv: `D:\project\UniversityBotDemoAPI\.venv` — **7.57 GB**, all imports working (including `psycopg2`).
- Database: `admissions_demo` on the `elearning-postgres` container's **published host port 9200** (not 5432).
- Schema: 10 tables (`call_queue`, `conversations`, `courses`, `crm_sync_outbox`, `follow_ups`, `lead_calls`, `lead_documents`, `leads`, `offer_letters`, `sentiment_scores`).
- Seed data **verified 2026-10-23**: 12 leads, 8 conversations, 5 follow-ups, 14 courses.

### 2.4 Validation results — final run 2026-10-23

Run on the standard ports **8000/8501/8502** with the demo venv, the real demo database, and the CRM API on `8098`.

**Result: 37 PASS / 0 BLOCKED / 0 WARN.**

| Status | Check | Detail |
|---|---|---|
| PASS | FastAPI health | HTTP 200 at `http://127.0.0.1:8000/` |
| PASS | Streamlit health | HTTP 200 at `http://127.0.0.1:8501/_stcore/health` |
| PASS | CRM health | HTTP 200 at `http://127.0.0.1:8098/health` |
| PASS | CRM read endpoint | HTTP 200 at `http://127.0.0.1:8098/admissions` |
| PASS | database connection | `current_database=admissions_demo` |
| PASS | database schema | `leads`, `conversations`, `courses` present |
| PASS | demo venv imports app.main |  |
| PASS | development tree clean at sync | manifest records clean source commit `29787f3` |
| PASS | demo secrets filled | all six secrets present |
| PASS | LLM and Sarvam keys configured | chat/voice checks enabled |
| PASS | Twilio credentials configured | all four `TWILIO_*` values present |

The stack was stopped cleanly afterwards with `demo_stop.ps1`; ports 8000/8501/8502/8098 are free again.

### 2.5 Integration verification — 2026-10-23

With all six secrets in place, every reachable integration was exercised directly:

| Check | Result |
|---|---|
| LLM via `/ws/voice/text` | **PASS** — real answer in 5.07 s: "Meridian University offers undergraduate, postgraduate, and doctoral programs across Engineering, Computer Science, Business, Arts, Science, and Commerce." |
| Streamlit LangChain chain | **PASS** — chain used by the chat UI answered in 2.66 s: "MBA tuition is $18,500 per year" |
| Sarvam TTS (Hindi) | **PASS** — 1.85 s of audio generated in 904 ms |
| Sarvam STT round-trip | **PASS** — synthesized Hindi speech transcribed back exactly ("नमस्ते यह एक परीक्षण है।") in 431 ms |
| Twilio account | **PASS** — status `active`, type `Full` |
| Twilio voice number | **PASS** — configured number is on the account with voice and SMS capabilities |
| Twilio Calls API | **PASS** — read access confirmed |
| Twilio Messages API | **PASS** — read access confirmed |
| Local Twilio voice webhook | **PASS** — HTTP 200, valid TwiML language menu |
| WhatsApp number | **PASS (ownership expectation)** — configured number is the shared Twilio Sandbox number, not an owned incoming number; this is expected |
| CRM health and read | **PASS** — HTTP 200 on `/health` and `/admissions` |
| Dashboard summary | **PASS** — seeded leads, conversations, and pipeline stats returned |
| `/ready` after restart | **PASS** — `ready=true`, all four clauses true |
| `-TunnelUi` UI tunnels | **PASS** — chat (8501) and dashboard (8502) quick tunnels both returned HTTP 200; `demo_stop.ps1` stopped both |

Not exercised: a real Twilio voice call, a real WhatsApp message exchange (needs a recipient who has joined the Sandbox), and CRM writes (kept read-only).

---

## 3. Task status

### DONE — P2: Docker and database

- Docker Desktop started; daemon 29.7.2.
- `elearning-postgres` healthy, `0.0.0.0:9200->5432`.
- Demo DB reachable with the demo venv; seed counts verified.

### DONE — P3: Same-port runtime validation

The earlier alternate-port gap (8100/8601) is closed. The demo ran on its standard ports and passed all stack/database checks. The three blockers are credentials only, not runtime defects.

### DONE — P5: Commit development tooling and re-sync

The demo tooling and docs were committed as `6fafc3b` (15 files). The subsequent sync reported:

- Source `6fafc3b` (clean)
- `KEEP=138`, `copied=0 deleted=0 conflicts=0`
- `.demo_manifest.json`: `status: clean`, `source_tracked_dirty=false`
- `DEMO_VERSION.txt` updated to the same commit

The runtime `app/`, `app.py`, and `dashboard.py` are unchanged from `07083ae`.

### DONE — P1: Demo secrets filled and validated

All six values are present in `D:\project\UniversityBotDemoAPI\.env` and were exercised on 2026-10-23: LLM completions, the Streamlit LangChain chain, Sarvam TTS, Sarvam STT, and Twilio account, number, and API checks all succeeded. Secret values are not copied or printed in this report.

### PENDING — P4: Live rehearsal (credentials verified; no real call or message yet)

`demo_start.ps1 -Live -StartCrm` will:

1. Start the Cloudflare tunnel.
2. Back up and repoint the Twilio voice webhook (number ending `8953`).
3. Start the CRM API at `127.0.0.1:8098` if it is down.
4. Print the WhatsApp Sandbox webhook URL for you to set manually.

`demo_stop.ps1` restores the voice webhook and prints the WhatsApp restore URL.

### PENDING — P6: Create the demo git repository (owner: you)

The demo folder has no `.git`. Its `.gitignore` is ready. A fresh repo will be about 6.7 MB because `.env`, `.venv/`, `data/`, and `logs/` are ignored.

### PARTIAL — P7: Post-secret verification (owner: me)

Verified on 2026-10-23: LLM text path, the Streamlit chat chain, Sarvam TTS, Sarvam STT round-trip, Twilio account and number, Twilio Calls/Messages API read access, the local voice webhook, CRM health/read, and dashboard reads.

Not yet exercised: a real Twilio voice call, a real WhatsApp message exchange (requires a recipient who has joined the Sandbox), and a CRM write (deliberately read-only so far).

---

## 4. Machine snapshot (verified 2026-10-23, after validation and sync)

| Check | Result |
|---|---|
| Docker daemon | Running (29.7.2) |
| `elearning-postgres` | Healthy; host port 9200 |
| Demo database | `admissions_demo` reachable; seed counts verified |
| Ports 8000 / 8501 / 8502 | Free — demo stopped after validation |
| CRM port 8098 | Free — stopped with the demo |
| Development stack | Stopped |
| Demo `.env` | Exists; all six required secrets set |
| Demo manifest | `status: clean`, `source_tracked_dirty: false`, 138 files; source commit advances with docs commits |
| `/ready` after restart | `ready=true`, all four clauses true |
| Sync tests | 12 passed |

---

## 5. Remaining sequence

```powershell
# 1. Live rehearsal (secrets are already set); -TunnelUi also tunnels the
#    Streamlit chat (8501) and dashboard (8502), matching the dev launcher
cd D:\project\UniversityBotDemoAPI
.\demo_start.ps1 -Live -StartCrm -TunnelUi

# 2. In the Twilio console, set the WhatsApp Sandbox webhook to the printed
#    https://<tunnel>/twilio/whatsapp URL (HTTP POST)

# 3. After the rehearsal
.\demo_stop.ps1
```

Local (non-live) rehearsal needs no secrets beyond what is already configured:

```powershell
cd D:\project\UniversityBotDemoAPI
.\demo_start.ps1 -StartCrm
```

---

## 6. Constraints and notes

- **Port 9200, not 5432.** The `elearning-postgres` container publishes PostgreSQL on host port 9200; `127.0.0.1:5432` is a different server. `demo_setup.ps1 -InitDb` detects the port with `docker port`; do not hardcode 5432.
- **One stack at a time.** Dev and demo share 8000/8501/8502. `demo_start.ps1` refuses to start if any of them is occupied and never kills the owner.
- **Secrets stay out.** The demo `.env` is generated locally with a demo-only DB password and is excluded by `.gitignore`; it is never synced or committed.
- **Smart App Control.** The demo venv's `psycopg2` DLL was briefly blocked by Windows Application Control, then cleared after re-evaluation. It imported and connected successfully during the 2026-10-23 validation.
- **CRM is shared.** The demo uses the same Salesforce org through the API at `:8098`; demo and development records mix in that org. Validation so far was read-only.
- **Twilio is shared.** The same voice number is repointed at the demo tunnel and restored afterwards. WhatsApp Sandbox switching is manual.
- **WhatsApp Sandbox number.** The configured WhatsApp number is the shared Twilio Sandbox number; it is not an owned incoming number on the account, which is expected. A real message test requires a recipient who has joined the Sandbox.
- **Readiness boot race.** Immediately after a start, `/ready` can report `not_ready` if the remote LLM probe loses the startup race. A restart with the keys present reported `ready=true` with all four clauses.
- **CRM writes remain untested.** Verification was read-only; no Salesforce records were created.
- **Runtime quality is unchanged.** Same model, prompts, knowledge base, STT/TTS providers, voices, and 41 pre-rendered audio files. Only the database name, secrets, tunnel URL, and webhook target differ.
