# Controlled Post-Hoc Analysis

Input directory: `experiment-runs\real-world-evaluation\real-world-sf1-mixed-20260522-221945`

Generated at: 2026-05-23T05:15:24.0569119Z

## Run Provenance

| Field | Value |
|-------|-------|
| Run ID | real-world-sf1-mixed-20260522-221945 |
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
| Search pairs | 240 |
| Held-out pairs | 60 |
| Workload case rows | 10 |

## Workload Outcomes

| Status / outcome | Cases | Generated | Returned | Safety-passed | Search pairs | Held-out pairs | Failures |
|------------------|-------|-----------|----------|---------------|--------------|----------------|----------|
| completed / held_out_completed | 2 | 2 | 2 | 2 | 60 | 60 | 0 |
| completed / candidate_pool | 6 | 8 | 8 | 8 | 180 | 0 | 0 |
| completed / no_candidate | 1 | 1 | 0 | 0 | 0 | 0 | 0 |
| completed / no_validated_candidate | 1 | 1 | 1 | 1 | 0 | 0 | 0 |

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
| Safe candidates | 10 |
| Semantically validated candidates | 10 |
| Semantically failed candidates | 1 |
| Promoted candidates | 2 |
| Candidates rejected with reason | 0 |

## Candidate Source Breakdown

| Source | Detail | Candidates | Safe | Validated | Failed | Promoted | Validation rate % | Promotion rate % | Rejection reasons |
|--------|--------|------------|------|-----------|--------|----------|-------------------|------------------|-------------------|
| llm | llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 9 | 8 | 8 | 1 | 2 | 88.89 | 22.22 |  |
| rule | rule:in_to_exists | 1 | 1 | 1 | 0 | 0 | 100.00 | 0.00 |  |
| rule | rule:redundant_group_by_elimination | 1 | 1 | 1 | 0 | 0 | 100.00 | 0.00 |  |

## Search-Phase Evidence

| Workload | Source | Pairs | Baseline median ms | Candidate median ms | Improvement % | p-value | 95% bootstrap CI % | Promotion status |
|----------|--------|-------|--------------------|---------------------|---------------|---------|--------------------|------------------|
| real_world_rw_01_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 146.01 | 38.33 | 73.75 | 0.000000 | 63.19 to 77.74 | promoted |
| real_world_rw_02_parameterized | rule:rule:in_to_exists | 30 | 41.07 | 39.42 | 4.02 | 0.342524 | -21.43 to 45.21 | pool |
| real_world_rw_03_parameterized | rule:rule:redundant_group_by_elimination | 30 | 207.60 | 206.72 | 0.42 | 0.664910 | -1.90 to 10.04 | pool |
| real_world_rw_04_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 68.46 | 113.30 | -65.51 | 1.000000 | -70.21 to -63.00 | pool |
| real_world_rw_05_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 337.49 | 332.75 | 1.40 | 0.033332 | -3.21 to 5.66 | pool |
| real_world_rw_07_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 264.82 | 265.94 | -0.42 | 0.932632 | -2.57 to 0.38 | pool |
| real_world_rw_08_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 368.22 | 354.24 | 3.80 | 0.000616 | 1.18 to 7.90 | pool |
| real_world_rw_10_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 516.20 | 114.16 | 77.89 | 0.000000 | 63.00 to 85.64 | promoted |

## Held-Out Evidence

| Workload | Source | Pairs | Baseline median ms | Candidate median ms | Improvement % | p-value | 95% bootstrap CI % | Decision | Result |
|----------|--------|-------|--------------------|---------------------|---------------|---------|--------------------|----------|--------|
| real_world_rw_01_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 140.92 | 37.23 | 73.58 | 0.000000 | 57.80 to 77.90 | promote | positive pilot evidence |
| real_world_rw_10_parameterized | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | 30 | 629.58 | 114.13 | 81.87 | 0.000000 | 75.19 to 85.70 | promote | positive pilot evidence |

## Promoted Query Text

### real_world_rw_01_parameterized

| Field | Value |
|-------|-------|
| Candidate ID | 0bab2b83-4daa-48d9-ac82-6a02f249fa67 |
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
| Candidate ID | 9df99dc5-7808-4626-bbd9-90ef92fd7817 |
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
| real_world_rw_01_parameterized | 30 | 73.58 | 0.000000 | 57.80 to 77.90 | positive pilot evidence |
| real_world_rw_10_parameterized | 30 | 81.87 | 0.000000 | 75.19 to 85.70 | positive pilot evidence |

## Equivalence Evidence

| Metric | Value |
|--------|-------|
| Equivalence checks | 11 |
| Passed checks | 10 |
| Failed checks | 1 |

| Method | Passed | Checks | Rows compared total | Rows compared median | Median execution ms |
|--------|--------|--------|---------------------|----------------------|---------------------|
| full_comparison | True | 10 | 244297 | 32359 | 1428.10 |
| full_comparison | False | 1 | 28 | 28 | 6.13 |

Failed equivalence checks:

| Candidate | Method | Check type | Original rows | Candidate rows | Rows compared | Mismatch detail |
|-----------|--------|------------|---------------|----------------|---------------|-----------------|
| b826e2de-82fe-42ed-9564-07dfc52da730 | full_comparison | initial | 28 | 27 | 28 | {"reason": "row_multiset_mismatch", "only_in_original": [["int:1375842", "date:1996-11-11", "date:1996-09-28"], ["int:4791589", "date:1996-09-28", "date:1996-07-01"]], "only_in_candidate": [["int:4791589", "date:1996-09-28", "date:1996-09-28"]]} |

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
| real_world_rw_09_parameterized | completed / no_validated_candidate |  |  | 1 | 1 | 1 | 0 |
| real_world_rw_10_parameterized | completed / held_out_completed |  |  | 1 | 1 | 1 | 30 |

Rejected, failed, demoted, or evicted candidates:

| Candidate | Template | Source | Semantic status | Promotion status | Rejection reason |
|-----------|----------|--------|-----------------|------------------|------------------|
| b826e2de-82fe-42ed-9564-07dfc52da730 | a85e835a1423dec3217d4271c4f21e8ac8da6e0760c74391038023512628455d | llm:llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1 | failed | pool |  |

## Monitoring Evidence

| Metric | Value |
|--------|-------|
| Monitoring enabled | True |
| Metrics path | metrics |
| Metrics files | 6 |

| File | Size bytes |
|------|------------|
| docker-containers.csv | 636401 |
| gpu.csv | 106166 |
| host.csv | 92846 |
| monitoring-manifest.json | 7116 |
| prometheus-window.json | 7406 |
| rewrite-service.csv | 41337 |

## H2-H5 Status

| Hypothesis | Status | Reason |
|------------|--------|--------|
| H2 convergence | not_estimated | Convergence requires repeated non-deterministic invocations and a growing candidate pool. |
| H3 empirical regret | partial_evidence_available | Empirical regret needs multiple benchmark-selection rounds over at least two validated candidates per template. |
| H4 model scale | candidate_source_summary_available | Model-scale claims require comparable local model runs at a fixed invocation budget. |
| H5 complementarity | plan_artifacts_available | Complementarity needs EXPLAIN/plan evidence and optional PostgreSQL planner-toggle probes. |

This report is a post-hoc analysis artifact. It does not turn a controlled pilot into a public benchmark result.
