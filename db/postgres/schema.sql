CREATE TABLE IF NOT EXISTS atlax_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS subscribers (
    chat_id TEXT PRIMARY KEY,
    username TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT FALSE,
    subscription_status TEXT NOT NULL DEFAULT 'inactive',
    style TEXT NOT NULL DEFAULT '',
    subscription_expires_at TIMESTAMPTZ,
    record JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_subscribers_active
    ON subscribers (active, subscription_expires_at);

CREATE TABLE IF NOT EXISTS invite_codes (
    code TEXT PRIMARY KEY,
    used_by TEXT,
    expires_at TIMESTAMPTZ,
    record JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS payment_intents (
    intent_id TEXT PRIMARY KEY,
    reference TEXT NOT NULL UNIQUE,
    chat_id TEXT NOT NULL,
    status TEXT NOT NULL,
    method TEXT NOT NULL,
    style TEXT NOT NULL DEFAULT '',
    amount_usd NUMERIC(18, 2) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL,
    record JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_payment_intents_status
    ON payment_intents (status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_payment_intents_chat
    ON payment_intents (chat_id, created_at DESC);

CREATE TABLE IF NOT EXISTS journal_entries (
    record_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    profile TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    confidence_breakdown JSONB NOT NULL DEFAULT '{}'::jsonb,
    lot_size NUMERIC,
    entry_price NUMERIC,
    sl_price NUMERIC,
    tp_price NUMERIC,
    exit_price NUMERIC,
    pnl NUMERIC,
    closed_at TIMESTAMPTZ NOT NULL,
    exit_reason TEXT NOT NULL DEFAULT '',
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    trader_notes TEXT NOT NULL DEFAULT '',
    signal_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    invalidation_reason TEXT NOT NULL DEFAULT '',
    final_outcome TEXT NOT NULL DEFAULT 'UNKNOWN',
    created_at TIMESTAMPTZ,
    outcome_at TIMESTAMPTZ,
    atr_method TEXT NOT NULL DEFAULT '',
    atr_period INTEGER,
    atr_value NUMERIC,
    parent_range NUMERIC,
    parent_atr_ratio NUMERIC,
    sweep_depth NUMERIC,
    sweep_atr_ratio NUMERIC,
    atr_grade TEXT,
    atr_configuration_snapshot_id TEXT NOT NULL DEFAULT '',
    atr_evaluation_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
    atr_explanation TEXT NOT NULL DEFAULT '',
    key_level_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
    key_level_zone BOOLEAN,
    key_level_match_count INTEGER,
    key_level_configuration_snapshot_id TEXT NOT NULL DEFAULT '',
    key_level_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    key_level_explanation TEXT NOT NULL DEFAULT '',
    volume_profile_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE',
    volume_profile_period TEXT,
    volume_profile_source_timeframe TEXT,
    volume_profile_volume_source TEXT,
    volume_profile_volume_quality TEXT,
    volume_profile_poc TEXT,
    volume_profile_vah TEXT,
    volume_profile_val TEXT,
    volume_profile_total_volume TEXT,
    volume_profile_source_bars TEXT,
    volume_profile_configuration_snapshot_id TEXT NOT NULL DEFAULT '',
    volume_profile_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    volume_profile_unknown_reason TEXT
);

ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS atr_method TEXT NOT NULL DEFAULT '';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS atr_period INTEGER;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS atr_value NUMERIC;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS parent_range NUMERIC;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS parent_atr_ratio NUMERIC;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS sweep_depth NUMERIC;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS sweep_atr_ratio NUMERIC;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS atr_grade TEXT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS atr_configuration_snapshot_id TEXT NOT NULL DEFAULT '';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS atr_evaluation_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS atr_explanation TEXT NOT NULL DEFAULT '';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS key_level_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS key_level_zone BOOLEAN;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS key_level_match_count INTEGER;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS key_level_configuration_snapshot_id TEXT NOT NULL DEFAULT '';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS key_level_evidence JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS key_level_explanation TEXT NOT NULL DEFAULT '';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_period TEXT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_source_timeframe TEXT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_volume_source TEXT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_volume_quality TEXT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_poc TEXT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_vah TEXT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_val TEXT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_total_volume TEXT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_source_bars TEXT;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_configuration_snapshot_id TEXT NOT NULL DEFAULT '';
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_evidence JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE journal_entries ADD COLUMN IF NOT EXISTS volume_profile_unknown_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_journal_profile
    ON journal_entries (profile);

CREATE INDEX IF NOT EXISTS idx_journal_symbol
    ON journal_entries (symbol);

CREATE INDEX IF NOT EXISTS idx_journal_created_at
    ON journal_entries (created_at DESC);

CREATE TABLE IF NOT EXISTS signal_replay_snapshots (
    snapshot_id TEXT NOT NULL UNIQUE,
    signal_id TEXT NOT NULL,
    snapshot_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    source_timeframe TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    formation_cutoff_at TIMESTAMPTZ NOT NULL,
    context_bars_before_parent INTEGER NOT NULL,
    outcome_forward_bars INTEGER NOT NULL,
    formation_context JSONB NOT NULL,
    higher_timeframe_context JSONB NOT NULL,
    outcome_context JSONB NOT NULL,
    rule_context JSONB NOT NULL,
    snapshot_hash TEXT NOT NULL,
    availability TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL,
    final_outcome TEXT NOT NULL,
    outcome_at TIMESTAMPTZ,
    invalidation_reason TEXT NOT NULL DEFAULT '',
    failure_reason TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (signal_id, snapshot_version)
);

CREATE INDEX IF NOT EXISTS idx_replay_signal
    ON signal_replay_snapshots (signal_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS execution_records (
    execution_record_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    lot_size NUMERIC,
    risk_evaluation_id TEXT NOT NULL,
    mt5_request JSONB,
    preflight_result TEXT,
    order_send_retcode INTEGER,
    mt5_order_id BIGINT,
    mt5_deal_id BIGINT,
    broker_comment TEXT,
    execution_status TEXT NOT NULL,
    error_reason TEXT,
    recovery_suggestion TEXT,
    attempted_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    mt5_position_id BIGINT,
    realized_status TEXT NOT NULL DEFAULT 'PENDING',
    realized_gross_pnl NUMERIC,
    realized_costs NUMERIC,
    realized_net_pnl NUMERIC,
    realized_deals_count INTEGER NOT NULL DEFAULT 0,
    realized_reason TEXT NOT NULL DEFAULT '',
    realized_at TIMESTAMPTZ
);

ALTER TABLE execution_records ADD COLUMN IF NOT EXISTS mt5_position_id BIGINT;
ALTER TABLE execution_records ADD COLUMN IF NOT EXISTS realized_status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE execution_records ADD COLUMN IF NOT EXISTS realized_gross_pnl NUMERIC;
ALTER TABLE execution_records ADD COLUMN IF NOT EXISTS realized_costs NUMERIC;
ALTER TABLE execution_records ADD COLUMN IF NOT EXISTS realized_net_pnl NUMERIC;
ALTER TABLE execution_records ADD COLUMN IF NOT EXISTS realized_deals_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE execution_records ADD COLUMN IF NOT EXISTS realized_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE execution_records ADD COLUMN IF NOT EXISTS realized_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_execution_candidate
    ON execution_records (candidate_id);

CREATE INDEX IF NOT EXISTS idx_execution_status
    ON execution_records (execution_status);

CREATE TABLE IF NOT EXISTS execution_quality_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    execution_record_id TEXT NOT NULL DEFAULT '',
    candidate_id TEXT NOT NULL DEFAULT '',
    profile TEXT NOT NULL DEFAULT '',
    symbol TEXT NOT NULL DEFAULT '',
    timeframe TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT '',
    position_id BIGINT,
    requested_price NUMERIC,
    actual_price NUMERIC,
    requested_volume NUMERIC,
    actual_volume NUMERIC,
    spread NUMERIC,
    slippage NUMERIC,
    stop_loss NUMERIC,
    take_profit NUMERIC,
    broker_retcode INTEGER,
    broker_comment TEXT,
    action TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'UNKNOWN',
    latency_ms INTEGER,
    gross_pnl NUMERIC,
    costs NUMERIC,
    net_pnl NUMERIC,
    reason TEXT NOT NULL DEFAULT '',
    occurred_at TIMESTAMPTZ NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_quality_execution
    ON execution_quality_events (execution_record_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_quality_type
    ON execution_quality_events (event_type, occurred_at DESC);

CREATE TABLE IF NOT EXISTS approval_queue (
    candidate_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    profile TEXT NOT NULL DEFAULT '',
    expires_at TIMESTAMPTZ,
    request JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_id BIGSERIAL PRIMARY KEY,
    event_hash TEXT UNIQUE,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_chat_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target_chat_id TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT 'success',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp
    ON audit_events (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_audit_events_action
    ON audit_events (action, timestamp DESC);

ALTER TABLE audit_events
    ADD COLUMN IF NOT EXISTS event_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_audit_events_event_hash
    ON audit_events (event_hash)
    WHERE event_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS runtime_state (
    state_key TEXT PRIMARY KEY,
    state_value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO atlax_schema_migrations (version)
VALUES ('001_initial_shared_storage')
ON CONFLICT (version) DO NOTHING;
