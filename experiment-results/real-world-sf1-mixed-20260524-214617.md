# Controlled Post-Hoc Analysis

Input directory: `D:\mk-feedback-driven-sql-optimization\experiment-runs\real-world-evaluation\real-world-sf1-mixed-20260524-214617`

Generated at: 2026-05-24T18:56:40.9169633Z

## Run Provenance

| Field | Value |
|-------|-------|
| Run ID | real-world-sf1-mixed-20260524-214617 |
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
| Benchmark rows | 660 |
| Valid pairs | 330 |
| Invalid pairs | 0 |
| Search pairs | 270 |
| Held-out pairs | 60 |
| Workload case rows | 10 |

## Workload Outcomes

| Status / outcome | Cases | Generated | Returned | Safety-passed | Search pairs | Held-out pairs | Failures |
|------------------|-------|-----------|----------|---------------|--------------|----------------|----------|
| completed / held_out_completed | 2 | 2 | 2 | 2 | 60 | 60 | 0 |
| completed / candidate_pool | 7 | 9 | 9 | 9 | 210 | 0 | 0 |
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
| Promoted candidates | 2 |
| Candidates rejected with reason | 0 |

## Candidate Source Breakdown

| Source | Detail | Candidates | Safe | Validated | Failed | Promoted | Validation rate % | Promotion rate % | Rejection reasons |
|--------|--------|------------|------|-----------|--------|----------|-------------------|------------------|-------------------|
| llm | llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 9 | 9 | 9 | 0 | 2 | 100.00 | 22.22 |  |
| rule | rule:in_to_exists | 1 | 1 | 1 | 0 | 0 | 100.00 | 0.00 |  |
| rule | rule:redundant_group_by_elimination | 1 | 1 | 1 | 0 | 0 | 100.00 | 0.00 |  |

## Search-Phase Evidence

| Workload | Source | Pairs | Baseline median ms | Candidate median ms | Improvement % | p-value | 95% bootstrap CI % | Promotion status |
|----------|--------|-------|--------------------|---------------------|---------------|---------|--------------------|------------------|
| real_world_rw_01_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 121.37 | 31.13 | 74.35 | 0.000000 | 64.49 to 78.60 | promoted |
| real_world_rw_02_parameterized | rule:rule:in_to_exists | 30 | 30.75 | 32.10 | -4.39 | 0.419696 | -21.06 to 47.84 | pool |
| real_world_rw_03_parameterized | rule:rule:redundant_group_by_elimination | 30 | 185.05 | 184.40 | 0.35 | 0.864497 | -1.76 to 16.58 | pool |
| real_world_rw_04_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 60.60 | 98.58 | -62.68 | 1.000000 | -66.18 to -62.09 | pool |
| real_world_rw_05_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 282.32 | 288.52 | -2.20 | 0.973868 | -3.31 to 1.33 | pool |
| real_world_rw_07_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 220.98 | 219.27 | 0.77 | 0.264553 | -1.06 to 1.49 | pool |
| real_world_rw_08_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 296.40 | 281.53 | 5.02 | 0.003556 | 2.71 to 6.99 | pool |
| real_world_rw_09_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 0.73 | 0.42 | 42.53 | 0.000002 | 19.76 to 52.68 | pool |
| real_world_rw_10_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 348.81 | 87.63 | 74.88 | 0.000000 | 58.97 to 85.41 | promoted |

## Held-Out Evidence

| Workload | Source | Pairs | Baseline median ms | Candidate median ms | Improvement % | p-value | 95% bootstrap CI % | Decision | Result |
|----------|--------|-------|--------------------|---------------------|---------------|---------|--------------------|----------|--------|
| real_world_rw_01_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 120.69 | 30.49 | 74.74 | 0.000000 | 62.23 to 79.94 | promote | positive pilot evidence |
| real_world_rw_10_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 484.24 | 88.18 | 81.79 | 0.000000 | 73.45 to 85.53 | promote | positive pilot evidence |

## Promoted Query Text

### real_world_rw_01_parameterized

| Field | Value |
|-------|-------|
| Candidate ID | 07700757-4e70-4d65-8d78-494cd8e727a7 |
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
LEFT JOIN customer c ON c.c_custkey = o.o_custkey
LEFT JOIN nation n ON n.n_nationkey = c.c_nationkey
WHERE o.o_orderdate >= $1::date
  AND o.o_orderdate < $2::date;
``

### real_world_rw_10_parameterized

| Field | Value |
|-------|-------|
| Candidate ID | 8295fc37-cf9b-4483-828d-32fc500ac456 |
| Template fingerprint | 243dcefdc17956d7008f1f26a83232dda7aee875081453bd08661d7ccdb7ea81 |
| Query file | rw_10.sql |
| Source | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 |
| Decision reason | paired_statistical_evidence |

Original SQL:

``sql
SELECT
  (
    SELECT SUM(l_extendedprice)
    FROM lineitem
    WHERE l_shipmode = $1::text
      AND l_shipdate >= $4::date
      AND l_shipdate < $5::date
  ) AS mode1_revenue,
  (
    SELECT SUM(l_extendedprice)
    FROM lineitem
    WHERE l_shipmode = $2::text
      AND l_shipdate >= $4::date
      AND l_shipdate < $5::date
  ) AS mode2_revenue,
  (
    SELECT SUM(l_extendedprice)
    FROM lineitem
    WHERE l_shipmode = $3::text
      AND l_shipdate >= $4::date
      AND l_shipdate < $5::date
  ) AS mode3_revenue;

``

Promoted SQL:

``sql
SELECT
  SUM(l_extendedprice) FILTER (WHERE l_shipmode = $1::text) AS mode1_revenue,
  SUM(l_extendedprice) FILTER (WHERE l_shipmode = $2::text) AS mode2_revenue,
  SUM(l_extendedprice) FILTER (WHERE l_shipmode = $3::text) AS mode3_revenue
FROM lineitem
WHERE l_shipdate >= $4::date
  AND l_shipdate < $5::date;
``

## H1 Improvement

Attempted templates used for hit-rate denominator: 10

| Workload | Pairs | Held-out improvement % | p-value | 95% bootstrap CI % | Result |
|----------|-------|------------------------|---------|--------------------|--------|
| real_world_rw_01_parameterized | 30 | 74.74 | 0.000000 | 62.23 to 79.94 | positive pilot evidence |
| real_world_rw_10_parameterized | 30 | 81.79 | 0.000000 | 73.45 to 85.53 | positive pilot evidence |

## Equivalence Evidence

| Metric | Value |
|--------|-------|
| Equivalence checks | 11 |
| Passed checks | 11 |
| Failed checks | 0 |

| Method | Passed | Checks | Rows compared total | Rows compared median | Median execution ms |
|--------|--------|--------|---------------------|----------------------|---------------------|
| full_comparison | True | 11 | 244325 | 30000 | 1208.34 |

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
| real_world_rw_10_parameterized | completed / held_out_completed |  |  | 1 | 1 | 1 | 30 |

No rejected, failed, demoted, or evicted candidate rows were exported.

## Monitoring Evidence

| Metric | Value |
|--------|-------|
| Monitoring enabled | True |
| Metrics path | metrics |
| Metrics files | 6 |

| File | Size bytes |
|------|------------|
| docker-containers.csv | 533895 |
| gpu.csv | 97727 |
| host.csv | 79012 |
| monitoring-manifest.json | 7116 |
| prometheus-window.json | 7406 |
| rewrite-service.csv | 37423 |

## H2-H5 Status

| Hypothesis | Status | Reason |
|------------|--------|--------|
| H2 convergence | not_estimated | Convergence requires repeated non-deterministic invocations and a growing candidate pool. |
| H3 empirical regret | partial_evidence_available | Empirical regret needs multiple benchmark-selection rounds over at least two validated candidates per template. |
| H4 model scale | candidate_source_summary_available | Model-scale claims require comparable local model runs at a fixed invocation budget. |
| H5 complementarity | plan_artifacts_available | Complementarity needs EXPLAIN/plan evidence and optional PostgreSQL planner-toggle probes. |

This report is a post-hoc analysis artifact. It does not turn a controlled pilot into a public benchmark result.
