"""
SQL schema definitions for the lead-management subsystem.

All tables use idempotent CREATE TABLE IF NOT EXISTS so the module
can be called safely on every startup (matching the existing pattern
in app/database.py).

Usage:
    from app.leads.schema import ALL_TABLES_SQL
    cursor.execute(ALL_TABLES_SQL)
"""

# ── Leads: canonical record for each prospective student ─────────────
CREATE_LEADS_TABLE = """
CREATE TABLE IF NOT EXISTS leads (
    id                UUID PRIMARY KEY,
    phone_number      VARCHAR(32) NOT NULL,
    name              VARCHAR(255) DEFAULT '',
    email             VARCHAR(255) DEFAULT '',
    program_interest  VARCHAR(255) DEFAULT '',
    status            VARCHAR(32) NOT NULL DEFAULT 'pending',
    source            VARCHAR(64) DEFAULT 'manual',
    notes             TEXT DEFAULT '',
    call_attempts     INTEGER DEFAULT 0,
    last_called_at    TIMESTAMP WITH TIME ZONE,
    next_follow_up    TIMESTAMP WITH TIME ZONE,
    created_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""

# ── Conversations: per-interaction transcript & outcome logging ──────
CREATE_CONVERSATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS conversations (
    id                    UUID PRIMARY KEY,
    lead_id               UUID REFERENCES leads(id) ON DELETE CASCADE,
    phone_number          VARCHAR(32) DEFAULT '',
    channel               VARCHAR(32) NOT NULL,
    transcript            TEXT DEFAULT '',
    summary               TEXT DEFAULT '',
    call_duration_seconds INTEGER DEFAULT 0,
    outcome               VARCHAR(64) DEFAULT '',
    follow_up_needed      BOOLEAN DEFAULT FALSE,
    follow_up_reason      TEXT DEFAULT '',
    extracted_lead        JSONB DEFAULT '{}',
    created_at            TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""

# ── Follow-ups: scheduled actions (calls or messages) ────────────────
CREATE_FOLLOW_UPS_TABLE = """
CREATE TABLE IF NOT EXISTS follow_ups (
    id              UUID PRIMARY KEY,
    lead_id         UUID REFERENCES leads(id) ON DELETE CASCADE,
    scheduled_at    TIMESTAMP WITH TIME ZONE NOT NULL,
    status          VARCHAR(32) DEFAULT 'scheduled',
    type            VARCHAR(32) DEFAULT 'call',
    notes           TEXT DEFAULT '',
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at    TIMESTAMP WITH TIME ZONE
);
"""

# ── Call queue: serialised outbound call queue ───────────────────────
CREATE_CALL_QUEUE_TABLE = """
CREATE TABLE IF NOT EXISTS call_queue (
    id              UUID PRIMARY KEY,
    lead_id         UUID REFERENCES leads(id) ON DELETE CASCADE,
    status          VARCHAR(32) DEFAULT 'queued',
    call_sid        VARCHAR(64) DEFAULT '',
    scheduled_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at      TIMESTAMP WITH TIME ZONE,
    completed_at    TIMESTAMP WITH TIME ZONE,
    error_message   TEXT DEFAULT '',
    language        VARCHAR(8) DEFAULT '',
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""

# Existing databases predate the per-call language column; CREATE TABLE IF NOT
# EXISTS does not add it. This is the outbound equivalent of the inbound IVR
# digit: an explicit answer language for THIS call, chosen when it is queued.
ALTER_CALL_QUEUE_SQL = """
ALTER TABLE call_queue ADD COLUMN IF NOT EXISTS language VARCHAR(8) DEFAULT '';
"""

# WhatsApp reference-delivery consent. The columns are additive so existing
# databases keep working; opt-out is authoritative when set.
ALTER_LEADS_WHATSAPP_SQL = """
ALTER TABLE leads ADD COLUMN IF NOT EXISTS whatsapp_opt_in BOOLEAN DEFAULT FALSE;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS whatsapp_opt_out_at TIMESTAMP WITH TIME ZONE;
"""

# ── Indexes ──────────────────────────────────────────────────────────
CREATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_leads_status       ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_phone        ON leads(phone_number);
CREATE INDEX IF NOT EXISTS idx_leads_follow_up    ON leads(next_follow_up);
CREATE INDEX IF NOT EXISTS idx_conversations_lead ON conversations(lead_id);
CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
CREATE INDEX IF NOT EXISTS idx_follow_ups_scheduled ON follow_ups(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_call_queue_status  ON call_queue(status);
"""

# ── Convenience: run everything in one shot ──────────────────────────
ALL_TABLES_SQL = "\n".join(
    [
        CREATE_LEADS_TABLE,
        CREATE_CONVERSATIONS_TABLE,
        CREATE_FOLLOW_UPS_TABLE,
        CREATE_CALL_QUEUE_TABLE,
        ALTER_CALL_QUEUE_SQL,
        ALTER_LEADS_WHATSAPP_SQL,
        CREATE_INDEXES_SQL,
    ]
)
