# Controlled Post-Hoc Analysis

Input directory: `D:\mk-feedback-driven-sql-optimization\experiment-runs\real-world-evaluation\real-world-sf1-mixed-20260524-085401`

Generated at: 2026-05-24T06:04:36.0690890Z

## Run Provenance

| Field | Value |
|-------|-------|
| Run ID | real-world-sf1-mixed-20260524-085401 |
| Candidate source | mixed |
| Model | qwen3.6:35b-a3b-q4_K_M |
| Scale factor | 1 |
| Workload manifest | /app/tpch/real-world/real-world-mixed-corpus.json |
| Search parameter sets/query | 70 |
| Held-out parameter sets/query | 30 |
| Benchmark iterations | 1 |
| Minimum promotion pairs | 30 |
| Promotion alpha | 0.05 |
| Promotion improvement threshold % | 2.0 |

## Integrity

| Metric | Value |
|--------|-------|
| Benchmark rows | 600 |
| Valid pairs | 300 |
| Invalid pairs | 0 |
| Search pairs | 270 |
| Held-out pairs | 30 |
| Workload case rows | 10 |

## Workload Outcomes

| Status / outcome | Cases | Generated | Returned | Safety-passed | Search pairs | Held-out pairs | Failures |
|------------------|-------|-----------|----------|---------------|--------------|----------------|----------|
| completed / held_out_completed | 1 | 1 | 1 | 1 | 30 | 30 | 0 |
| completed / candidate_pool | 8 | 10 | 10 | 10 | 240 | 0 | 0 |
| completed / no_candidate | 1 | 1 | 0 | 0 | 0 | 0 | 0 |

## Candidate Funnel

| Stage | Count |
|-------|-------|
| Workload cases | 10 |
| Invocations | 10 |
| Candidates generated from workload cases | 12 |
| Candidates returned from workload cases | 11 |
| Candidates rejected from workload cases | 1 |
| Candidates after dedup from workload cases | 11 |
| Candidates after safety from workload cases | 11 |
| Candidate rows recorded | 11 |
| Safe candidates | 11 |
| Semantically validated candidates | 11 |
| Semantically failed candidates | 0 |
| Promoted candidates | 1 |
| Candidates rejected with reason | 0 |

## Candidate Source Breakdown

| Source | Detail | Candidates | Safe | Validated | Failed | Promoted | Validation rate % | Promotion rate % | Rejection reasons |
|--------|--------|------------|------|-----------|--------|----------|-------------------|------------------|-------------------|
| llm | llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 9 | 9 | 9 | 0 | 1 | 100.00 | 11.11 |  |
| rule | rule:in_to_exists | 1 | 1 | 1 | 0 | 0 | 100.00 | 0.00 |  |
| rule | rule:redundant_group_by_elimination | 1 | 1 | 1 | 0 | 0 | 100.00 | 0.00 |  |

## Search-Phase Evidence

| Workload | Source | Pairs | Baseline median ms | Candidate median ms | Improvement % | p-value | 95% bootstrap CI % | Promotion status |
|----------|--------|-------|--------------------|---------------------|---------------|---------|--------------------|------------------|
| real_world_rw_01_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 124.32 | 32.48 | 73.87 | 0.000000 | 64.68 to 77.56 | promoted |
| real_world_rw_02_parameterized | rule:rule:in_to_exists | 30 | 32.99 | 33.69 | -2.12 | 0.380533 | -14.04 to 46.76 | pool |
| real_world_rw_03_parameterized | rule:rule:redundant_group_by_elimination | 30 | 189.52 | 190.80 | -0.68 | 0.940462 | -9.11 to 0.57 | pool |
| real_world_rw_04_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 62.77 | 102.93 | -63.97 | 1.000000 | -68.16 to -59.05 | pool |
| real_world_rw_05_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 303.78 | 305.22 | -0.47 | 0.596085 | -7.65 to 3.09 | pool |
| real_world_rw_07_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 242.25 | 241.85 | 0.16 | 0.556403 | -1.40 to 1.60 | pool |
| real_world_rw_08_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 316.17 | 302.91 | 4.20 | 0.000365 | 1.07 to 6.42 | pool |
| real_world_rw_09_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 0.70 | 0.63 | 9.36 | 0.085299 | -18.73 to 25.41 | pool |
| real_world_rw_10_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 403.33 | 282.43 | 29.98 | 0.000156 | -26.40 to 57.00 | pool |

## Held-Out Evidence

| Workload | Source | Pairs | Baseline median ms | Candidate median ms | Improvement % | p-value | 95% bootstrap CI % | Decision | Result |
|----------|--------|-------|--------------------|---------------------|---------------|---------|--------------------|----------|--------|
| real_world_rw_01_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 126.32 | 33.77 | 73.27 | 0.000000 | 62.05 to 79.30 | promote | positive pilot evidence |

## Promoted Query Text

### real_world_rw_01_parameterized

| Field | Value |
|-------|-------|
| Candidate ID | 4d041602-dbfa-46e3-ae58-4df2356af97e |
| Template fingerprint | 8389a004eea95458ea560aed44b0759e45fb3b8fb41892d7cc67bdf9f513680c |
| Query file | rw_01.sql |
| Source | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 |
| Decision reason | paired_statistical_evidence |

Original SQL:

``sql
SELECT
  o.o_orderkey,
  o.o_totalprice,
  (SELECT c.c_name FROM customer c WHERE c.c_custkey = o.o_custkey) AS customer_name,
  (
    SELECT n.n_name
    FROM nation n
    JOIN customer c2 ON c2.c_nationkey = n.n_nationkey
    WHERE c2.c_custkey = o.o_custkey
  ) AS nation_name
FROM orders o
WHERE o.o_orderdate >= $1::date
  AND o.o_orderdate < $2::date;

``

Promoted SQL:

``sql
SELECT
  o.o_orderkey,
  o.o_totalprice,
  c.c_name AS customer_name,
  n.n_name AS nation_name
FROM orders o
JOIN customer c ON c.c_custkey = o.o_custkey
JOIN nation n ON n.n_nationkey = c.c_nationkey
WHERE o.o_orderdate >= $1::date
  AND o.o_orderdate < $2::date;
``

## H1 Improvement

Attempted templates used for hit-rate denominator: 10

| Workload | Pairs | Held-out improvement % | p-value | 95% bootstrap CI % | Result |
|----------|-------|------------------------|---------|--------------------|--------|
| real_world_rw_01_parameterized | 30 | 73.27 | 0.000000 | 62.05 to 79.30 | positive pilot evidence |

## Equivalence Evidence

| Metric | Value |
|--------|-------|
| Equivalence checks | 11 |
| Passed checks | 11 |
| Failed checks | 0 |

| Method | Passed | Checks | Rows compared total | Rows compared median | Median execution ms |
|--------|--------|--------|---------------------|----------------------|---------------------|
| full_comparison | True | 11 | 244325 | 30000 | 1237.07 |

## Negative and Null Outcomes

Workload cases that did not produce a promoted outcome:

| Workload | Status / outcome | Failure stage | Failure reason | Generated | Returned | Safety-passed | Benchmark pairs |
|----------|------------------|---------------|----------------|-----------|----------|---------------|-----------------|
| real_world_rw_01_parameterized | completed / held_out_completed |  |  | 1 | 1 | 1 | 30 |
| real_world_rw_02_parameterized | completed / candidate_pool |  |  | 2 | 2 | 2 | 30 |
| real_world_rw_03_parameterized | completed / candidate_pool |  |  | 2 | 2 | 2 | 30 |
| real_world_rw_04_parameterized | completed / candidate_pool |  |  | 1 | 1 | 1 | 30 |
| real_world_rw_05_parameterized | completed / candidate_pool |  |  | 1 | 1 | 1 | 30 |
| real_world_rw_06_parameterized | completed / no_candidate |  |  | 1 | 0 | 0 | 0 |
| real_world_rw_07_parameterized | completed / candidate_pool |  |  | 1 | 1 | 1 | 30 |
| real_world_rw_08_parameterized | completed / candidate_pool |  |  | 1 | 1 | 1 | 30 |
| real_world_rw_09_parameterized | completed / candidate_pool |  |  | 1 | 1 | 1 | 30 |
| real_world_rw_10_parameterized | completed / candidate_pool |  |  | 1 | 1 | 1 | 30 |

No rejected, failed, demoted, or evicted candidate rows were exported.

## Monitoring Evidence

| Metric | Value |
|--------|-------|
| Monitoring enabled | True |
| Metrics path | metrics |
| Metrics files | 6 |

| File | Size bytes |
|------|------------|
| docker-containers.csv | 545489 |
| gpu.csv | 99378 |
| host.csv | 80661 |
| monitoring-manifest.json | 7116 |
| prometheus-window.json | 7406 |
| rewrite-service.csv | 37491 |

## H2-H5 Status

| Hypothesis | Status | Reason |
|------------|--------|--------|
| H2 convergence | not_estimated | Convergence requires repeated non-deterministic invocations and a growing candidate pool. |
| H3 empirical regret | partial_evidence_available | Empirical regret needs multiple benchmark-selection rounds over at least two validated candidates per template. |
| H4 model scale | candidate_source_summary_available | Model-scale claims require comparable local model runs at a fixed invocation budget. |
| H5 complementarity | plan_artifacts_available | Complementarity needs EXPLAIN/plan evidence and optional PostgreSQL planner-toggle probes. |

This report is a post-hoc analysis artifact. It does not turn a controlled pilot into a public benchmark result.
