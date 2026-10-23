# Separate Demo — Update Guide

**Audience:** a future developer or AI agent asked to change the client demo.
**Last verified:** 2026-10-23, development commit `356ca29`.
**Demo folder:** `D:\project\UniversityBotDemoAPI`
**Development source:** `D:\project\universityDemo`

Read this guide before changing anything. Also read:

- `D:\project\UniversityBotDemoAPI\README_DEMO.md` — operator-facing setup/start/stop guide.
- `D:\project\universityDemo\DEMO_FREEZE_STATUS.md` — current verified state and open items.
- `D:\project\universityDemo\DEMO_FREEZE_PLAN.md` — original design decisions.
- `D:\project\universityDemo\DEMO_FREEZE_VALIDATION.md` — last validation run.
- `D:\project\universityDemo\scripts\sync_demo.py` — the updater (policy is in this file).

Do **not** hand-edit files in the demo folder for a development change. Edit the
development source, commit it, and run the sync. Hand-edited demo files cause a
conflict on the next sync and are overwritten only with `--force`.

---

## 1. What the demo is

The demo is a standalone, runnable copy of the client-approved application. It is
feature-complete, not a stripped build: same application code, prompts, knowledge
base, LLM settings, STT/TTS providers, voices, and pre-rendered audio.

What differs from development:

| Item | Development | Demo |
|---|---|---|
| Folder | `D:\project\universityDemo` | `D:\project\UniversityBotDemoAPI` |
| Git | full development history | **no git repo yet** (user will initialize) |
| Python env | development `.venv` | own `.venv` (7.5+ GB) |
| Database host | `localhost:5432` | `127.0.0.1:9200` |
| Database container | `universitydemo-postgres` | `elearning-postgres` |
| Database name | `universcity` | `admissions_demo` |
| Database user | `elearning` | `demo_user` |
| Data volume | `universitydemo_postgres_data` | `e-learning-backend_pgdata` |
| Secrets | development `.env` | demo `.env` (demo-owned) |
| Tests/docs/logs/data | present | excluded |

The two databases are completely separate: different containers, volumes,
databases, and users. The demo seed script refuses any database whose name does
not end in `_demo`, so it cannot touch `universcity`.

Shared state (not isolated):

- **CRM** — both use the same Salesforce org through the API at
  `http://127.0.0.1:8098`. Demo and development records mix in that org.
- **Twilio** — the same account and voice number. `demo_start.ps1 -Live`
  repoints the voice webhook to the demo tunnel and `demo_stop.ps1` restores it.
- **Machine ports** — both stacks use 8000/8501/8502. Run one at a time.

---

## 2. Snapshot (re-verify before acting)

This is the state at the last verification. Treat it as a starting point, not
as live truth.

| Fact | Value |
|---|---|
| Development HEAD at last sync | `356ca29` |
| Demo manifest | `status: clean`, `source_tracked_dirty: false` |
| Managed files | 138 (about 6.7 MB) |
| Demo runtime | `app/`, `app.py`, `dashboard.py` byte-identical to the approved source |
| Validation | 37 PASS / 0 BLOCKED / 0 WARN |
| Demo DB | `admissions_demo`, seeded (12 leads, 8 conversations, 5 follow-ups, 14 courses) |
| Demo runtime state | stopped; ports free; Postgres container healthy |

Re-verify with:

```powershell
cd D:\project\universityDemo
git rev-parse HEAD
python scripts\sync_demo.py --dry-run

$m = Get-Content D:\project\UniversityBotDemoAPI\.demo_manifest.json -Raw | ConvertFrom-Json
$m.source_commit; $m.source_tracked_dirty; $m.status; @($m.files.PSObject.Properties).Count
```

If `source_tracked_dirty` is `true`, the manifest does not describe a clean
commit. Commit the development change and sync again.

---

## 3. Standard update workflow

Use this for a normal feature: existing files, or new files under `app/`.

```powershell
# 1. Make and test the change in development
cd D:\project\universityDemo

# 2. Commit it. The sync refuses a dirty tracked tree.
git add <paths>
git commit -m "..."

# 3. Preview exactly what the demo will receive
python scripts\sync_demo.py --dry-run

# 4. Apply
python scripts\sync_demo.py --sync

# 5. If files were deleted or renamed in development
python scripts\sync_demo.py --sync --confirm-deletions

# 6. Validate the demo (stack must be running; see section 7)
cd D:\project\UniversityBotDemoAPI
.\demo_start.ps1 -StartCrm
cd D:\project\universityDemo
python scripts\validate_demo.py --check-http --report DEMO_FREEZE_VALIDATION.md
```

Then update `DEMO_FREEZE_STATUS.md` with the new commit, file count, and
validation result, and commit that update.

Important:

- **Commit before syncing.** The dirty check only looks at tracked files
  (`git status --untracked-files=no`). A new untracked file under `app/` would
  be copied while the manifest claims an older commit, breaking reproducibility.
- **Do not edit the demo copy directly.** Edit `scripts/demo/*` templates or
  development files, then sync.
- **One source of truth.** `D:\project\universityDemo` is authoritative; the
  demo is a generated artifact plus demo-owned runtime state.

---

## 4. What the sync manages

Policy is deny-by-default in `D:\project\universityDemo\scripts\sync_demo.py`:

| Constant | Meaning |
|---|---|
| `INCLUDE_FILES` | explicit source files copied to the same relative path |
| `INCLUDE_DIRS` | directories copied recursively (`app` only) |
| `DEMO_TEMPLATES` | demo support files: source template -> demo root destination |
| `POLICY_VERSION` | sentinel compatibility version |

Current explicit files:

```text
requirements.txt
app.py
dashboard.py
content/meridian/meridian_knowledge_base.md
scripts/build_call_assets.py
scripts/seed_demo_data.py
scripts/update_twilio_webhook.py
```

Current recursive directory: `app/` → `app/` (excludes `__pycache__`, `*.pyc`,
`.git`, `.venv`, etc.).

Demo templates:

```text
scripts/demo/demo_setup.ps1      -> demo_setup.ps1
scripts/demo/demo_start.ps1      -> demo_start.ps1
scripts/demo/demo_stop.ps1       -> demo_stop.ps1
scripts/demo/demo_seed.ps1       -> demo_seed.ps1
scripts/demo/demo_twilio.ps1     -> demo_twilio.ps1
scripts/demo/env.demo.example    -> env.demo.example
scripts/demo/README_DEMO.md      -> README_DEMO.md
scripts/demo/gitignore.demo      -> .gitignore
```

Never synced (demo-owned or excluded):

```text
.env, .env.*, .venv/, data/, logs/, .git/,
tests/, doc/, models/, __pycache__/, *.pyc,
.demo_pids.json, .whatsapp_tunnel, .twilio_repoint_backup.json
```

`doc/` is excluded, so this guide stays in development only. That is why a
future AI must read it here, not in the demo folder.

### New files

- **Under `app/`** — automatic. The recursive copy picks them up.
- **Top-level file, new directory, new content file, new runtime script** — not
  automatic. Add it to `INCLUDE_FILES`, `INCLUDE_DIRS`, or `DEMO_TEMPLATES`,
  then commit and sync.
- Run the audit to find omissions:

```powershell
python scripts\sync_demo.py --audit
python scripts\sync_demo.py --audit --strict   # exit 1 on runtime-looking files
```

The audit examines **tracked** files only, so commit new files first.

### Policy version caveat

If you change the policy and bump `POLICY_VERSION`, the existing
`.demo_directory` sentinel still records the old version and sync will refuse.
Either edit `policy_version` in `D:\project\UniversityBotDemoAPI\.demo_directory`
manually, or delete the sentinel and run:

```powershell
python scripts\sync_demo.py --init --sync --adopt
```

`--adopt` is a safety override; only use it when the target is definitely the
demo directory. Additive policy changes can keep `POLICY_VERSION = 1` if you do
not need the compatibility guard.

---

## 5. Change matrix

| Change in development | Extra action beyond normal sync |
|---|---|
| Code in an existing `app/` file | None |
| New file under `app/` | None |
| New top-level file or directory | Add to the policy in `sync_demo.py` |
| New knowledge-base / content file | Add to `INCLUDE_FILES` (or a new include dir) |
| New runtime script under `scripts/` | Add to `INCLUDE_FILES` |
| `requirements.txt` changed | Reinstall in the demo venv (section 6) |
| New table/column or schema change | Apply `init_db()` in the demo DB (section 6) |
| New `.env` key | Update `scripts/demo/env.demo.example`, then add it to the demo `.env` manually |
| File deleted or renamed | `--sync --confirm-deletions` |
| Demo-only tweak | Do not edit the demo copy; put it in the template/source and sync |
| Demo support script changed | Edit `scripts/demo/*`, commit, sync |
| Twilio/WhatsApp behavior changed | Update `demo_start.ps1`/`demo_twilio.ps1`/README and re-test live |

---

## 6. Dependencies, schema, and environment

### Python dependencies

`requirements.txt` is synced, but the demo `.venv` is not reinstalled by the
sync. Reinstall from the demo folder:

```powershell
cd D:\project\UniversityBotDemoAPI
.\demo_setup.ps1
```

`demo_setup.ps1` creates the venv if missing and always runs
`pip install -r requirements.txt` (unless `-SkipVenv`). On this machine the
NVIDIA torch cu128 wheel is installed first.

### Database schema

The app's schema modules are idempotent (`CREATE TABLE IF NOT EXISTS`,
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`). Apply them to the demo DB with:

```powershell
cd D:\project\UniversityBotDemoAPI
.\demo_setup.ps1 -InitDb
```

Caveat: `-InitDb` also generates a **new** `demo_user` password and rewrites
`DATABASE_URL` in the demo `.env`. That is fine for the demo, but any external
client using the old password breaks.

To apply schema changes **without** rotating the password, run the same
`init_db()` call directly:

```powershell
cd D:\project\UniversityBotDemoAPI
.\.venv\Scripts\python.exe -c "from dotenv import load_dotenv; load_dotenv('.env'); import asyncio; from app.database import init_db; raise SystemExit(0 if asyncio.run(init_db()) else 1)"
```

### Environment variables

- The demo `.env` is **never** overwritten by sync.
- `env.demo.example` is synced from `scripts/demo/env.demo.example`; update the
  template there.
- After adding a key, add it to `D:\project\UniversityBotDemoAPI\.env` manually.
- The validator checks that non-allowed keys present in development are present
  in the demo, and that non-allowed values match development. A missing key
  fails validation.
- Secrets (`LLM_API_KEY`, `SARVAM_API_KEY`, `TWILIO_*`, `SMTP_*`) are allowed to
  differ and are never copied. Do not paste development secrets into the demo
  unless that is intentional.
- `CRM_ENABLED` and `MCP_ENABLED` accept only the literal lowercase string
  `true`; `True`/`1` are treated as off. Check `.env`, not `config.py` defaults.

---

## 7. Start, stop, and live demo

The launcher is `D:\project\UniversityBotDemoAPI\demo_start.ps1`. There is no
`start_services.ps1` in the demo folder.

| Command | What runs |
|---|---|
| `.\demo_start.ps1` | FastAPI 8000, Streamlit chat 8501, dashboard 8502. No tunnel, no Twilio change |
| `.\demo_start.ps1 -StartCrm` | same, plus CRM API 8098 |
| `.\demo_start.ps1 -Live -StartCrm` | full client demo: FastAPI tunnel, Twilio voice repoint, WhatsApp Sandbox URL, CRM |
| `.\demo_start.ps1 -Live -StartCrm -TunnelUi` | same, plus public tunnels for chat 8501 and dashboard 8502; matches development's `.\start_services.ps1 -WithStreamlit` |
| `.\demo_start.ps1 -Live -StartCrm -NoDashboard` | full demo without the dashboard or its tunnel |

`Get-Help .\demo_start.ps1 -Examples` prints the same list.

### Live behavior

`-Live`:

1. Starts a Cloudflare quick tunnel to FastAPI and prints its URL.
2. Backs up the current Twilio voice webhook and status callback to
   `.twilio_repoint_backup.json`.
3. Repoints them at `https://<tunnel>/twilio/voice` and
   `https://<tunnel>/twilio/outbound/status`.
4. Prints the WhatsApp Sandbox webhook URL:
   `https://<tunnel>/twilio/whatsapp` (HTTP POST).
5. `-TunnelUi` starts two more quick tunnels for 8501 and 8502.

WhatsApp is **manual**: Twilio exposes no API to change the Sandbox webhook.
Set it in the Twilio Console before the demo and restore it afterwards.
`demo_stop.ps1` prints the development URL to restore.

Stop:

```powershell
cd D:\project\UniversityBotDemoAPI
.\demo_stop.ps1
```

This restores the voice webhook first, prints the WhatsApp restore URL, then
stops the FastAPI/Streamlit/dashboard/CRM process trees and both UI tunnels. It
never kills an unrelated process that happens to own a port.

### One stack at a time

Development and demo share 8000/8501/8502. Stop the development stack first.
`demo_start.ps1` refuses to start if a port is occupied and reports the owner;
it does not kill it.

---

## 8. Validation

With the demo stack running:

```powershell
cd D:\project\universityDemo
python scripts\validate_demo.py --check-http --report DEMO_FREEZE_VALIDATION.md
```

Expected on a complete demo: **37 PASS / 0 BLOCKED / 0 WARN**.

Static checks (file policy, manifest hashes, secrets, DB name, env parity) run
without the stack. `--check-http` adds FastAPI, Streamlit, and CRM probes.

Health endpoints:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
Invoke-WebRequest http://127.0.0.1:8501/_stcore/health
Invoke-RestMethod http://127.0.0.1:8098/health
```

---

## 9. Troubleshooting and known traps

| Symptom / trap | Cause and action |
|---|---|
| Cannot connect to demo DB | The container publishes Postgres on host port **9200**, not 5432. Container internal port is also 5432, which is why `inet_server_port()` reports 5432. Check `docker port elearning-postgres 5432/tcp` |
| Docker errors | Start Docker Desktop; `elearning-postgres` must be healthy. The dev DB is a different container on 5432 |
| `Port 8000 is already in use` | Development stack or another process owns it. Stop that stack; do not kill blindly |
| CRM warning about outbox | CRM API is not running. Pass `-StartCrm` or start it from `D:\project\salesforce\salesforce-admission-api` |
| `/ready` says `not_ready` right after start | Boot-time race with the remote LLM probe. Restart with the keys present; verified `ready=true` after restart. `?refresh=1` does not clear the carried residency clause |
| Readiness shows Postgres 5432 | `app/boot_readiness.py` hardcodes the 5432 probe. The demo DB is on 9200; the probe only passes because another Postgres listens on 5432. Latent false alarm, not an active failure. Runtime is frozen, so do not change app code for this |
| `psycopg2: DLL load failed ... Application Control` | Smart App Control blocked the new venv's `_psycopg` DLL. It cleared after Windows re-evaluated the package. See `README_DEMO.md` troubleshooting |
| `localhost` is slow | On this machine `localhost` can stall on IPv6 (~2 s). Use `127.0.0.1` on hot paths |
| Twilio call has no audio | Tunnel is down or the voice webhook points at the wrong host. Run `.\demo_twilio.ps1 -Status` |
| WhatsApp gets no reply | Sandbox webhook is not set to `https://<tunnel>/twilio/whatsapp` (HTTP POST) |
| Demo edited a file by hand | Sync refuses to overwrite without `--force`. Prefer reverting the demo file and syncing from source |
| Source tree dirty | Commit tracked changes. `--allow-dirty-source` exists but breaks reproducibility |
| Demo git tree dirty | Commit the demo or pass `--allow-dirty-demo` (not recommended) |

The Twilio voice number and the Salesforce org are shared with development.
Treat live calls, WhatsApp messages, and CRM writes as outward-facing actions:
confirm before testing them.

---

## 10. Git workflow

### Development repository

- The sync records the current development commit in `.demo_manifest.json`.
- It refuses `--sync` when tracked files are modified.
- Untracked files are not flagged. Commit new files before syncing.
- Commit the demo tooling/doc changes in the development repo as usual.

### Demo repository (when created)

The demo is not a git repository yet. Its `.gitignore` is ready. To initialize:

```powershell
cd D:\project\UniversityBotDemoAPI
git init
git add .
git commit -m "Demo freeze 2026-10-22 (dev 07083ae)"
```

Once the demo is a git repo:

- Commit the demo before every sync. The sync checks for a dirty demo tree.
- Never run `git add`/`git commit` from the sync script; it only reads git state.
- `.env`, `.venv/`, `data/`, `logs/`, `.demo_pids.json`, `.whatsapp_tunnel`,
  `.twilio_repoint_backup.json`, and tunnel state are gitignored.

---

## 11. Instructions for a future AI agent

1. **Read first:** this guide, `README_DEMO.md`, `DEMO_FREEZE_STATUS.md`, and
   `scripts/sync_demo.py`.
2. **Verify live state** before acting. The snapshot in section 2 may be stale.
   Check `git rev-parse HEAD`, the manifest, ports, Docker, and the demo `.env`.
3. **Never hand-edit the demo folder** for a development change. Edit
   development source or `scripts/demo/*` templates, commit, then sync.
4. **Commit before sync.** New files must be committed, not just present.
5. **Dry-run first:** `python scripts\sync_demo.py --dry-run`. Read the
   `[UPD]`, `[ADD]`, `[DEL]`, `[CONFLICT]` list before writing.
6. **Use `--confirm-deletions`** only after confirming the deleted files are
   genuinely obsolete. It removes managed files from the demo.
7. **Handle edge cases from the change matrix** in section 5: dependencies,
   schema, env keys, and policy changes are not covered by a plain sync.
8. **Validate** with `validate_demo.py --check-http` and keep the report.
9. **Never touch app runtime code just to make the demo work.** The runtime must
   stay byte-identical to the approved development commit. Fix the environment
   or the support scripts instead.
10. **Ask before outward-facing actions:** live Twilio calls, WhatsApp messages,
    CRM writes, or Twilio webhook changes. `-Live` is a deliberate, reversible
    change; say what it will do before running it.
11. **Record the result:** update `DEMO_FREEZE_STATUS.md` with the new commit,
    file count, validation result, and any new caveats. If this guide's facts
    change (paths, ports, policy, commands), update this guide too.

---

## 12. Quick command reference

```powershell
# --- update ---
cd D:\project\universityDemo
python scripts\sync_demo.py --dry-run
python scripts\sync_demo.py --sync
python scripts\sync_demo.py --sync --confirm-deletions
python scripts\sync_demo.py --audit --strict

# --- start / stop ---
cd D:\project\UniversityBotDemoAPI
.\demo_start.ps1 -StartCrm
.\demo_start.ps1 -Live -StartCrm -TunnelUi
.\demo_stop.ps1

# --- validate ---
cd D:\project\universityDemo
python scripts\validate_demo.py --check-http --report DEMO_FREEZE_VALIDATION.md

# --- database ---
docker ps --filter "name=elearning-postgres"
docker port elearning-postgres 5432/tcp

# --- help ---
Get-Help D:\project\UniversityBotDemoAPI\demo_start.ps1 -Examples
```

---

## 13. Related paths

| Path | Purpose |
|---|---|
| `D:\project\UniversityBotDemoAPI` | demo runtime |
| `D:\project\UniversityBotDemoAPI\README_DEMO.md` | operator guide |
| `D:\project\UniversityBotDemoAPI\.demo_manifest.json` | source commit + file hashes |
| `D:\project\UniversityBotDemoAPI\.demo_directory` | sentinel; records policy version |
| `D:\project\UniversityBotDemoAPI\DEMO_VERSION.txt` | human-readable freeze stamp |
| `D:\project\universityDemo\scripts\sync_demo.py` | sync engine + policy |
| `D:\project\universityDemo\scripts\validate_demo.py` | validator |
| `D:\project\universityDemo\scripts\demo\` | source templates for demo support files |
| `D:\project\universityDemo\tests\test_sync_demo.py` | sync tests (12) |
| `D:\project\universityDemo\DEMO_FREEZE_STATUS.md` | living status report |
