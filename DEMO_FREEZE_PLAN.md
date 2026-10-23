# Demo Freeze Plan — `UniversityBotDemoAPI`

**Status:** Implemented — see `DEMO_FREEZE_VALIDATION.md` (PASS with credential/stack blockers)  
**Date:** 2026-10-22  
**Development source:** `D:\project\universityDemo`  
**Freeze commit:** `07083aece22774c592d68314a3bc86f8296cff59`  
`feat(chat): language selection at chat start, localized WhatsApp replies` (2026-10-20)  
**Demo target:** `D:\project\UniversityBotDemoAPI` (exists, empty, no `.git`)  
**Runtime:** the current client-approved version. No application code changes.

---

## 1. Goal

Create a small, independently runnable copy of the current tested application in
`D:\project\UniversityBotDemoAPI`, containing only what is needed to run the
demo. The development directory remains the source of truth. The demo is
updated only when an explicit sync command is run.

The demo is **feature-complete**, not a stripped-down build:

- live Twilio voice,
- live WhatsApp (inbound and outbound),
- live Salesforce CRM integration,
- the same LLM, knowledge base, prompts, STT/TTS providers, voices, languages,
  and pre-rendered audio as the approved version.

The demo directory will later become its own git repository. This plan does
**not** copy `.git`, does **not** create a worktree, and does **not** link the
demo to the development repository. The sync script only reads the development
tree and writes files into the demo tree.

### Non-goals

- No rebuild or refactor of the chatbot.
- No application code changes to `app/`, `app.py`, or `dashboard.py`.
- No copy of the development `.git`, `.venv`, `.env`, `data/`, `logs/`, or tests.
- No automatic sync on file save.
- No parallel dev + demo stack in v1 (see §8).

---

## 2. Freeze baseline

The freeze is the current `HEAD` of `universityDemo`:

- Commit: `07083aece22774c592d68314a3bc86f8296cff59`
- Subject: `feat(chat): language selection at chat start, localized WhatsApp replies`
- Committed: 2026-10-20 15:09:15 -0700
- Branch at freeze: `fix/voice-grounding-boundary` (1 commit ahead of origin)
- Tracked working tree: clean (only untracked local backups/logs exist)

The sync script records this commit in `.demo_manifest.json` and refuses a
`--sync` when tracked development files are modified, unless
`--allow-dirty-source` is passed. This guarantees the demo is reproducible from
a commit.

---

## 3. Target layout

```text
D:\project\UniversityBotDemoAPI\
├── .demo_directory              # sentinel: "this is the demo directory"
├── .demo_manifest.json          # source commit + SHA-256 of every managed file
├── .gitignore                   # prepared for your future demo git repo
├── .env                         # demo secrets + live integration settings
├── DEMO_VERSION.txt             # human-readable freeze stamp (generated)
├── README_DEMO.md               # the only markdown file in the demo
├── env.demo.example             # sanitized template for .env
├── demo_setup.ps1               # one-time venv + DB + env setup
├── demo_start.ps1               # local or -Live validated start
├── demo_stop.ps1                # restore webhooks, then stop demo PIDs
├── demo_seed.ps1                # guarded synthetic data seed
├── demo_twilio.ps1              # Twilio voice backup / repoint / restore
├── app.py                       # Streamlit chat UI
├── dashboard.py                 # Streamlit admin dashboard
├── requirements.txt
├── app/                         # FastAPI + all runtime modules + static UI
├── content/meridian/meridian_knowledge_base.md
├── scripts/
│   ├── build_call_assets.py
│   ├── seed_demo_data.py
│   └── update_twilio_webhook.py
├── data/                        # empty at setup; demo-owned, never synced
│   ├── documents/
│   └── offers/
├── logs/                        # empty at setup; demo-owned, never synced
└── .venv/                       # created by demo_setup.ps1; never synced
```

`data/`, `logs/`, `.env`, `.venv/`, `.git/`, `.whatsapp_tunnel*`, and
`.twilio_repoint_backup.json` are **demo-owned** and are never touched by sync.

---

## 4. Exact file policy

The policy is **deny-by-default**: a file is copied only if it matches an
explicit include rule. Everything else is excluded automatically.

### 4.1 Included files

| Source | Files | Size | Reason |
|---|---:|---:|---|
| `app/` (all files except `__pycache__`, `*.pyc`) | 123 | 6.50 MB | FastAPI, Streamlit backend, voice, CRM, offers, MCP, static UI; includes 41 pre-rendered WAVs (5.09 MB) |
| `app.py` | 1 | ~50 KB | Streamlit chat UI |
| `dashboard.py` | 1 | ~10 KB | Streamlit admin dashboard |
| `requirements.txt` | 1 | <1 KB | pinned runtime dependencies |
| `content/meridian/meridian_knowledge_base.md` | 1 | 15.6 KB | the only `DOC_PATHS` context file; RAG was removed |
| `scripts/build_call_assets.py` | 1 | 20 KB | regenerate pre-rendered call audio |
| `scripts/seed_demo_data.py` | 1 | 12 KB | synthetic demo data (receives the safety guard) |
| `scripts/update_twilio_webhook.py` | 1 | 4.5 KB | Twilio voice webhook update |
| **Total runtime files** | **130** | **~6.6 MB** | |

Plus 8 new demo support files, copied from templates in
`scripts/demo/` (mapped to the demo root):

| Template | Destination |
|---|---|
| `scripts/demo/demo_setup.ps1` | `demo_setup.ps1` |
| `scripts/demo/demo_start.ps1` | `demo_start.ps1` |
| `scripts/demo/demo_stop.ps1` | `demo_stop.ps1` |
| `scripts/demo/demo_seed.ps1` | `demo_seed.ps1` |
| `scripts/demo/demo_twilio.ps1` | `demo_twilio.ps1` |
| `scripts/demo/env.demo.example` | `env.demo.example` |
| `scripts/demo/README_DEMO.md` | `README_DEMO.md` |
| `scripts/demo/gitignore.demo` | `.gitignore` |

Final initial population: **138 files, ~6.6 MB**. The demo's future git
repository will be small (no `.venv`, no models, no logs, no docs).

### 4.2 Explicitly excluded

| Excluded | Size / count | Reason |
|---|---:|---|
| `.venv/` | 8,034 MB / 63,169 files | recreated by `demo_setup.ps1` |
| `models/` | 74.6 MB / 7 files | **unused by the app**: `WHISPER_MODEL=small` resolves from `~/.cache/huggingface/hub`, not this directory (which contains only `tiny.en`). Verified: no code sets `HF_HOME` or a `download_root` to it. |
| `tests/`, `pytest.ini`, `requirements-dev.txt` | 72+ files | test-only |
| `doc/` | 267 tracked / 366 files | stale docs, perf runs, backup zip |
| `logs/` | 35.2 MB / 109 files | runtime logs |
| `data/` | 7.2 MB / 76 files | uploaded documents and offer letters (sensitive) |
| `content/sample_data/` | 11 files | development corpus, not in `DOC_PATHS` |
| `chroma_local_db/`, `chroma_local_db_pre_c4/` | 10 files | RAG removed from the code |
| `tts_service/` | 5 files | separate Indic TTS service; `TTS_PROVIDER=sarvam` |
| `eval/`, `environmentsetup/`, `.github/`, `.claude/` | small | dev/tooling |
| `Dockerfile`, `docker-compose.yml` | 2 | native demo; PostgreSQL is external |
| Dev launchers (`start_services.*`, `start_demo.bat`, `bootstrap_services.*`, `check_and_tunnel.*`, `launch*`, `setup.sh`, `stop.sh`, `tunnel_streamlit.*`) | ~20 | stale/complex; replaced by the demo scripts |
| Root test fixtures/scripts (`test_*.py`, `test_*.wav`, `test_speech.*`, `run_pipeline_test.py`, `validate_platform_readiness.py`) | ~15 | dev-only |
| `admissions_bot.py`, `quick_call.py` | 2 | legacy Colab entry / CLI duplicate of `/call` |
| `University_Projwect_v1.ipynb` | 1 | notebook |
| `salesforceAPISchema`, `salesforcer_api.txt` | 2 | reference only |
| Stale root markdown (`DELIVERY_COMPLETE.md`, `IMPLEMENTATION_COMPLETE.md`, `PROJECT_COMPLETE.md`, `SETUP_*`, `QUICK_*`, `README_DEPLOYMENT.md`, `DEMO_GUIDE.md`, `TRD_INDEX.md`, etc.) | ~15 | not runtime; the demo gets one fresh README |
| `.env`, `.env.bak*`, `.twilio_repoint_backup.json`, `.whatsapp_tunnel*`, `.tunnel_*`, `.machine_profile.json`, `.pytest_cache/`, `__pycache__/`, `*.pyc` | — | secrets / runtime / dev artifacts |
| `scripts/predeploy.py`, `scripts/verify_*.py`, `scripts/test_foreign_languages.py` | ~10 | dev tooling |

The deny-by-default rule is what guarantees that `.env` and its three backups
cannot leak. A file-walker with a hand-written exclusion list would miss at
least `.env.bak-pre-twilio-restore-20261006T110029`,
`.env.bak-pre-us006`, and `.env.bak.20260919T041845`.

---

## 5. Sync design — `scripts/sync_demo.py`

### 5.1 Command-line interface

```powershell
# Preview only. This is the default when no action flag is given.
python scripts/sync_demo.py --dry-run

# Populate an empty target for the first time (writes sentinel + manifest).
python scripts/sync_demo.py --init --sync

# Normal update.
python scripts/sync_demo.py --sync

# Update and remove managed files that no longer exist in development.
python scripts/sync_demo.py --sync --confirm-deletions

# Show whether new tracked files fall outside the include policy.
python scripts/sync_demo.py --audit

# Advanced overrides.
python scripts/sync_demo.py --sync --force
python scripts/sync_demo.py --sync --allow-dirty-source
python scripts/sync_demo.py --sync --allow-dirty-demo
python scripts/sync_demo.py --dry-run --dest "D:\project\UniversityBotDemoAPI"
```

`--sync` is required for any write. Dry-run is the default. The target defaults
to `D:\project\UniversityBotDemoAPI`; `--dest` exists for testing.

### 5.2 Validation before any write

The script refuses to run if any of these fail:

1. Source root is the parent of `scripts/`; it must contain `app/main.py`,
   `requirements.txt`, and `app.py`.
2. Target is not the source, is not inside the source, and does not contain the
   source.
3. Target is not a drive root or the user's home directory.
4. Target is either empty, or contains the `.demo_directory` sentinel.
   A non-empty directory that looks like another project is refused unless
   `--adopt` is explicitly passed with confirmation.
5. All resolved target paths stay inside the target root. Symlinks and Windows
   junctions are not followed; they are skipped with a warning.
6. Development tracked files are clean (`git status --porcelain
   --untracked-files=no`). `--allow-dirty-source` bypasses this with a warning.
7. If the target is a git repository, its working tree is clean.
   `--allow-dirty-demo` bypasses this with a warning. The sync script never
   runs `git add`, `git commit`, or any other write to the demo repository.

### 5.3 Manifest

`.demo_manifest.json` in the demo root is the source of truth for the last
successful sync:

```json
{
  "manifest_version": 1,
  "policy_version": 1,
  "generated_at_utc": "2026-10-22T00:00:00Z",
  "source_root": "D:\\project\\universityDemo",
  "source_commit": "07083aece22774c592d68314a3bc86f8296cff59",
  "source_commit_subject": "feat(chat): language selection at chat start, localized WhatsApp replies",
  "source_tracked_dirty": false,
  "target_root": "D:\\project\\UniversityBotDemoAPI",
  "status": "clean",
  "files": {
    "app/main.py": {"sha256": "…", "size": 12345},
    "requirements.txt": {"sha256": "…", "size": 456}
  }
}
```

`status` is `clean` when every managed file matches the source, or `partial`
when conflicts or pending deletions remain.

### 5.4 Classification

| State | Condition | Sync action |
|---|---|---|
| `ADD` | not present in demo, not in manifest | copy |
| `UPD` | demo hash differs from source, demo matches manifest | copy |
| `KEEP` | demo hash equals source hash | nothing |
| `CONFLICT` | demo file was edited since the last sync and source also differs | skip, warn; `--force` overwrites |
| `RESTORE` | in manifest but missing from demo | copy |
| `OBSOLETE` | in manifest but no longer in source policy | list; delete only with `--confirm-deletions` |

Deletions never touch a path outside the manifest. An obsolete file that was
edited in the demo additionally requires `--force`.

### 5.5 Write behavior

- Files are copied byte-for-byte; no text rewriting.
- Each file is written to a temporary file in the target directory and then
  `os.replace`-d into place, so an interrupted sync cannot leave a half file.
- Only files in the include policy are written. Nothing is deleted by default.
- The manifest is written last, atomically.
- `DEMO_VERSION.txt` is regenerated with the source commit, date, and file
  count.
- Console categories: `[ADD]`, `[UPD]`, `[KEEP]`, `[RESTORE]`, `[CONFLICT]`,
  `[OBSOLETE]`, `[SKIP]`, `[PROTECTED]`.
- Exit codes: `0` clean/no-op, `1` validation or refusal, `2` partial
  (conflicts or pending deletions).
- The sync log is written to the development tree at
  `logs/sync_demo/<timestamp>.log`; it contains paths and hashes only, no
  secrets.

### 5.6 Audit mode

`--audit` compares `git ls-files` in the development repo with the include
policy and prints:

- tracked files not covered by the policy (e.g. a new runtime script or a new
  content file),
- untracked, non-ignored files that look runtime-related,
- oversized files that would bloat the demo.

This is how we catch a future feature that adds a runtime file outside the
current 130-file set. It is informational unless `--strict` is passed.

### 5.7 Tests

`tests/test_sync_demo.py` (development only, never copied) covers:

- path validation (target inside source is refused),
- allowlist excludes `.env`, `.env.bak*`, `data/`, `logs/`, `tests/`, `doc/`,
- dry-run writes nothing,
- first sync produces exactly the expected file count and manifest,
- second sync is a no-op,
- update / restore / obsolete-deletion / conflict paths,
- protected paths are preserved,
- a dirty demo git tree blocks `--sync`,
- a dirty development tracked tree blocks `--sync`.

Tests use temporary directories, never the real demo target.

---

## 6. Demo runtime design

### 6.1 Environment file — full feature parity

`env.demo.example` is generated from the current `.env` key list, with secrets
blank. `demo_setup.ps1` copies it to `.env` if `.env` does not exist. The sync
script never copies or overwrites `.env`.

Non-secret runtime values are copied **verbatim** from the tested version:

- `FASTAPI_HOST=127.0.0.1`, `FASTAPI_PORT=8000`, `STREAMLIT_PORT=8501`
- `LLM_PROVIDER=auto`, `LLM_MODEL=gpt-6-luna`, `LLM_BASE_URL=https://api.openai.com/v1`
- `LLM_TIMEOUT_S=15`, `LLM_MAX_TOKENS=192`, `LLM_REASONING_EFFORT=none`,
  `LLM_PROMPT_CACHE_KEY=meridian-kb-v1`
- `CONTEXT_MODE=doc`, `DOC_PATHS=content/meridian/meridian_knowledge_base.md`,
  `DOC_MAX_BYTES=2000000`, `DOC_MAX_TOKENS=500000`
- `TTS_PROVIDER=sarvam`, `STT_PROVIDER=sarvam`,
  `STT_FALLBACK_PROVIDERS=indic_conformer,whisper`
- `DEFAULT_LANGUAGE=en`, `SUPPORTED_LANGUAGES=…` (full current list)
- `MAX_CONCURRENT_CALLS=2`, `TURN_TIMEOUT_S=30`, `VAD_SILENCE_MS=600`
- `COMPANY_NAME=Meridian University`, `AGENT_NAME=Alex`, `ADMISSION_ENABLED=true`
- `LOG_LEVEL=INFO`, `LOG_FILE=./logs/voice_assistant.log`
- `CRM_IDLE_WINDOW_HOURS=6`, `CRM_OFFER_DOCUMENT_TYPE=Other`,
  `CRM_OFFER_DOCUMENT_SOURCE=Internal Upload`

Integration settings are **live**, matching development:

- `CRM_ENABLED=true`
- `CRM_OFFER_UPLOAD_ENABLED=true`
- `CRM_DOCUMENT_UPLOAD_ENABLED=true`
- `CRM_BASE_URL=http://127.0.0.1:8098`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`,
  `TWILIO_WHATSAPP_NUMBER` — the same values as development, or demo-specific
  credentials. They live only in `.env`, never in the demo git repository.
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` — the same values as
  development if offer email is part of the demo.

Demo-specific values:

- `DATABASE_URL=postgresql://demo_user:<generated>@127.0.0.1:<published-port>/admissions_demo`
  (the setup script detects the `elearning-postgres` published host port; it is
  **9200** on this machine, not 5432)
- `LLM_API_KEY=`, `SARVAM_API_KEY=` — use the same keys, or dedicated demo keys
  that access the same model/voice. A separate key changes billing, not quality.
- `BACKEND_BASE=http://127.0.0.1:8000` (avoids the `localhost` IPv6 stall in
  the streamlit backend helper)

No `HF_HOME` override: the demo uses the machine's existing HuggingFace cache,
which contains the exact `small` whisper and IndicConformer weights.

### 6.2 Database isolation

Use the existing PostgreSQL 16 container (`elearning-postgres`). Its container
port 5432 is published on host port **9200** on this machine; `demo_setup.ps1`
detects that port with `docker port` rather than assuming 5432. Use a **new
database and a new least-privilege role**:

```sql
CREATE ROLE demo_user LOGIN PASSWORD '<generated>';
CREATE DATABASE admissions_demo OWNER demo_user;
-- then, connected to admissions_demo:
GRANT ALL ON SCHEMA public TO demo_user;
```

`demo_setup.ps1 -InitDb` runs this via `docker exec` (the container's
`elearning` user is the superuser), generates a random password, and writes the
DSN into `.env`. It never drops or resets anything. If Docker is unavailable,
it prints the SQL for manual execution.

Three independent guards prevent a dev-DB accident:

1. The demo DSN uses `demo_user`, which has no table privileges on the live
   development database (`universcity`); a mistaken connection fails at schema
   init/write.
2. `demo_start.ps1` parses the DSN and refuses to start unless the database
   name is exactly `admissions_demo`.
3. `seed_demo_data.py` is modified to print the target host/database, require
   `--yes`, and refuse any database name that does not end in `_demo`.

There are no Alembic migrations. Schema creation is `init_db()` at FastAPI
startup; `demo_setup.ps1` calls it explicitly once after the database is
created. No destructive database operation is performed by setup.

### 6.3 `demo_setup.ps1`

One-time setup:

1. Verify Python 3.11 and create `.venv`.
2. Install `requirements.txt`. On an NVIDIA machine, install
   `torch==2.7.1+cu128` from the PyTorch index first, matching
   `requirements.txt`'s GPU note; otherwise use the PyPI build.
3. `pip check`.
4. Create `data/documents`, `data/offers`, `logs`.
5. Create `.env` from `env.demo.example` if absent; print which keys must be
   filled (never print values).
6. `-InitDb`: create `admissions_demo` + `demo_user`, write the DSN into `.env`.
7. Run `init_db()` once with the demo environment loaded.
8. Verify the external caches:
   - `~/.cache/huggingface/hub/models--Systran--faster-whisper-small`
     (463.7 MB),
   - `models--OpenVoiceOS--ai4bharat-indicconformer-hi-onnx` (131.3 MB),
   - `models--OpenVoiceOS--ai4bharat-indicconformer-gu-onnx` (131.3 MB),
   - `~/.cache/pipecat/kokoro-onnx/` (353.7 MB, non-fatal because
     `TTS_PROVIDER=sarvam`).
   Warn if missing; the first warmup downloads HF models if internet is
   available.
9. Verify `ffmpeg` is on PATH; warn if missing.
10. `python -c "import app.main"` as an import smoke test.
11. Print the remaining manual step (fill API keys) and the start command.

The venv is ~8 GB. It is created once and is excluded from the demo git
repository.

### 6.4 `demo_start.ps1`

Two modes:

- `.\demo_start.ps1` — local UI only: FastAPI, Streamlit chat, optional
  dashboard. No tunnel, no Twilio webhook change, no WhatsApp change.
- `.\demo_start.ps1 -Live` — full client demo: tunnel, Twilio voice repoint,
  CRM health check, WhatsApp manual instructions. This is the documented
  command for stakeholders.

Steps:

1. Parse `.env` without printing secrets.
2. Refuse unless `DATABASE_URL` resolves to `admissions_demo`.
3. Verify `CRM_ENABLED` is the literal string `true`.
4. Verify `.venv\Scripts\python.exe`.
5. Check ports 8000, 8501, and 8502; refuse if occupied; print the owner
   PID/process. Never kill a process.
6. Verify the Salesforce Admission API at `http://127.0.0.1:8098/health`.
   If it is not running and `-StartCrm` is passed, start it from
   `D:\project\salesforce\salesforce-admission-api` using its own venv so it
   loads its own `.env`. Never start it from the demo directory.
7. Start FastAPI (`app.main:app`), Streamlit chat (`app.py`), and optionally
   the dashboard (`dashboard.py`), writing logs to `logs/`.
8. Poll `http://127.0.0.1:8000/` and the Streamlit health endpoint until ready
   (timeout 60 s), then open `http://127.0.0.1:8501`.
9. Write `.demo_pids.json` for a safe stop.
10. `-Live` only:
    - start `cloudflared tunnel --url http://127.0.0.1:8000`,
    - capture the URL and write `.whatsapp_tunnel` in the demo directory,
    - call `demo_twilio.ps1 -Enable -TunnelUrl <host>` to back up and repoint
      the voice webhook and status callback,
    - print the WhatsApp Sandbox URL to set manually:
      `https://<host>/twilio/whatsapp`.
11. Optional `-TunnelUi` starts a second tunnel to 8501 for remote viewers.

### 6.5 `demo_stop.ps1`

1. If `.twilio_repoint_backup.json` exists, call
   `demo_twilio.ps1 -Restore` first, so the development voice webhook is
   restored even if a demo process fails to stop.
2. Print the development WhatsApp webhook URL to restore in the Twilio Console.
3. Stop the Cloudflare tunnel started by the demo.
4. Read `.demo_pids.json`, verify each PID still belongs to a process started
   from the demo root (via `Get-CimInstance Win32_Process`), and stop only those
   processes. Never kill whatever happens to own port 8000.
5. If the demo started the CRM API (`-StartCrm`) and it is recorded in the PID
   file, stop it too; otherwise leave the already-running CRM API alone.

### 6.6 `demo_seed.ps1` and the seed guard

`demo_seed.ps1` loads `.env`, requires `admissions_demo`, and runs:

```powershell
python scripts/seed_demo_data.py --yes
```

`scripts/seed_demo_data.py` is changed (**development tool only, not imported
by the application**) to:

- load `.env` from the project root,
- print the target host/database (never the password),
- refuse if the database name does not end in `_demo`,
- require `--yes` before deleting existing demo rows,
- run the deletes and inserts in one transaction, so a failed seed rolls back
  instead of leaving the demo database empty.

Effect on existing workflows:

- Demo seeding produces the same synthetic data.
- Running `python scripts/seed_demo_data.py` without `--yes` now refuses
  instead of wiping a database.
- Intentional development seeding requires an explicit override
  (`--allow-dev-db`); accidental seeding does not.
- Application runtime is unaffected because the app never imports this script.

### 6.7 Twilio voice and WhatsApp — live, one stack at a time

**Voice number** ends `8953`; **WhatsApp number** ends `8886` (Twilio Sandbox).

`demo_twilio.ps1 -Enable -TunnelUrl <host>`:

1. Read the current voice number configuration:
   `voice_url`, `voice_method`, `status_callback`,
   `status_callback_method`.
2. Save it to `.twilio_repoint_backup.json` in the demo root. If a backup
   already exists, keep it; it is the restore point.
3. Point the voice webhook to `https://<host>/twilio/voice` (GET) and the
   status callback to `https://<host>/twilio/outbound/status` (POST).
4. Verify by reading the values back.
5. Print the WhatsApp Sandbox step:
   `https://<host>/twilio/whatsapp` (HTTP POST), to be pasted in the Twilio
   Console. The sandbox webhook has no Twilio API.

`demo_twilio.ps1 -Restore`:

1. Read the saved values.
2. Restore the voice webhook, method, status callback, and method.
3. Verify by reading them back.
4. Do not delete the backup; leave it for inspection.

`demo_twilio.ps1 -Status` shows current values vs the backup.

WhatsApp has one sandbox webhook per account and it is console-only. The demo
script prints the demo URL before the demo and the development URL after it.
Dev and demo cannot both receive WhatsApp messages at the same time.

### 6.8 CRM — live, same Salesforce org

- `CRM_ENABLED=true` and the same CRM flags as development.
- `CRM_BASE_URL=http://127.0.0.1:8098` — the Salesforce Admission API is a
  separate process pointing at the Salesforce org through OAuth.
- The demo does not start a second CRM API instance. A second instance would
  still write to the same Salesforce org, so it would not isolate data.
- Demo-created leads and documents are written into the same Salesforce
  sandbox/org as development/test data. If the org is a sandbox, production is
  untouched, but demo and dev records are mixed.
- Full isolation requires a separate Salesforce sandbox/org and separate
  credentials — a separate Salesforce workstream.
- The CRM service must be started from its own directory because it calls
  `load_dotenv()` with no path. `demo_start.ps1 -StartCrm` handles that.
- Validation: create a test lead with a unique demo phone number and verify it
  appears in Salesforce through the CRM API. If Salesforce is unreachable,
  report the check as blocked, not passed.

### 6.9 Quality parity

| Component | Demo behavior |
|---|---|
| `app/`, `app.py`, `dashboard.py` | byte-identical to commit `07083ae` |
| Knowledge base | exact `content/meridian/meridian_knowledge_base.md` |
| Prompts | same code, unchanged |
| LLM | same model, base URL, max tokens, reasoning effort, cache key |
| STT | same `sarvam` primary + `indic_conformer,whisper` fallback |
| STT weights | same HuggingFace cache (`faster-whisper-small`, IndicConformer hi/gu) |
| TTS | same `sarvam` provider, voices, pace, language settings |
| Audio | same 41 pre-rendered WAVs |
| CRM / Twilio / WhatsApp | enabled, same flags, same numbers, same code paths |
| Database schema | same `init_db()` schema |
| Tests/docs/logs | removed; no runtime effect |

The only functional differences are infrastructure wiring: the database name,
secrets, the tunnel URL, and the webhook target during the demo. None of them
changes answer quality or feature behavior.

`models/` is excluded because it is unused; the active `small` model comes from
the machine's HuggingFace cache. If the demo is later moved to another machine,
bundle the three HF cache directories and set `HF_HOME` (about 726 MB). Kokoro
(353.7 MB) is only a pre-warm/fallback because `TTS_PROVIDER=sarvam`.

### 6.10 External dependencies the demo still shares

- the existing `elearning-postgres` container (separate database),
- the Salesforce Admission API at `127.0.0.1:8098` and its Salesforce org,
- the machine's HuggingFace cache (whisper small + IndicConformer hi/gu),
- `~/.cache/pipecat/kokoro-onnx/` for the non-fatal Kokoro pre-warm,
- the OpenAI-compatible LLM endpoint and Sarvam API,
- `ffmpeg`, and `cloudflared` for live Twilio/WhatsApp.

This is intentional: duplicating 8 GB of Python packages and several model
caches would make the demo harder to maintain, not more reliable.

---

## 7. Demo git workflow (yours)

The demo starts as a plain directory. When you want version history:

```powershell
cd D:\project\UniversityBotDemoAPI
git init
git add .
git commit -m "Demo freeze 2026-10-22 (dev 07083ae)"
```

From then on, the sync script:

- detects `.git` and requires a clean working tree before `--sync`,
- never copies, modifies, or deletes `.git`,
- never commits for you.

Your update loop:

1. Develop and test in `universityDemo`.
2. Commit the tested change in `universityDemo`.
3. `python scripts/sync_demo.py --dry-run` from `universityDemo`.
4. Review the `ADD` / `UPD` / `OBSOLETE` / `CONFLICT` list.
5. Commit the current demo state in `UniversityBotDemoAPI` (the old demo is now
   preserved in its own history).
6. `python scripts/sync_demo.py --sync` (add `--confirm-deletions` if the
   dry-run lists obsolete files).
7. Commit the new demo state in `UniversityBotDemoAPI`.
8. Run `demo_start.ps1 -Live` and validate.

`.gitignore` for the demo:

```gitignore
.env
.venv/
__pycache__/
*.pyc
data/
logs/
.demo_pids.json
.demo_tmp/
.whatsapp_tunnel
.tunnel_*
.twilio_repoint_backup.json
```

`app/static/audio/*.wav` and `README_DEMO.md` are committed. The demo repo is
about 6.6 MB.

---

## 8. Ports and concurrency

**Same ports as development — 8000 (FastAPI), 8501 (chat), 8502 (dashboard) —
one stack at a time.** This is confirmed.

`demo_start.ps1` refuses to start if those ports are occupied. This keeps the
runtime byte-identical: `app.py` contains hardcoded
`http://localhost:8000` call sites (`app.py:89`, `:234`, `:312`, `:1079`), so
different demo ports would require a code change and re-test.

Because one voice number and one WhatsApp sandbox webhook exist, dev and demo
cannot both receive inbound calls/messages at the same time. The workflow is:

1. Stop the development stack.
2. Start the demo with `-Live` (repoints Twilio voice, prints WhatsApp URL).
3. Demonstrate.
4. Stop the demo (`demo_stop.ps1` restores the voice webhook).
5. Restore the WhatsApp webhook in the Twilio Console.
6. Restart development.

If parallel operation is ever required, the optional phase is:

- add `BACKEND_BASE` support to the four `app.py` call sites,
- run the demo on 8100/8601/8602,
- buy a second Twilio voice number for the demo,
- use a second WhatsApp sender/Messaging Service for the demo.

That is a separate, tested change.

---

## 9. Implementation phases and gates

### Phase 0 — Pre-flight
- Confirm `UniversityBotDemoAPI` is empty and not a git repo. (Verified.)
- Confirm freeze commit `07083ae`, clean tracked tree. (Verified.)
- Confirm PostgreSQL container is running and Python 3.11 is available.
- Confirm ~8 GB free for the demo venv. (D: has ~219 GB free.)
- Confirm the CRM API at 8098 is healthy.

**Gate:** all checks pass or are reported as blocked.

### Phase 1 — Sync engine
- Write `scripts/sync_demo.py` with the policy, validation, manifest,
  classification, audit, and logging described above.
- Write `tests/test_sync_demo.py`.

**Gate:** tests pass; a dry-run against the empty target lists exactly the 130
runtime files + 8 support files.

### Phase 2 — Demo support templates
- Write `scripts/demo/demo_setup.ps1`, `demo_start.ps1`, `demo_stop.ps1`,
  `demo_seed.ps1`, `demo_twilio.ps1`, `env.demo.example`, `README_DEMO.md`,
  `gitignore.demo`.
- PowerShell parse check on every `.ps1`.

**Gate:** all scripts parse; no secret literals in templates.

### Phase 3 — Seed safety
- Modify `scripts/seed_demo_data.py` with the target print, `_demo` name
  guard, `--yes` requirement, and transaction.
- Test the refusal path first (database name `universcity` must be refused
  without executing any DELETE).

**Gate:** refusal verified before any demo data is written.

### Phase 4 — Initial population
- Create `admissions_demo` and `demo_user`.
- Run `python scripts/sync_demo.py --init --sync`.
- Verify file count, manifest, and that no `.env`, `.env.bak*`, `data/`,
  `logs/`, `tests/`, or `doc/` exists in the demo.

**Gate:** 138 files, ~6.6 MB, no secrets, manifest records `07083ae`.

### Phase 5 — Demo virtual environment
- Run `demo_setup.ps1` to create `.venv` and install dependencies.
- Run `pip check` and `python -c "import app.main"`.

**Gate:** import succeeds; any install failure is reported with the exact
package and error, not hidden.

### Phase 6 — Runtime and integration validation
- Start the demo stack; check FastAPI `/`, Streamlit health, and the browser
  voice page.
- Run one text chat request (requires the LLM key).
- Run one voice turn (requires the Sarvam key and audio).
- CRM: check `/health` on 8098, create a test lead, verify it in Salesforce.
- Twilio voice: start `-Live`, verify the webhook/status callback point at the
  demo tunnel, place a test call if a phone is available, then restore and
  verify the old values.
- WhatsApp: set the sandbox webhook manually, send a test message, receive a
  reply, then restore the development URL.
- Compare non-secret `.env` values with development; only the database,
  secrets, and tunnel/webhook values may differ.

**Gate:** passed checks recorded; blocked checks named honestly.

### Phase 7 — Demo git handoff
- Write `README_DEMO.md` instructions for `git init` and the update loop.
- Confirm the sync script refuses a dirty demo tree when `.git` exists.

**Gate:** the user can commit the initial freeze and run a dry-run update.

### Phase 8 — Final report
- Produce a validation report in the **development** tree only (not copied to
  the demo): passed, failed, blocked, and the exact commands.

---

## 10. Validation checklist

| Check | Method | Expected |
|---|---|---|
| Target is empty / not git | filesystem | 0 entries, no `.git` |
| Source commit recorded | manifest | `07083ae…` |
| File count / size | `Get-ChildItem` | 138 files, ~6.6 MB |
| No dev secrets | search demo for `.env`, `.env.bak*`, non-empty `TWILIO_AUTH_TOKEN=` | none |
| No dev data | `data/documents`, `data/offers` | empty directories only |
| No tests/docs/logs | `Test-Path tests`, `doc`, `logs` | `tests`/`doc` absent; `logs` empty |
| Imports resolve | `.venv\Scripts\python -c "import app.main"` | exit 0 |
| DB target | parse `.env` | `admissions_demo` |
| DB schema | `init_db()` | returns true |
| Seed guard | run with `universcity` | refused, no DELETE executed |
| Dev DB untouched | row counts before/after | unchanged |
| FastAPI health | `GET http://127.0.0.1:8000/` | 200 |
| Streamlit health | `GET http://127.0.0.1:8501/_stcore/health` | 200 |
| Chat flow | one text query | grounded answer |
| Voice page | `GET /voice` | 200, page loads |
| CRM API | `GET http://127.0.0.1:8098/health` | 200 |
| CRM enabled | effective `.env` + startup log | `CRM_ENABLED=true` |
| CRM write | create a test lead, query Salesforce | record exists |
| Twilio repoint | `-Live` then read back | voice/status URLs match demo tunnel |
| Twilio restore | `demo_stop.ps1` then read back | original values restored |
| WhatsApp inbound | manual sandbox switch + test message | reply received |
| WhatsApp restore | console step | development URL restored |
| Quality parity | diff non-secret `.env` keys | only DB/secrets/tunnel differ |
| Sync dry-run | first run | lists exactly the expected set |
| Sync no-op | second run | 0 changes |
| Update path | touch one source file, dry-run | `UPD` for that file only |
| Deletion path | remove a test file, dry-run | `OBSOLETE`; not deleted without flag |
| Conflict path | edit a demo managed file | `CONFLICT`; not overwritten without `--force` |
| Dirty demo git | uncommitted demo change | `--sync` refused |
| Dirty dev git | uncommitted tracked dev change | `--sync` refused |

---

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Demo accidentally connects to `universcity` | dedicated `demo_user`, exact DB-name check in launcher, `_demo` check in seed script |
| Seed script wipes development data | `--yes` + `_demo` guard + transaction; refusal tested first |
| `.env.bak*` / tunnel backups leak into demo | deny-by-default policy; explicit secret-pattern skip; validation search |
| Old demo overwritten with no history | clean-demo-git check; dry-run first; demo-owned `.git` |
| Hand-edited demo files overwritten | SHA-256 conflict detection; `--force` required |
| New runtime file missed by the policy | `--audit` lists unclassified tracked files |
| CRM records mix with dev/test data | Accepted: same Salesforce org. Production is untouched if it is a sandbox. A separate sandbox is the future isolation option. |
| WhatsApp webhook left pointing at demo | `demo_stop.ps1` prints the dev URL; manual console restore is documented |
| Twilio voice webhook left pointing at demo | `demo_stop.ps1` runs `-Restore` first; `demo_twilio.ps1 -Restore` is the manual fallback |
| Dev and demo both receive calls | One number, one sandbox webhook; one stack at a time |
| CRM API started from wrong directory | `-StartCrm` uses its own working directory; never starts from the demo root |
| 8 GB venv install failure | proven torch cu128 install order; `pip check`; exact failure reported |
| Model cache missing on another machine | setup warns; first warmup downloads; optional bundled HF cache is documented |
| Same ports block simultaneous dev + demo | accepted; optional port decoupling phase later |
| Frozen runtime drifts from tested commit | source commit recorded; no `app/` changes; sync refuses a dirty tracked tree |

---

## 12. Final decisions

1. **Target:** `D:\project\UniversityBotDemoAPI` — not `D:\project\demo-chatbot`.
2. **Ports:** same 8000/8501/8502; one stack at a time; no `app.py` changes.
3. **CRM:** same Salesforce org via the existing API at `:8098`; demo and dev
   records mix in that org. Production is untouched if it is a sandbox.
4. **WhatsApp:** Twilio Sandbox manual webhook switch before/after each demo.
   No second WhatsApp sender in v1.
5. **Twilio voice:** same number, webhook backed up and repointed by
   `demo_start.ps1 -Live`, restored by `demo_stop.ps1`.
6. **Models:** use the machine's HuggingFace/Kokoro caches; `models/` stays
   excluded. No quality change.
7. **Seed script:** guard the development script (target print, `--yes`,
   `_demo` name check, transaction). No app runtime effect.
8. **Demo git:** not connected; the demo will get its own repository later.
   Sync respects a clean demo git tree and never touches `.git`.
9. **Runtime code:** byte-identical to commit `07083ae`. Only development
   tooling changes and new demo scripts are added.

---

## 13. Exact commands after implementation

```powershell
# From the development directory
cd D:\project\universityDemo

# Preview
python scripts/sync_demo.py --dry-run

# First population
python scripts/sync_demo.py --init --sync

# Normal update
python scripts/sync_demo.py --sync

# Update with deletion confirmation
python scripts/sync_demo.py --sync --confirm-deletions

# From the demo directory
cd D:\project\UniversityBotDemoAPI
.\demo_setup.ps1 -InitDb
# edit .env with the demo LLM/Sarvam keys
.\demo_seed.ps1 -Confirm

# Local UI demo (no Twilio/WhatsApp webhook changes)
.\demo_start.ps1

# Full client demo (tunnel + Twilio voice repoint + WhatsApp instructions)
.\demo_start.ps1 -Live
# optionally start the CRM API if it is not already running
.\demo_start.ps1 -Live -StartCrm

# Stop and restore the development webhooks
.\demo_stop.ps1

# Manual Twilio restore if a demo crashed
.\demo_twilio.ps1 -Restore
.\demo_twilio.ps1 -Status
```
