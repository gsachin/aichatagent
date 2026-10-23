# Demo Freeze Validation

Generated: 2026-10-23T08:35:26-07:00

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
| PASS | source commit recorded | 219904e52d287a302a8db043e9df69257456a61e |
| PASS | development tree clean at sync |  |
| PASS | demo .env present |  |
| PASS | demo database name | admissions_demo |
| PASS | CRM_ENABLED | true |
| PASS | runtime env keys present | all non-allowed development keys are present |
| PASS | runtime env parity | all non-allowed values match development |
| PASS | demo secrets filled |  |
| PASS | LLM and Sarvam keys configured |  |
| PASS | Twilio credentials configured |  |
| PASS | database connection | current_database=admissions_demo |
| PASS | database schema | leads, conversations, courses present |
| PASS | demo venv imports app.main |  |
| PASS | FastAPI health | HTTP 200 (http://127.0.0.1:8000/) |
| PASS | Streamlit health | HTTP 200 (http://127.0.0.1:8501/_stcore/health) |
| PASS | CRM health | HTTP 200 (http://127.0.0.1:8098/health) |
| PASS | CRM read endpoint | HTTP 200 (http://127.0.0.1:8098/admissions) |

## Summary

PASS=37

Result: **PASS** — all checks passed.
