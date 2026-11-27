-- ==============================================================================
-- anti-judol-hunter: Supabase (PostgreSQL) Schema & Atomic Queue Locking
-- Run this entire file in Supabase SQL Editor.
-- ==============================================================================

CREATE TABLE IF NOT EXISTS candidates (
    id                  BIGSERIAL PRIMARY KEY,
    -- Canonical deduplication key (strict competition normalization rule)
    normalized_url      TEXT NOT NULL UNIQUE,
    -- Full URL (http/https) used for fetching & submission
    original_url        TEXT NOT NULL,
    -- Lowercase hostname without www.
    domain              TEXT NOT NULL,
    -- OSINT source: 'crtsh', 'urlscan', 'certstream', 'manual'
    source              TEXT NOT NULL DEFAULT 'unknown',

    -- Lifecycle status:
    --   PENDING          -> waiting for async HTTP + AI verification
    --   PROCESSING       -> locked by a worker for verification
    --   VERIFIED_ILLEGAL -> confirmed illegal by AI, ready for ISP check / submit
    --   SAFE             -> classified safe by AI
    --   LOW_CONFIDENCE   -> illegal category but below confidence threshold
    --   PARKED           -> parked domain / for sale / suspended
    --   DEAD             -> unreachable / timeout / 4xx-5xx error page
    --   SUBMITTED        -> submitted by user via CLI
    --   SKIPPED          -> skipped by user during CLI review
    --   FALSE_POSITIVE   -> manually rejected by user during CLI review
    status              TEXT NOT NULL DEFAULT 'PENDING',

    -- HTTP & Content metadata
    http_status         INTEGER,
    final_url           TEXT,
    page_title          TEXT,
    meta_description    TEXT,
    is_parked           BOOLEAN NOT NULL DEFAULT FALSE,

    -- AI Classification
    category            TEXT, -- 'JUDOL', 'PINJOL_ILEGAL', 'SCAM_PHISHING', 'SAFE'
    confidence          DOUBLE PRECISION,
    ai_reason           TEXT,

    -- Indonesian ISP accessibility check (run from local PC in Indonesia)
    id_isp_accessible   BOOLEAN,
    id_isp_note         TEXT,
    id_isp_checked_at   TIMESTAMPTZ,

    -- Atomic Queue Locking (prevents GitHub Actions & local PC collisions)
    locked_by           TEXT,
    locked_at           TIMESTAMPTZ,

    -- Audit timestamps
    submitted_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes for fast queue claiming, deduplication, and submitter ranking
CREATE INDEX IF NOT EXISTS idx_candidates_status_locked
    ON candidates (status, locked_at);

CREATE INDEX IF NOT EXISTS idx_candidates_submit_queue
    ON candidates (status, id_isp_accessible DESC NULLS LAST, confidence DESC NULLS LAST, created_at DESC)
    WHERE status = 'VERIFIED_ILLEGAL';

CREATE INDEX IF NOT EXISTS idx_candidates_isp_queue
    ON candidates (status, id_isp_checked_at)
    WHERE status = 'VERIFIED_ILLEGAL' AND id_isp_accessible IS NULL;

CREATE INDEX IF NOT EXISTS idx_candidates_domain
    ON candidates (domain);

-- Auto-update updated_at trigger
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_candidates_updated_at ON candidates;
CREATE TRIGGER trg_candidates_updated_at
    BEFORE UPDATE ON candidates
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

-- ==============================================================================
-- Atomic Queue Claiming: FOR UPDATE SKIP LOCKED
-- Safely allows GitHub Actions and local PC to run concurrently without conflict.
-- Also reclaims stale locks if a worker crashed mid-batch.
-- ==============================================================================
CREATE OR REPLACE FUNCTION claim_pending_candidates(
    p_worker_id TEXT,
    p_batch_size INTEGER DEFAULT 30,
    p_lock_timeout_minutes INTEGER DEFAULT 15
)
RETURNS SETOF candidates AS $$
BEGIN
    RETURN QUERY
    WITH picked AS (
        SELECT c.id
        FROM candidates c
        WHERE c.status = 'PENDING'
           OR (
               c.status = 'PROCESSING'
               AND c.locked_at < NOW() - make_interval(mins => p_lock_timeout_minutes)
           )
        ORDER BY c.created_at ASC
        LIMIT p_batch_size
        FOR UPDATE SKIP LOCKED
    )
    UPDATE candidates c
    SET
        status = 'PROCESSING',
        locked_by = p_worker_id,
        locked_at = NOW()
    FROM picked
    WHERE c.id = picked.id
    RETURNING c.*;
END;
$$ LANGUAGE plpgsql;
