# Offer Letter Module — Chat Update Reference

> **Date**: 2026-08-08 to 2026-08-09 | **Branch**: `offerlaterupdate` | **Repo**: `gsachin/aichatagent`

## Session Overview

Complete implementation of the Offer Letter module for the University Admissions Voice Assistant. The feature auto-generates branded PDF offer letters and sends them via WhatsApp + email when a student uploads documents and expresses interest in a program.

---

## All Commits on `offerlaterupdate`

| Commit | Description |
|---|---|
| `0ec2688` | feat: add in-chat document uploader — appears when ready for docs |
| `46cbbef` | fix: improve upload error handling — proper fallback lead creation |
| `88bc1a0` | fix: rewrite Streamlit chat as intelligent admission agent |
| `be3bbbe` | fix: pdf_path DB persistence + update_course RETURNING column fix |
| `f7a3a26` | feat: offer letter module — auto-generate PDF + send via WhatsApp/email |

---

## Files Created (12 new)

```
app/offers/__init__.py          — package marker
app/offers/schema.py            — courses, lead_documents, offer_letters tables
app/offers/models.py            — 16 async CRUD functions
app/offers/pdf.py               — fpdf2 branded PDF template (A4, 2 pages)
app/offers/service.py           — auto-trigger orchestration + WhatsApp/email send
app/messaging.py                — shared Twilio WhatsApp sender (extracted from main.py)
app/emailer.py                  — SMTP email sender with PDF attachment
app/dashboard/courses_page.py   — course catalog Streamlit page
data/                           — runtime: data/documents/, data/offers/
doc/offerletterfile/offer_letter_plan.md
doc/offerletterfile/updated_plan_student_self_service.md
doc/offerletterfile/IMPLEMENTATION_STATUS.md
```

## Files Modified (13 existing)

```
app/main.py                     — WhatsApp webhook rewrite + 10 new REST endpoints
app/leads/service.py            — _detect_admission_intent + post-call WhatsApp follow-up
app/config.py                   — 11 new settings (DATA_DIR, SMTP, payment_link, etc.)
app/database.py                 — 1-line wiring for ALL_OFFERS_SQL
app/dashboard/pages.py          — api_upload() + api_call_bytes() helpers
app/dashboard/leads_page.py     — document upload + offer history in lead expander
dashboard.py                    — courses page in nav
requirements.txt                — fpdf2 added
.gitignore                      — data/ added
scripts/seed_demo_data.py       — 6 demo courses
app.py                          — sidebar uploader + admission agent rewrite
app/outbound/caller.py          — Twilio error 21216 formatting
app/static/quick_call.html      — error display improvements
```

---

## Key Features Implemented

### 1. Auto Offer Letter Generation
- Upload document for a lead with `program_interest` → offer letter auto-generated
- PDF includes: university branding, student name, program details, fees, payment link, terms, signature
- 24h idempotency guard prevents duplicate offers

### 2. Multi-Channel Document Upload
| Channel | How |
|---|---|
| **Dashboard (staff)** | File upload in lead expander |
| **Streamlit Chat (student)** | In-chat uploader + sidebar uploader |
| **WhatsApp (student)** | Native — send photo/PDF as message |
| **Voice calls** | Post-call WhatsApp follow-up asking for documents |

### 3. WhatsApp Webhook Rewrite
- Media detection: checks `MediaContentType0` — routes audio to voice pipeline, images/PDFs to doc handler
- `NumMedia` support for multi-attachment
- LLM-based admission intent detection (keyword fast-path + Ollama semantic matching)
- ACCEPT/DECLINE reply handling (was dead-end, now updates offer status)

### 4. Intelligent Admission Agent (Streamlit Chat)
- State machine flow: Name → Email → Phone → Confirm → Program → Qualification Check → Documents → Offer
- Per-program qualification requirements (MBA, CS, Data Science, Engineering, etc.)
- Multi-intent handling: "my name is Deepak and I want to take admission in MBA" extracts both
- In-chat upload widget appears when chatbot enters "awaiting_docs" state

### 5. Payment Link Integration
- `payment_link` column on courses table
- Included in PDF, WhatsApp message, and email
- Configurable per course via dashboard

### 6. REST API (10 new endpoints)
- `GET/POST/PUT/DELETE /api/courses`
- `POST/GET /api/leads/{id}/documents`
- `GET/DELETE /api/documents/{id}`
- `GET /api/leads/{id}/offer-letters`
- `GET /api/offers/{id}`, `GET /api/offers/{id}/pdf`
- `PUT /api/offers/{id}/status`

---

## Database Tables Created

```sql
courses (id, name, duration, fees, intake, description, payment_link, is_active, created_at)
lead_documents (id, lead_id FK, filename, stored_path, doc_type, mime_type, size_bytes, uploaded_at)
offer_letters (id, lead_id FK, course_id FK, program, status, pdf_path, offer_date, valid_until, terms, sent_via, whatsapp_sid, email_id, sent_at, response_at, created_at, updated_at)
```

---

## Test Results

| Test | Result |
|---|---|
| 44/44 dashboard tests | ✅ |
| 33/33 live flow tests | ✅ |
| Document upload → auto offer | ✅ |
| PDF generation (3KB branded A4) | ✅ |
| PDF content verified (decompressed) | ✅ |
| 24h idempotency guard | ✅ |
| Accept/Decline status update | ✅ |
| WhatsApp sandbox send (Twilio 201) | ✅ |
| Course matching (ILIKE) | ✅ |
| Streamlit admission agent flow | ✅ |
| In-chat upload widget | ✅ |

---

## Bug Fixes During Session

1. **Twilio error 21216**: Added user-friendly error formatting in `app/outbound/caller.py`
2. **WhatsApp media misrouting**: PDFs/images were treated as voice notes → fixed with `MediaContentType0` check
3. **ACCEPT/DECLINE dead-end**: WhatsApp replies weren't handled → added `_handle_offer_response()`
4. **pdf_path not persisted to DB**: PDF path only in memory, not in database → added `pdf_path` param to `update_offer_letter_status`
5. **update_course RETURNING missing column**: `payment_link` not in RETURNING clause → fixed
6. **Name detection storing wrong values**: "Wan to take admission in MBA" stored as name → skip if contains admission keywords
7. **Admission intent never firing**: State machine intercepted before intent check → reordered with LLM semantic matching
8. **Upload silently failing**: Empty `lead_id` with silent exceptions → proper fallback + clear error messages

---

## How to Test

```bash
# Start backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Start admin dashboard
streamlit run dashboard.py --server.port 8502

# Start student chat
streamlit run app.py --server.port 8503

# Seed demo data
python scripts/seed_demo_data.py
```

### Student Chat Flow:
1. Open http://localhost:8503
2. Say "I want to take admission in MBA"
3. Give name → email → phone
4. Confirm profile → see qualification requirements → say "yes"
5. Upload widget appears in chat → upload PDF/image
6. Offer letter auto-generated with balloons!

### WhatsApp Sandbox Setup:
1. Activate sandbox in Twilio Console → get join code
2. Send `join <code>` to sandbox number from phone
3. Configure webhook URL in Twilio Console
4. Send "I want to take admission" → upload photo → receive offer PDF

---

## Environment Variables Added

```env
TWILIO_WHATSAPP_NUMBER=+14155238886    # WhatsApp sandbox number
DATA_DIR=data                           # Document storage
UNIVERSITY_NAME=...                     # PDF branding
OFFER_EMAIL=admissions@university.edu   # PDF footer
OFFER_VALID_DAYS=30                     # Offer expiry
SMTP_HOST=smtp.gmail.com               # Email delivery
SMTP_PORT=587
SMTP_USER=
SMTP_PASS=
DEFAULT_PAYMENT_LINK=https://pay.university.edu/admissions
```
