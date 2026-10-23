# Demo Freeze Validation

Generated: 2026-10-23T07:54:35-07:00

| Status | Check | Detail |
|---|---|---|
| PASS | demo directory | D:\project\UniversityBotDemoAPI |
| PASS | required file: app/main.py |  |
| PASS | required file: app.py |  |
| PASS | required file: dashboard.py |  |
| PASS | required file: requirements.txt |  |
| PASS | required file: content/meridian/meridian_knowledge_base.md |  |
| PASS | required file: README_DEMO.md |  |
| PASS | required file: .demo_directory |  |
| PASS | required file: .demo_manifest.json |  |
| PASS | forbidden path absent: tests/ |  |
| PASS | forbidden path absent: doc/ |  |
| PASS | forbidden path absent: models/ |  |
| PASS | forbidden path absent: tts_service/ |  |
| PASS | forbidden path absent: chroma_local_db/ |  |
| PASS | forbidden path absent: chroma_local_db_pre_c4/ |  |
| PASS | no secret-like files |  |
| PASS | only one markdown file | README_DEMO.md |
| PASS | manifest readable |  |
| PASS | manifest files present | 138 files |
| PASS | manifest hashes match | 138 files |
| PASS | source commit recorded | 07083aece22774c592d68314a3bc86f8296cff59 |
| WARN | development tree clean at sync | manifest says tracked development files were modified; commit before the next sync |
| PASS | demo .env present |  |
| PASS | demo database name | admissions_demo |
| PASS | CRM_ENABLED | true |
| PASS | runtime env keys present | all non-allowed development keys are present |
| PASS | runtime env parity | all non-allowed values match development |
| BLOCKED | demo secrets filled | empty: LLM_API_KEY, SARVAM_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, TWILIO_WHATSAPP_NUMBER |
| BLOCKED | LLM and Sarvam keys configured | chat/voice checks need demo LLM_API_KEY and SARVAM_API_KEY |
| BLOCKED | Twilio credentials configured | voice/WhatsApp checks need all four TWILIO_* values |
| PASS | database connection | current_database=admissions_demo |
| PASS | database schema | leads, conversations, courses present |
| PASS | demo venv imports app.main |  |
| PASS | FastAPI health | HTTP 200 (http://127.0.0.1:8000/) |
| PASS | Streamlit health | HTTP 200 (http://127.0.0.1:8501/_stcore/health) |
| PASS | CRM health | HTTP 200 (http://127.0.0.1:8098/health) |
| PASS | CRM read endpoint | HTTP 200 (http://127.0.0.1:8098/admissions) |

## Summary

BLOCKED=3, PASS=33, WARN=1

Result: **PASS with blocked checks** — blocked checks require credentials, a running stack, or an external service.
