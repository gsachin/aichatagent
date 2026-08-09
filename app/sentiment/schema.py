"""
SQL schema for the sentiment-analysis subsystem.

Tables are idempotent (CREATE TABLE IF NOT EXISTS) so they can be
called safely on every startup alongside the existing lead tables.
"""

# ── Sentiment scores: per-call sentiment extraction results ────────────
CREATE_SENTIMENT_SCORES_TABLE = """
CREATE TABLE IF NOT EXISTS sentiment_scores (
    id                  UUID PRIMARY KEY,
    lead_id             UUID REFERENCES leads(id) ON DELETE CASCADE,
    conversation_id     UUID REFERENCES conversations(id) ON DELETE SET NULL,
    -- Per-call sentiment components
    s_call              DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    primary_emotion     VARCHAR(64) DEFAULT '',
    buying_intent_score DOUBLE PRECISION DEFAULT 0.0,
    friction_score      DOUBLE PRECISION DEFAULT 0.0,
    objections          JSONB DEFAULT '[]',
    -- Composite scores
    s_lead              DOUBLE PRECISION DEFAULT 0.0,
    p_convert           DOUBLE PRECISION,
    trajectory          VARCHAR(32) DEFAULT 'Stable',
    category            VARCHAR(32) DEFAULT 'Nurture',
    -- Scoring metadata
    extraction_raw      JSONB DEFAULT '{}',
    model_version       VARCHAR(32) DEFAULT 'rules-v1',
    weights_used        JSONB DEFAULT '{}',
    transcript_snippet  TEXT DEFAULT '',
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""

# ── Indexes ──────────────────────────────────────────────────────────
CREATE_SENTIMENT_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_sentiment_lead       ON sentiment_scores(lead_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_conv       ON sentiment_scores(conversation_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_created     ON sentiment_scores(created_at);
CREATE INDEX IF NOT EXISTS idx_sentiment_category    ON sentiment_scores(category);
CREATE INDEX IF NOT EXISTS idx_sentiment_lead_latest ON sentiment_scores(lead_id, created_at DESC);
"""

# ── Add sentiment columns to leads table ─────────────────────────────
ALTER_LEADS_SENTIMENT_SQL = """
ALTER TABLE leads ADD COLUMN IF NOT EXISTS current_category        VARCHAR(32) DEFAULT '';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS overall_sentiment_score DOUBLE PRECISION DEFAULT 0.0;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS sentiment_trajectory    VARCHAR(32) DEFAULT '';
ALTER TABLE leads ADD COLUMN IF NOT EXISTS conversion_probability  DOUBLE PRECISION;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_sentiment_at       TIMESTAMP WITH TIME ZONE;
"""

# ── Convenience: run everything in one shot ──────────────────────────
ALL_SENTIMENT_SQL = "\n".join(
    [
        CREATE_SENTIMENT_SCORES_TABLE,
        CREATE_SENTIMENT_INDEXES_SQL,
        ALTER_LEADS_SENTIMENT_SQL,
    ]
)
