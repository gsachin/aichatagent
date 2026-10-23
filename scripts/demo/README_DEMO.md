# UniversityBotDemoAPI — Frozen Demo

This directory is a **frozen, independently runnable demo** of the University
Admissions Voice Assistant. It is not the development tree.

- Application code, prompts, knowledge base, model settings, voices, and
  language settings are a byte-for-byte copy of the approved development
  version.
- Only the database name, secrets, tunnel URL, and demo runtime state differ.
- Updates come only from `python scripts/sync_demo.py` run in
  `D:\project\universityDemo`. Nothing here is synced automatically.

The freeze source commit is in `.demo_manifest.json` and `DEMO_VERSION.txt`.

---

## 1. Prerequisites

- Windows 11
- Python 3.11
- Docker Desktop with the `elearning-postgres` container running (PostgreSQL 16)
- `ffmpeg` on PATH
- `cloudflared` on PATH for live Twilio/WhatsApp demonstrations
- The Salesforce Admission API source at
  `D:\project\salesforce\salesforce-admission-api` (for live CRM)
- Network access to the LLM endpoint and Sarvam API

The demo uses these external caches from this machine:

| Cache | Approx. size | Used for |
|---|---:|---|
| `~/.cache/huggingface/hub/models--Systran--faster-whisper-small` | 464 MB | Whisper STT fallback |
| `~/.cache/huggingface/hub/models--OpenVoiceOS--ai4bharat-indicconformer-hi-onnx` | 131 MB | Hindi STT fallback |
| `~/.cache/huggingface/hub/models--OpenVoiceOS--ai4bharat-indicconformer-gu-onnx` | 131 MB | Gujarati STT fallback |
| `~/.cache/pipecat/kokoro-onnx` | 354 MB | Kokoro TTS pre-warm/fallback (non-fatal; primary TTS is Sarvam) |

The demo does not copy the model caches. If the demo is moved to another
machine, copy those directories or let the first warmup download them.

---

## 2. First-time setup

```powershell
cd D:\project\UniversityBotDemoAPI
.\demo_setup.ps1 -InitDb
```

This one-time step:

1. Creates `.venv` and installs `requirements.txt` (several GB; the NVIDIA
   torch cu128 wheel is installed first on this machine).
2. Creates `data/documents`, `data/offers`, and `logs`.
3. Creates `.env` from `env.demo.example`.
4. Creates the `admissions_demo` database and the least-privilege `demo_user`
   role, generates a password, updates `DATABASE_URL`, and initializes the
   schema. The script detects the container's published host port (currently
   **9200**, not 5432) and writes it into the DSN.
5. Checks the model caches, `ffmpeg`, and `cloudflared`.
6. Runs `python -c "import app.main"`.

Then fill the blank secrets in `.env`:

- `LLM_API_KEY`
- `SARVAM_API_KEY`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`
- `TWILIO_WHATSAPP_NUMBER`
- `SMTP_*` only if offer email is demonstrated

Do **not** commit `.env`.

---

## 3. Demo data

```powershell
.\demo_seed.ps1 -Confirm
```

The seed script refuses any database whose name does not end in `_demo`, prints
the target database, and runs the deletes and inserts in one transaction. It
clears only `admissions_demo`.

The live development database on this machine is `universcity`. The demo role
`demo_user` has no table privileges on it, the launcher refuses any database
other than `admissions_demo`, and the seed script refuses `universcity`.

---

## 4. Run the demo

### Local UI only

```powershell
.\demo_start.ps1
```

Starts FastAPI on `http://127.0.0.1:8000`, Streamlit chat on
`http://127.0.0.1:8501`, and the dashboard on `http://127.0.0.1:8502`.
No tunnel, no Twilio webhook change.

### Full client demo

```powershell
.\demo_start.ps1 -Live -StartCrm -TunnelUi
```

This additionally:

1. Starts a Cloudflare tunnel to FastAPI and captures its public URL.
2. Backs up the current Twilio voice webhook and status callback to
   `.twilio_repoint_backup.json`.
3. Repoints the voice webhook and status callback at the demo tunnel.
4. Checks the CRM API at `http://127.0.0.1:8098`; starts it from its own
   directory if `-StartCrm` is passed.
5. Prints the WhatsApp Sandbox webhook URL to set manually:
   `https://<tunnel>/twilio/whatsapp` (HTTP POST).
6. Starts Cloudflare tunnels for the Streamlit chat (8501) and the admin
   dashboard (8502), printing both public URLs.

The demo and development stacks use the same ports. **Stop the development
stack first.** The start script refuses to start if 8000, 8501, or 8502 is in
use.

### Remote viewers

`-TunnelUi` starts both UI tunnels — Streamlit chat on 8501 and the dashboard
on 8502 — matching development's `start_services.ps1 -WithStreamlit`. The
dashboard tunnel is skipped when `-NoDashboard` is used.

---

## 5. Twilio voice and WhatsApp

### Voice

`demo_start.ps1 -Live` handles the voice webhook automatically. Manual control:

```powershell
.\demo_twilio.ps1 -Enable -TunnelUrl <tunnel-host>
.\demo_twilio.ps1 -Restore
.\demo_twilio.ps1 -Status
```

`-Enable` saves the current values only once. If a backup already exists, it is
kept as the restore point. `-Restore` reads it back and verifies the result.

### WhatsApp

The Twilio Sandbox has **one** webhook and Twilio exposes no API to change it.
Before the demo, set it in the Twilio Console to:

`https://<tunnel-host>/twilio/whatsapp` (HTTP POST)

After the demo, set it back to the development tunnel URL.
`demo_stop.ps1` prints the restore URL. Dev and demo cannot both receive
WhatsApp messages at the same time.

### One number, one stack

There is one voice number and one sandbox webhook. Run development and the demo
one at a time. The demo leaves the voice webhook restored on `demo_stop.ps1`;
if a demo crashes, run `.\demo_twilio.ps1 -Restore` manually.

---

## 6. CRM

`CRM_ENABLED=true` in `.env`. The app calls the Salesforce Admission API at
`http://127.0.0.1:8098`, which talks to the Salesforce org through OAuth.

- The CRM API must be started with its own working directory because it calls
  `load_dotenv()` with no path. `demo_start.ps1 -StartCrm` does this correctly.
- Demo-created leads and documents go into the **same Salesforce org** as
  development/test data. If the org is a sandbox, production is untouched, but
  demo and dev records are mixed.
- Full CRM isolation requires a separate Salesforce sandbox/org and separate
  credentials — a separate Salesforce workstream.

Validate CRM during a demo by creating a lead with a unique demo phone number
and checking it in Salesforce.

---

## 7. Stop

```powershell
.\demo_stop.ps1
```

This restores the Twilio voice webhook first, prints the WhatsApp restore URL,
stops the tunnel and the demo processes recorded in `.demo_pids.json`, and
leaves the CRM API running if it was already running before the demo. Use
`-KeepCrm` to leave a demo-started CRM API running.

---

## 8. Update from development

Only run the sync after the development change is tested and committed.

```powershell
cd D:\project\universityDemo

# 1. Preview
python scripts/sync_demo.py --dry-run

# 2. Commit the current demo state in its own git repo first (if initialized)

# 3. Apply
python scripts/sync_demo.py --sync

# 4. Remove managed files that no longer exist in development
python scripts/sync_demo.py --sync --confirm-deletions
```

The sync script:

- copies only the managed policy (application code, KB, requirements, three
  runtime scripts, eight demo support files);
- never copies `.env`, `.env.bak*`, `data/`, `logs/`, tests, docs, `.venv`, or
  `.git`;
- records every file hash and the source commit in `.demo_manifest.json`;
- refuses to sync when the development tracked tree is dirty;
- refuses to overwrite a demo file you edited unless `--force` is passed;
- requires `--confirm-deletions` before removing obsolete managed files;
- refuses to sync when the demo is a git repository with uncommitted changes.

`--audit` reports runtime-looking development files that fall outside the
policy, so a future feature cannot be silently omitted.

---

## 9. Demo git

When ready, initialize the demo's own repository:

```powershell
cd D:\project\UniversityBotDemoAPI
git init
git add .
git commit -m "Demo freeze 2026-10-22 (dev 07083ae)"
```

Before each sync, commit the current demo state. The sync script checks that the
demo working tree is clean and never runs `git add` or `git commit`.

`.env`, `.venv/`, `data/`, `logs/`, `.demo_pids.json`, `.whatsapp_tunnel`, and
`.twilio_repoint_backup.json` are gitignored.

---

## 10. Troubleshooting

| Symptom | Check |
|---|---|
| `Refusing to start: DATABASE_URL targets ...` | `.env` must use `admissions_demo` |
| `Port 8000 is already in use` | Stop the development stack; do not kill the process blindly |
| `CRM API is not running` | Start `D:\project\salesforce\salesforce-admission-api` or pass `-StartCrm` |
| CRM writes queue but never appear | Check the CRM API `/health` and its own `.env`; it must run from its own directory |
| Twilio call has no audio | Check the tunnel is up and the voice webhook points at the demo host |
| WhatsApp gets no reply | Check the sandbox webhook is set to `https://<tunnel>/twilio/whatsapp`, HTTP POST |
| `cloudflared is not on PATH` | Install cloudflared or run without `-Live` |
| Voice quality differs | Verify `.env` matches the development non-secret values; the demo uses the same model, voices, and prompts |
| Model download warning | Check the HuggingFace cache or allow the first warmup to download |
| `psycopg2: DLL load failed ... Application Control policy` | Smart App Control may block `_psycopg` in a newly created venv on this machine. Use the approved interpreter workaround or add a policy exclusion for `D:\project\UniversityBotDemoAPI\.venv`. |

Logs are in `logs/`:

- `fastapi.out.log`, `fastapi.err.log`
- `streamlit.out.log`, `streamlit.err.log`
- `dashboard.out.log`, `dashboard.err.log`
- `crm.out.log`, `crm.err.log`
- `tunnel.err.log`, `tunnel-ui.err.log`
- `voice_assistant.log` (application)

---

## 11. Security

- `.env` contains API keys, Twilio credentials, and the demo database password.
  It is gitignored and must never be committed or shared.
- `data/` contains uploaded documents and generated offer letters. It is
  gitignored.
- Use demo-specific API keys with spend limits where possible. The model and
  voice settings must stay the same to preserve answer quality.
- The demo database role `demo_user` has privileges only on `admissions_demo`.
  It has no table privileges on the development database.
