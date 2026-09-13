"""
Database migration for the Salesforce user-API integration (plan §8.5).

Idempotent ``ADD COLUMN IF NOT EXISTS`` / ``CREATE TABLE IF NOT EXISTS``,
executed on every startup by ``app/database.init_db()`` — matching the pattern
already used by the leads, offers and sentiment subsystems.

Every change here is **additive and nullable**, so there is no down-migration
and no backfill step the operator has to remember. The app runs correctly
against both the old and the new schema.

Why ``conversations.conversation_id`` is separate from ``conversations.id``
----------------------------------------------------------------------------
``id`` is the row's primary key. For a phone call that is one conversation, so
the two coincide. WhatsApp is different: every inbound message writes its own
``conversations`` row (``app/main.py`` → ``log_interaction``), but a WhatsApp
*session* spans many messages and must present ONE id to the CRM. So
``conversation_id`` groups rows into a logical conversation while ``id`` keeps
identifying the row.

For pre-existing rows ``conversation_id`` is backfilled from ``id``, which is
exactly right for the voice calls that dominate the existing data.
"""

# ── Columns added to existing tables ─────────────────────────────────────────

ALTER_LEADS_SQL = """
ALTER TABLE leads         ADD COLUMN IF NOT EXISTS crm_user_id   VARCHAR(32);
ALTER TABLE leads         ADD COLUMN IF NOT EXISTS crm_synced_at TIMESTAMP WITH TIME ZONE;
"""

ALTER_CONVERSATIONS_SQL = """
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS conversation_id VARCHAR(64);
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS crm_user_id     VARCHAR(32);
"""

# Backfill: one conversation per row for everything that predates this migration.
# Voice calls are one-row-per-call, so the row id IS the conversation id.
BACKFILL_CONVERSATIONS_SQL = """
UPDATE conversations
   SET conversation_id = id::text
 WHERE conversation_id IS NULL;
"""

# ── Retry queue for status writes that could not be delivered ────────────────
#
# Created here rather than in Phase 7 so the schema is settled in one step.
# Unused until Phase 2, which is harmless.

CREATE_OUTBOX_TABLE = """
CREATE TABLE IF NOT EXISTS crm_sync_outbox (
    id              BIGSERIAL PRIMARY KEY,
    crm_user_id     VARCHAR(32),
    conversation_id VARCHAR(64),
    op              VARCHAR(32)  NOT NULL,
    payload         JSONB        NOT NULL,
    attempts        INTEGER      NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    last_error      TEXT,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
"""

CREATE_CRM_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_conversations_conversation_id
    ON conversations(conversation_id);
CREATE INDEX IF NOT EXISTS idx_leads_crm_user
    ON leads(crm_user_id);
CREATE INDEX IF NOT EXISTS idx_crm_outbox_due
    ON crm_sync_outbox(next_attempt_at)
    WHERE last_error IS NULL OR attempts < 10;
"""

# ── Offer-letter document upload (plan §D7) ──────────────────────────────────
#
# The application number the document routes address is NOT the userId we store:
# they resolve the path segment with `WHERE Application_No__c = X`, while
# `crm_user_id` holds the Salesforce record Id. So the id is resolved once via
# `GET /admissions/{userId}` and cached here — a hint that saves a round trip,
# never a source of truth (it is cleared whenever the CRM link changes).
ALTER_LEADS_CRM_APPLICATION_SQL = """
ALTER TABLE leads ADD COLUMN IF NOT EXISTS crm_application_no VARCHAR(64);
"""

# The last program we told the CRM about. ``Course__c`` is free text and the CRM
# is only updated when this differs, so the per-turn link does not become a
# per-turn write.
ALTER_LEADS_CRM_COURSE_SQL = """
ALTER TABLE leads ADD COLUMN IF NOT EXISTS crm_course VARCHAR(255);
"""

# offer_letters is created by app/offers/schema.py, but the CRM integration owns
# the columns CRM writes — the same precedent as the conversations ALTER above.
# This is why ALL_CRM_SQL must stay last in init_db(): it ALTERs tables that the
# offers schema creates.
#
# crm_upload_status separates "never attempted" ('') from 'uploaded', 'failed'
# and 'unknown' — the last being a /complete that returned 500 after the
# ContentVersion may already exist, where the operator must go and look.
ALTER_OFFER_LETTERS_CRM_SQL = """
ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS crm_document_id   VARCHAR(32);
ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS crm_upload_status VARCHAR(16);
ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS crm_uploaded_at   TIMESTAMP WITH TIME ZONE;
ALTER TABLE offer_letters ADD COLUMN IF NOT EXISTS crm_expired_at    TIMESTAMP WITH TIME ZONE;
"""

ALL_CRM_SQL = "\n".join(
    [
        ALTER_LEADS_SQL,
        ALTER_CONVERSATIONS_SQL,
        BACKFILL_CONVERSATIONS_SQL,
        CREATE_OUTBOX_TABLE,
        CREATE_CRM_INDEXES_SQL,
        ALTER_LEADS_CRM_APPLICATION_SQL,
        ALTER_LEADS_CRM_COURSE_SQL,
        ALTER_OFFER_LETTERS_CRM_SQL,
    ]
)
