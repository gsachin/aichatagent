# Offer Letter Feature — Implementation Plan

> Saved to: `doc/offerletterfile/` | Branch: `DashboardImplemetation`

## Context

When a prospective student submits documents and expresses interest in a program, the system should **automatically** generate and send a professional PDF offer letter via **WhatsApp and email**. The app currently has no document storage, no PDF generation, no email delivery, and no course catalog — all four must be built.

The WhatsApp chatbot **already** collects name, phone number, and email via a state machine (`app/main.py` lines 1084-1135). No changes needed there.

## User Decisions

- **Trigger**: Automatic — when documents are uploaded for a lead that already has `program_interest`, the system generates AND sends the offer letter immediately.
- **Delivery**: WhatsApp (existing Twilio integration) + Email (new SMTP).
- **Course catalog**: New structured `courses` table with name, duration, fees, intake dates.

---

## Folder Structure & Complexity Analysis

### All offer-letter code lives in `app/offers/` — self-contained

```
app/offers/              ← ONE isolated folder for ALL offer-letter logic
├── __init__.py           ← package marker (empty)
├── schema.py             ← 3 CREATE TABLE IF NOT EXISTS SQL strings
├── models.py             ← async CRUD functions
├── pdf.py                ← fpdf2 offer letter PDF template
└── service.py            ← auto-trigger orchestration + send logic
```

### Two shared utility files at `app/` level (used by other modules too)

```
app/messaging.py          ← WhatsApp sender (also used by voice pipeline)
app/emailer.py            ← SMTP sender (generic, reusable)
```

### Why this adds ZERO complexity:

| Concern | Answer |
|---|---|
| **Circular imports?** | No — `app/offers/` imports FROM `app/messaging.py`, `app/emailer.py`, `app/config.py`, `app/leads/models.py`. None of those import back from `app/offers/`. |
| **Follows existing pattern?** | Yes — identical structure to `app/leads/` (schema + models + service), `app/outbound/` (caller + twiml + scheduler), `app/dashboard/` (page modules). |
| **Tight coupling?** | No — the `app/offers/` package only touches other modules through their public functions (e.g., `get_lead()`, `send_whatsapp_message()`). Same as how `app/outbound/caller.py` calls `app/leads/models.py`. |
| **Startup wiring?** | 1 line in `app/database.py::init_db()` → `cur.execute(ALL_OFFERS_SQL)`. Same as the existing 1 line for `ALL_TABLES_SQL`. |
| **New dependencies?** | Only `fpdf2` (pure Python, zero system deps, works on AppLocker-constrained Windows). SMTP uses stdlib `smtplib` + `email.mime`. |

### Files touched outside `app/offers/`:

| File | Change | Risk |
|---|---|---|
| `app/main.py` | 10 new endpoints + 2 imports | Low — follows existing endpoint patterns |
| `app/messaging.py` | **NEW** — WhatsApp sender extracted from main.py | Low — pure refactor, existing callers delegate |
| `app/emailer.py` | **NEW** — SMTP sender | None — standalone, no existing code touched |
| `app/database.py` | 1 line in `init_db()` | None |
| `app/config.py` | ~8 new settings | None |
| `app/dashboard/leads_page.py` | UI additions inside expander | Low — additive, no existing UI removed |
| `app/dashboard/courses_page.py` | **NEW** — course management | None — new page |
| `app/dashboard/pages.py` | 2 new helpers | Low — additive |
| `dashboard.py` | Add courses to nav | None — 1 line |
| `requirements.txt` | Add `fpdf2` | None |

---

## 1. New Database Tables

Three new tables in `app/offers/schema.py` (following the existing `app/leads/schema.py` pattern — idempotent `CREATE TABLE IF NOT EXISTS`, UUID PKs, FK `ON DELETE CASCADE`):

### `courses` — program catalog
```sql
CREATE TABLE IF NOT EXISTS courses (
    id              UUID PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,
    duration        VARCHAR(128) DEFAULT '',
    fees            VARCHAR(128) DEFAULT '',
    intake          VARCHAR(128) DEFAULT '',
    description     TEXT DEFAULT '',
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### `lead_documents` — uploaded files
```sql
CREATE TABLE IF NOT EXISTS lead_documents (
    id           UUID PRIMARY KEY,
    lead_id      UUID REFERENCES leads(id) ON DELETE CASCADE,
    filename     VARCHAR(255) NOT NULL,
    stored_path  VARCHAR(512) NOT NULL,
    doc_type     VARCHAR(64) DEFAULT 'other',
    mime_type    VARCHAR(128) DEFAULT '',
    size_bytes   INTEGER DEFAULT 0,
    uploaded_at  TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### `offer_letters` — generated PDFs + tracking
```sql
CREATE TABLE IF NOT EXISTS offer_letters (
    id                UUID PRIMARY KEY,
    lead_id           UUID REFERENCES leads(id) ON DELETE CASCADE,
    course_id         UUID REFERENCES courses(id) ON DELETE SET NULL,
    program           VARCHAR(255) DEFAULT '',
    status            VARCHAR(32) NOT NULL DEFAULT 'sent',
    pdf_path          VARCHAR(512) DEFAULT '',
    offer_date        DATE DEFAULT CURRENT_DATE,
    valid_until       DATE,
    terms             TEXT DEFAULT '',
    sent_via          VARCHAR(32) DEFAULT 'whatsapp',
    whatsapp_sid      VARCHAR(64) DEFAULT '',
    email_id          VARCHAR(255) DEFAULT '',
    sent_at           TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    response_at       TIMESTAMP WITH TIME ZONE,
    created_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

**Statuses**: `sent` (auto-sent), `accepted`, `rejected`. No `draft` since auto-send.

---

## 2. REST API Endpoints

### Courses
- `GET /api/courses` — list active courses
- `POST /api/courses` — create course
- `PUT /api/courses/{id}` — update course
- `DELETE /api/courses/{id}` — deactivate

### Documents
- `POST /api/leads/{lead_id}/documents` — multipart upload. **Auto-triggers** offer letter if lead has `program_interest`.
- `GET /api/leads/{lead_id}/documents` — list documents
- `DELETE /api/documents/{document_id}` — delete
- `GET /api/documents/{document_id}/file` — serve file

### Offer Letters
- `GET /api/leads/{lead_id}/offer-letters` — list
- `GET /api/offers/{offer_id}` — details
- `GET /api/offers/{offer_id}/pdf` — serve PDF (public URL for Twilio + email)
- `PUT /api/offers/{offer_id}/status` — mark accepted/rejected

---

## 3. Auto-Trigger Flow

`POST /api/leads/{lead_id}/documents`:
1. Validate lead → save file → insert `lead_documents` row
2. If `lead.program_interest` is non-empty → `service.generate_and_send_offer(lead_id)`
3. Return `{ document, offer_letter }`

`service.generate_and_send_offer()`:
1. Match `program_interest` to `courses.name` (ILIKE)
2. Generate PDF → `data/offers/<id>.pdf`
3. Insert `offer_letters` row
4. Send WhatsApp (PDF as media_url via Twilio)
5. Send Email (PDF as attachment via SMTP)
6. Log conversation

**Guard**: Skip if offer already sent to this lead in last 24h.

---

## 4. PDF Generation

Library: **fpdf2** (pure Python, zero system deps). Template:
- A4, auto page-break, branded header
- Reference: `OL-{year}-{offer_id[:6]}`
- Student name, program, duration, fees, intake, dates table
- Terms & Conditions, signature block, page footer

---

## 5. WhatsApp + Email

- **WhatsApp**: Extract `_send_whatsapp_message` → `app/messaging.py`. Already supports `media_url`. PDF served at `https://{tunnel}/api/offers/{id}/pdf`.
- **Email**: `app/emailer.py` via `smtplib` + `email.mime` (stdlib). New `.env`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`.

---

## 6. Dashboard Integration

- **New page**: `app/dashboard/courses_page.py` — course CRUD
- **Leads page**: Add document upload + offer letter history inside each lead's expander
- **New helpers**: `api_upload()` (multipart), `api_call_bytes()` (PDF download)

---

## 7. Implementation Order

| # | Step | File(s) |
|---|---|---|
| 1 | Install fpdf2 | `pip install fpdf2`, `requirements.txt` |
| 2 | Schema + wiring | `app/offers/schema.py`, `app/database.py` |
| 3 | Config | `app/config.py` |
| 4 | CRUD models | `app/offers/models.py` |
| 5 | WhatsApp sender extract | `app/messaging.py`, `app/main.py` |
| 6 | Email sender | `app/emailer.py` |
| 7 | PDF template | `app/offers/pdf.py` |
| 8 | Orchestration | `app/offers/service.py` |
| 9 | API endpoints | `app/main.py` |
| 10 | Course management UI | `app/dashboard/courses_page.py`, `dashboard.py` |
| 11 | Dashboard helpers | `app/dashboard/pages.py` |
| 12 | Lead detail UI | `app/dashboard/leads_page.py` |
| 13 | Gitignore + seed data | `.gitignore`, `scripts/seed_demo_data.py` |

---

## 8. Verification

1. Restart server → tables created without errors
2. Add courses via dashboard → verify in DB
3. Upload document for lead with program_interest → offer letter auto-generated + sent
4. Check WhatsApp for PDF delivery
5. Check email inbox for PDF delivery
6. Upload second doc → no duplicate offer (24h guard)
7. Mark offer accepted/rejected → DB updated
8. Existing voice calls, WhatsApp bot, dashboard still work
