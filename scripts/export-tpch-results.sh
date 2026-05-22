#!/bin/sh
set -eu

export PGPASSWORD="${METADATA_DB_PASSWORD}"
connection="host=${METADATA_DB_HOST} port=${METADATA_DB_PORT} dbname=${METADATA_DB_NAME} user=${METADATA_DB_USER}"

for attempt in $(seq 1 60); do
  if pg_isready -h "${METADATA_DB_HOST}" -p "${METADATA_DB_PORT}" -U "${METADATA_DB_USER}" -d "${METADATA_DB_NAME}"; then
    break
  fi
  if [ "${attempt}" -eq 60 ]; then
    echo "Timed out waiting for metadata database at ${METADATA_DB_HOST}:${METADATA_DB_PORT}." >&2
    exit 1
  fi
  sleep 1
done

export_dir="/workspace/results/${TPCH_RESULT_RUN_ID}"
mkdir -p "${export_dir}"
sql_run_id=$(printf "%s" "${TPCH_RESULT_RUN_ID}" | sed "s/'/''/g")
run_id_literal="'${sql_run_id}'"

run_psql() {
  psql "${connection}" -v ON_ERROR_STOP=1 -c "$1"
}

run_invocations="SELECT id, template_fingerprint FROM invocations WHERE experiment_run_id = ${run_id_literal}"

run_psql "\copy (
WITH run_invocations AS (${run_invocations}),
run_templates AS (
  SELECT template_fingerprint FROM run_invocations
  UNION
  SELECT template_fingerprint FROM workload_case_results
  WHERE experiment_run_id = ${run_id_literal} AND template_fingerprint IS NOT NULL
)
SELECT qt.*
FROM query_templates qt
WHERE qt.template_fingerprint IN (SELECT template_fingerprint FROM run_templates)
ORDER BY qt.first_seen
) TO '${export_dir}/query_templates.csv' WITH CSV HEADER"

run_psql "\copy (
SELECT *
FROM invocations
WHERE experiment_run_id = ${run_id_literal}
ORDER BY started_at
) TO '${export_dir}/invocations.csv' WITH CSV HEADER"

run_psql "\copy (
WITH run_invocations AS (${run_invocations})
SELECT c.*
FROM candidates c
JOIN run_invocations i ON i.id = c.invocation_id
ORDER BY c.created_at
) TO '${export_dir}/candidates.csv' WITH CSV HEADER"

run_psql "\copy (
WITH run_invocations AS (${run_invocations})
SELECT e.*
FROM equivalence_checks e
JOIN candidates c ON c.id = e.candidate_id
JOIN run_invocations i ON i.id = c.invocation_id
ORDER BY e.checked_at
) TO '${export_dir}/equivalence_checks.csv' WITH CSV HEADER"

run_psql "\copy (
SELECT *
FROM benchmark_runs
WHERE experiment_run_id = ${run_id_literal}
ORDER BY run_at, run_pair_id, execution_order
) TO '${export_dir}/benchmark_runs.csv' WITH CSV HEADER"

run_psql "\copy (
SELECT *
FROM workload_case_results
WHERE experiment_run_id = ${run_id_literal}
ORDER BY started_at
) TO '${export_dir}/workload_case_results.csv' WITH CSV HEADER"

run_psql "\copy (
WITH run_invocations AS (${run_invocations})
SELECT bs.*
FROM bandit_state bs
JOIN candidates c ON c.id = bs.candidate_id
JOIN run_invocations i ON i.id = c.invocation_id
ORDER BY bs.updated_at
) TO '${export_dir}/bandit_state.csv' WITH CSV HEADER"

run_psql "\copy (
WITH run_invocations AS (${run_invocations})
SELECT d.*
FROM decisions d
JOIN candidates c ON c.id = d.candidate_id
JOIN run_invocations i ON i.id = c.invocation_id
ORDER BY d.decided_at
) TO '${export_dir}/decisions.csv' WITH CSV HEADER"

run_psql "\copy (
WITH run_invocations AS (${run_invocations})
SELECT
  c.template_fingerprint,
  count(*) AS total_candidates,
  count(*) FILTER (WHERE c.semantic_status = 'validated') AS validated,
  count(*) FILTER (WHERE c.semantic_status = 'failed') AS failed,
  count(*) FILTER (WHERE c.promotion_status = 'promoted') AS promoted,
  count(*) FILTER (WHERE c.source_type = 'rule') AS from_rules,
  count(*) FILTER (WHERE c.source_type = 'llm') AS from_llm,
  avg(c.benchmark_runs) AS avg_benchmark_runs
FROM candidates c
JOIN run_invocations i ON i.id = c.invocation_id
WHERE c.promotion_status != 'evicted'
GROUP BY c.template_fingerprint
ORDER BY c.template_fingerprint
) TO '${export_dir}/pool_summary.csv' WITH CSV HEADER"

echo "TPC-H metadata exports for ${TPCH_RESULT_RUN_ID} written to ${export_dir}."
