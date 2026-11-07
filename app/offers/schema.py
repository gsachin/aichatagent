"""
SQL schema definitions for the offer-letter subsystem.

All tables use idempotent CREATE TABLE IF NOT EXISTS so the module
can be called safely on every startup (matching the existing pattern
in app/leads/schema.py).

Usage:
    from app.offers.schema import ALL_OFFERS_SQL
    cursor.execute(ALL_OFFERS_SQL)
"""

# ── Courses: program catalog ──────────────────────────────────────────
CREATE_COURSES_TABLE = """
CREATE TABLE IF NOT EXISTS courses (
    id              UUID PRIMARY KEY,
    name            VARCHAR(255) NOT NULL UNIQUE,
    duration        VARCHAR(128) DEFAULT '',
    fees            VARCHAR(128) DEFAULT '',
    intake          VARCHAR(128) DEFAULT '',
    description     TEXT DEFAULT '',
    payment_link    VARCHAR(512) DEFAULT '',
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""

# ── Documents: files uploaded for a lead ──────────────────────────────
CREATE_LEAD_DOCUMENTS_TABLE = """
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
"""

# ── Offer letters: generated PDFs + send/tracking state ──────────────
CREATE_OFFER_LETTERS_TABLE = """
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
"""

# ── Indexes ──────────────────────────────────────────────────────────
CREATE_OFFER_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_lead_documents_lead  ON lead_documents(lead_id);
CREATE INDEX IF NOT EXISTS idx_offer_letters_lead   ON offer_letters(lead_id);
CREATE INDEX IF NOT EXISTS idx_offer_letters_status ON offer_letters(status);
CREATE INDEX IF NOT EXISTS idx_courses_name         ON courses(name);
"""

# ── Migrations: add columns to existing tables (safe to run repeatedly) ─
OFFER_MIGRATIONS_SQL = """
ALTER TABLE courses ADD COLUMN IF NOT EXISTS payment_link VARCHAR(512) DEFAULT '';
"""

# ── Convenience: run everything in one shot ──────────────────────────
ALL_OFFERS_SQL = "\n".join(
    [
        CREATE_COURSES_TABLE,
        CREATE_LEAD_DOCUMENTS_TABLE,
        CREATE_OFFER_LETTERS_TABLE,
        CREATE_OFFER_INDEXES_SQL,
        OFFER_MIGRATIONS_SQL,
    ]
)
