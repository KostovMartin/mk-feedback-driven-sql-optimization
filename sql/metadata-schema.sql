CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS query_templates (
    template_fingerprint    text PRIMARY KEY,
    normalized_sql          text NOT NULL,
    first_seen              timestamptz NOT NULL DEFAULT now(),
    last_seen               timestamptz NOT NULL DEFAULT now(),
    execution_count         bigint NOT NULL DEFAULT 0,
    total_execution_time_ms double precision NOT NULL DEFAULT 0,
    avg_execution_time_ms   double precision NOT NULL DEFAULT 0,
    p50_execution_time_ms   double precision,
    p95_execution_time_ms   double precision,
    p99_execution_time_ms   double precision,
    max_execution_time_ms   double precision,
    is_eligible             boolean NOT NULL DEFAULT false,
    is_excluded             boolean NOT NULL DEFAULT false,
    current_candidate_id    uuid,
    tables_referenced       text[] NOT NULL DEFAULT '{}',
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_templates_eligible ON query_templates (is_eligible)
    WHERE is_eligible = true AND is_excluded = false;
CREATE INDEX IF NOT EXISTS idx_templates_priority ON query_templates (execution_count, avg_execution_time_ms DESC)
    WHERE is_eligible = true;

CREATE TABLE IF NOT EXISTS capture_records (
    id                      bigserial PRIMARY KEY,
    template_fingerprint    text NOT NULL REFERENCES query_templates(template_fingerprint),
    raw_sql                 text NOT NULL,
    bound_parameters        jsonb,
    execution_time_ms       double precision NOT NULL,
    rows_returned           integer,
    was_promoted            boolean NOT NULL DEFAULT false,
    candidate_id            uuid,
    captured_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_capture_template ON capture_records (template_fingerprint, captured_at DESC);

CREATE TABLE IF NOT EXISTS invocations (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_run_id       text NOT NULL DEFAULT 'local-run',
    template_fingerprint    text NOT NULL REFERENCES query_templates(template_fingerprint),
    trigger_type            text NOT NULL CHECK (trigger_type IN ('new_eligible', 'periodic', 'regression', 'manual')),
    model_name              text,
    model_digest            text,
    model_runtime_version   text,
    prompt_template_version text,
    prompt_hash             text,
    generation_parameters   jsonb,
    schema_snapshot_hash    text,
    candidates_generated    integer NOT NULL DEFAULT 0,
    candidates_after_dedup  integer NOT NULL DEFAULT 0,
    candidates_after_safety integer NOT NULL DEFAULT 0,
    llm_latency_ms          double precision,
    rule_latency_ms         double precision,
    started_at              timestamptz NOT NULL DEFAULT now(),
    completed_at            timestamptz
);

ALTER TABLE invocations
    ADD COLUMN IF NOT EXISTS experiment_run_id text;
ALTER TABLE invocations
    ADD COLUMN IF NOT EXISTS prompt_template_version text;
ALTER TABLE invocations
    ADD COLUMN IF NOT EXISTS prompt_hash text;

UPDATE invocations
SET experiment_run_id = COALESCE(generation_parameters->>'experiment_run_id', 'local-run')
WHERE experiment_run_id IS NULL OR experiment_run_id = '';

ALTER TABLE invocations
    ALTER COLUMN experiment_run_id SET DEFAULT 'local-run',
    ALTER COLUMN experiment_run_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_invocations_template ON invocations (template_fingerprint, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_invocations_run ON invocations (experiment_run_id, started_at DESC);

CREATE TABLE IF NOT EXISTS candidates (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    template_fingerprint    text NOT NULL REFERENCES query_templates(template_fingerprint),
    sql_text                text NOT NULL,
    canonical_hash          text NOT NULL,
    source_type             text NOT NULL CHECK (source_type IN ('rule', 'llm')),
    source_detail           text NOT NULL,
    generation_metadata     jsonb,
    schema_snapshot_hash    text,
    parent_id               uuid REFERENCES candidates(id),
    applied_rules           text[],
    invocation_id           uuid NOT NULL REFERENCES invocations(id),
    safety_status           text NOT NULL DEFAULT 'unchecked'
                            CHECK (safety_status IN ('unchecked', 'safe', 'rejected')),
    semantic_status         text NOT NULL DEFAULT 'unvalidated'
                            CHECK (semantic_status IN ('unvalidated', 'validated', 'failed')),
    promotion_status        text NOT NULL DEFAULT 'pool'
                            CHECK (promotion_status IN ('pool', 'promoted', 'demoted', 'evicted')),
    rejection_reason        text,
    rejection_layer         integer,
    benchmark_runs          integer NOT NULL DEFAULT 0,
    parameter_mapping       jsonb,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (template_fingerprint, canonical_hash)
);

CREATE INDEX IF NOT EXISTS idx_candidates_template ON candidates (template_fingerprint, promotion_status);
CREATE INDEX IF NOT EXISTS idx_candidates_promoted ON candidates (template_fingerprint)
    WHERE promotion_status = 'promoted';
CREATE INDEX IF NOT EXISTS idx_candidates_benchmarkable ON candidates (template_fingerprint)
    WHERE semantic_status = 'validated' AND promotion_status IN ('pool', 'promoted');

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_templates_current_candidate'
    ) THEN
        ALTER TABLE query_templates
            ADD CONSTRAINT fk_templates_current_candidate
            FOREIGN KEY (current_candidate_id) REFERENCES candidates(id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_capture_candidate'
    ) THEN
        ALTER TABLE capture_records
            ADD CONSTRAINT fk_capture_candidate
            FOREIGN KEY (candidate_id) REFERENCES candidates(id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS benchmark_runs (
    id                      bigserial PRIMARY KEY,
    experiment_run_id       text NOT NULL DEFAULT 'local-run',
    candidate_id            uuid REFERENCES candidates(id),
    template_fingerprint    text NOT NULL REFERENCES query_templates(template_fingerprint),
    benchmark_phase         text NOT NULL DEFAULT 'search'
                            CHECK (benchmark_phase IN ('search', 'held_out', 'baseline_calibration', 'overhead')),
    run_pair_id             uuid NOT NULL DEFAULT gen_random_uuid(),
    parameter_set_id        text,
    execution_order         integer NOT NULL DEFAULT 1
                            CHECK (execution_order > 0),
    execution_times_ms      double precision[] NOT NULL,
    mean_execution_time     double precision NOT NULL,
    median_execution_time   double precision NOT NULL,
    p75_execution_time      double precision NOT NULL,
    p95_execution_time      double precision NOT NULL,
    planning_time_ms        double precision,
    rows_returned           integer,
    rows_scanned            bigint,
    shared_hit_blocks       bigint,
    shared_read_blocks      bigint,
    temp_written_blocks     bigint,
    plan_json               jsonb,
    plan_analysis           jsonb,
    signed_improvement_pct  double precision,
    reproducibility_metadata jsonb,
    is_baseline             boolean NOT NULL DEFAULT false,
    warm_cache              boolean NOT NULL DEFAULT true,
    error_message           text,
    timed_out               boolean NOT NULL DEFAULT false,
    run_at                  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_benchmark_candidate_role CHECK (
        benchmark_phase IN ('baseline_calibration', 'overhead')
        OR (is_baseline = true AND candidate_id IS NULL)
        OR (is_baseline = false AND candidate_id IS NOT NULL)
    )
);

-- Idempotent compatibility migration for existing local metadata volumes.
-- Older smoke runs persisted only execution_times_ms and median_execution_time.
-- Backfill the new distribution columns from the median so retained rows remain
-- queryable, while new rows always store first-class summaries.
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS experiment_run_id text;
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS mean_execution_time double precision;
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS p75_execution_time double precision;
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS p95_execution_time double precision;
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS planning_time_ms double precision;
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS rows_scanned bigint;
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS shared_hit_blocks bigint;
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS shared_read_blocks bigint;
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS temp_written_blocks bigint;
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS plan_json jsonb;
ALTER TABLE benchmark_runs
    ADD COLUMN IF NOT EXISTS plan_analysis jsonb;

UPDATE benchmark_runs
SET experiment_run_id = COALESCE(reproducibility_metadata->>'experiment_run_id', 'local-run')
WHERE experiment_run_id IS NULL OR experiment_run_id = '';

UPDATE benchmark_runs
SET mean_execution_time = median_execution_time
WHERE mean_execution_time IS NULL;

UPDATE benchmark_runs
SET p75_execution_time = median_execution_time
WHERE p75_execution_time IS NULL;

UPDATE benchmark_runs
SET p95_execution_time = median_execution_time
WHERE p95_execution_time IS NULL;

ALTER TABLE benchmark_runs
    ALTER COLUMN experiment_run_id SET DEFAULT 'local-run',
    ALTER COLUMN experiment_run_id SET NOT NULL,
    ALTER COLUMN mean_execution_time SET NOT NULL,
    ALTER COLUMN p75_execution_time SET NOT NULL,
    ALTER COLUMN p95_execution_time SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_benchmark_candidate ON benchmark_runs (candidate_id, run_at DESC);
CREATE INDEX IF NOT EXISTS idx_benchmark_template ON benchmark_runs (template_fingerprint, is_baseline, run_at DESC);
CREATE INDEX IF NOT EXISTS idx_benchmark_pair ON benchmark_runs (run_pair_id, execution_order);
CREATE INDEX IF NOT EXISTS idx_benchmark_phase ON benchmark_runs (template_fingerprint, benchmark_phase, run_at DESC);
CREATE INDEX IF NOT EXISTS idx_benchmark_run ON benchmark_runs (experiment_run_id, run_at DESC);

CREATE TABLE IF NOT EXISTS bandit_state (
    candidate_id            uuid PRIMARY KEY REFERENCES candidates(id),
    template_fingerprint    text NOT NULL REFERENCES query_templates(template_fingerprint),
    strategy                text NOT NULL CHECK (strategy IN ('thompson', 'ucb1')),
    total_pulls             integer NOT NULL DEFAULT 0,
    total_reward            double precision NOT NULL DEFAULT 0,
    mean_reward             double precision NOT NULL DEFAULT 0,
    reward_variance         double precision NOT NULL DEFAULT 1.0,
    prior_pulls             double precision NOT NULL DEFAULT 0,
    alpha                   double precision NOT NULL DEFAULT 1.0,
    beta                    double precision NOT NULL DEFAULT 1.0,
    ucb_score               double precision,
    last_pulled_at          timestamptz,
    updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bandit_template ON bandit_state (template_fingerprint);

CREATE TABLE IF NOT EXISTS decisions (
    id                      bigserial PRIMARY KEY,
    template_fingerprint    text NOT NULL REFERENCES query_templates(template_fingerprint),
    decision_type           text NOT NULL CHECK (decision_type IN ('promote', 'demote', 'evict')),
    candidate_id            uuid NOT NULL REFERENCES candidates(id),
    reason                  text NOT NULL,
    baseline_median_ms      double precision,
    candidate_median_ms     double precision,
    improvement_pct         double precision,
    p_value                 double precision,
    confidence_interval     jsonb,
    benchmark_summary       jsonb,
    decided_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_decisions_template ON decisions (template_fingerprint, decided_at DESC);

CREATE TABLE IF NOT EXISTS equivalence_checks (
    id                      bigserial PRIMARY KEY,
    candidate_id            uuid NOT NULL REFERENCES candidates(id),
    check_type              text NOT NULL CHECK (check_type IN ('initial', 'revalidation')),
    passed                  boolean NOT NULL,
    method                  text NOT NULL CHECK (method IN ('full_comparison', 'hash_comparison')),
    parameter_set_ids       text[],
    checks                  jsonb,
    original_row_count      integer,
    candidate_row_count     integer,
    rows_compared           integer,
    mismatch_detail         jsonb,
    execution_time_ms       double precision,
    checked_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_equiv_candidate ON equivalence_checks (candidate_id, checked_at DESC);

CREATE TABLE IF NOT EXISTS workload_case_results (
    id                              bigserial PRIMARY KEY,
    experiment_run_id               text NOT NULL,
    workload_manifest_file          text NOT NULL,
    workload_label                  text NOT NULL,
    workload_description            text,
    query_file                      text NOT NULL,
    parameter_file                  text NOT NULL,
    held_out_parameter_file         text,
    expected_candidate_source_detail text NOT NULL,
    template_fingerprint            text,
    invocation_id                   uuid REFERENCES invocations(id),
    candidate_id                    uuid REFERENCES candidates(id),
    status                          text NOT NULL,
    outcome                         text NOT NULL,
    failure_stage                   text,
    failure_reason                  text,
    failure_detail                  text,
    search_parameter_sets           integer NOT NULL DEFAULT 0,
    held_out_parameter_sets         integer NOT NULL DEFAULT 0,
    candidates_generated            integer NOT NULL DEFAULT 0,
    candidates_returned             integer NOT NULL DEFAULT 0,
    candidates_rejected             integer NOT NULL DEFAULT 0,
    candidates_after_dedup          integer NOT NULL DEFAULT 0,
    candidates_after_safety         integer NOT NULL DEFAULT 0,
    equivalence_passed              boolean,
    benchmark_pairs                 integer NOT NULL DEFAULT 0,
    held_out_benchmark_pairs        integer NOT NULL DEFAULT 0,
    baseline_median_ms              double precision,
    candidate_median_ms             double precision,
    improvement_pct                 double precision,
    details                         jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at                      timestamptz NOT NULL,
    completed_at                    timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workload_case_results_run ON workload_case_results (experiment_run_id, started_at);
CREATE INDEX IF NOT EXISTS idx_workload_case_results_status ON workload_case_results (status, outcome);
CREATE INDEX IF NOT EXISTS idx_workload_case_results_template ON workload_case_results (template_fingerprint);

CREATE TABLE IF NOT EXISTS schema_cache (
    table_name              text NOT NULL,
    table_schema            text NOT NULL DEFAULT 'public',
    ddl_text                text NOT NULL,
    ddl_token_count         integer NOT NULL,
    estimated_row_count     bigint,
    table_size_bytes        bigint,
    columns                 jsonb NOT NULL,
    indexes                 jsonb NOT NULL,
    foreign_keys            jsonb NOT NULL,
    constraints             jsonb NOT NULL,
    column_statistics       jsonb,
    version_hash            text NOT NULL,
    refreshed_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (table_schema, table_name)
);

CREATE OR REPLACE VIEW v_template_status AS
SELECT
    qt.template_fingerprint,
    qt.normalized_sql,
    qt.execution_count,
    qt.avg_execution_time_ms AS baseline_avg_ms,
    qt.is_eligible,
    c.id AS promoted_candidate_id,
    c.sql_text AS promoted_sql,
    c.source_type AS promoted_source,
    bs.mean_reward AS promoted_reward,
    bs.total_pulls AS promoted_pulls
FROM query_templates qt
LEFT JOIN candidates c ON c.template_fingerprint = qt.template_fingerprint
    AND c.promotion_status = 'promoted'
LEFT JOIN bandit_state bs ON bs.candidate_id = c.id;

CREATE OR REPLACE VIEW v_pool_summary AS
SELECT
    template_fingerprint,
    count(*) AS total_candidates,
    count(*) FILTER (WHERE semantic_status = 'validated') AS validated,
    count(*) FILTER (WHERE semantic_status = 'failed') AS failed,
    count(*) FILTER (WHERE promotion_status = 'promoted') AS promoted,
    count(*) FILTER (WHERE source_type = 'rule') AS from_rules,
    count(*) FILTER (WHERE source_type = 'llm') AS from_llm,
    avg(benchmark_runs) AS avg_benchmark_runs
FROM candidates
WHERE promotion_status != 'evicted'
GROUP BY template_fingerprint;
