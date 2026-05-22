from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REAL_WORLD_ROOT = "real-world"
QUERY_DIR = f"{REAL_WORLD_ROOT}/queries"
PARAMETER_DIR = f"{REAL_WORLD_ROOT}/parameters"
BASELINE_MANIFEST = f"{REAL_WORLD_ROOT}/real-world-baseline-corpus.json"
RULE_MANIFEST = f"{REAL_WORLD_ROOT}/real-world-rule-corpus.json"
LLM_MANIFEST = f"{REAL_WORLD_ROOT}/real-world-local-llm-corpus.json"
MIXED_MANIFEST = f"{REAL_WORLD_ROOT}/real-world-mixed-corpus.json"
METADATA_FILE = f"{REAL_WORLD_ROOT}/REAL_WORLD_METADATA.json"
MIXED_CANDIDATE_SELECTOR = "candidate-pool"
SEARCH_PARAMETER_COUNT = 70
HELD_OUT_PARAMETER_COUNT = 30

SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]
REGIONS = ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]
SHIP_MODES = ["REG AIR", "AIR", "RAIL", "SHIP", "TRUCK", "MAIL", "FOB"]
ORDER_PRIORITIES = ["1-URGENT", "2-HIGH", "3-MEDIUM", "4-NOT SPECIFIED", "5-LOW"]
PART_TYPE_PATTERNS = ["%BRASS", "%STEEL", "%COPPER", "PROMO%", "STANDARD%"]


@dataclass(frozen=True)
class ParameterValue:
    position: str
    type: str
    value: Any


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    title: str
    sql: str

    @property
    def file_name(self) -> str:
        return f"{self.scenario_id.lower().replace('-', '_')}.sql"

    @property
    def label(self) -> str:
        return f"real_world_{self.scenario_id.lower().replace('-', '_')}_parameterized"


def write_real_world_workload(scale_root: Path, *, scale_factor: str, seed: int = 20260512) -> None:
    query_dir = scale_root / QUERY_DIR
    parameter_dir = scale_root / PARAMETER_DIR
    query_dir.mkdir(parents=True, exist_ok=True)
    parameter_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    baseline_manifest = []
    rule_manifest = []
    llm_manifest = []
    mixed_manifest = []

    for spec in _scenario_specs():
        (query_dir / spec.file_name).write_text(spec.sql.strip() + "\n", encoding="utf-8", newline="\n")
        search_params = _parameter_sets(spec.scenario_id, "search", SEARCH_PARAMETER_COUNT, rng, scale_factor)
        held_out_params = _parameter_sets(spec.scenario_id, "held-out", HELD_OUT_PARAMETER_COUNT, rng, scale_factor)
        search_file = f"{spec.scenario_id.lower().replace('-', '_')}_search_params.json"
        held_out_file = f"{spec.scenario_id.lower().replace('-', '_')}_held_out_params.json"
        _write_json(parameter_dir / search_file, search_params)
        _write_json(parameter_dir / held_out_file, held_out_params)

        baseline_manifest.append(_manifest_entry(spec, search_file, held_out_file, "baseline-only", scale_factor))
        rule_manifest.append(_manifest_entry(spec, search_file, held_out_file, "first-rule-candidate", scale_factor))
        llm_manifest.append(_manifest_entry(spec, search_file, held_out_file, "first-llm-candidate", scale_factor))
        mixed_manifest.append(
            _manifest_entry(spec, search_file, held_out_file, MIXED_CANDIDATE_SELECTOR, scale_factor)
        )

    _write_json(scale_root / BASELINE_MANIFEST, baseline_manifest)
    _write_json(scale_root / RULE_MANIFEST, rule_manifest)
    _write_json(scale_root / LLM_MANIFEST, llm_manifest)
    _write_json(scale_root / MIXED_MANIFEST, mixed_manifest)
    _write_metadata(scale_root, scale_factor=scale_factor, seed=seed)


def real_world_artifact_paths(scale_root: Path) -> list[Path]:
    files: list[Path] = []
    for spec in _scenario_specs():
        stem = spec.scenario_id.lower().replace("-", "_")
        files.append(scale_root / QUERY_DIR / spec.file_name)
        files.append(scale_root / PARAMETER_DIR / f"{stem}_search_params.json")
        files.append(scale_root / PARAMETER_DIR / f"{stem}_held_out_params.json")
    files.extend(
        [
            scale_root / BASELINE_MANIFEST,
            scale_root / RULE_MANIFEST,
            scale_root / LLM_MANIFEST,
            scale_root / MIXED_MANIFEST,
            scale_root / METADATA_FILE,
        ]
    )
    return files


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_metadata(scale_root: Path, *, scale_factor: str, seed: int) -> None:
    metadata = {
        "corpus": "custom-real-world-suboptimal-tpch",
        "target_engine": "postgresql",
        "base_schema": "TPC-H",
        "scale_factor": scale_factor,
        "seed": seed,
        "search_parameter_sets_per_query": SEARCH_PARAMETER_COUNT,
        "held_out_parameter_sets_per_query": HELD_OUT_PARAMETER_COUNT,
        "parameter_policy": (
            "Deterministic bounded parameter sets preserve the scenario anti-patterns while keeping "
            "empirical result-set comparison tractable for routine SF1/SF10 runs."
        ),
        "manifests": {
            BASELINE_MANIFEST: "baseline-only calibration for all ten real-world scenarios",
            RULE_MANIFEST: "rule-candidate search for all ten real-world scenarios",
            LLM_MANIFEST: "local LLM candidate search for all ten real-world scenarios",
            MIXED_MANIFEST: (
                "mixed candidate-pool search: rule and local LLM candidates are generated "
                "together, validated, and measured when at least one candidate is available"
            ),
        },
    }
    _write_json(scale_root / METADATA_FILE, metadata)


def _manifest_entry(
    spec: ScenarioSpec,
    search_file: str,
    held_out_file: str,
    candidate_selector: str,
    scale_factor: str,
) -> dict[str, Any]:
    return {
        "query_file": spec.file_name,
        "parameter_file": search_file,
        "held_out_parameter_file": held_out_file,
        "expected_candidate_source_detail": candidate_selector,
        "workload_label": spec.label,
        "workload_description": (
            f"{spec.scenario_id}: {spec.title}. Parameterized custom real-world anti-pattern "
            f"scenario over TPC-H data at scale factor {scale_factor}."
        ),
    }


def _parameter_sets(
    scenario_id: str,
    phase: str,
    count: int,
    rng: random.Random,
    scale_factor: str,
) -> list[dict[str, Any]]:
    return [
        {
            "parameter_set_id": f"{scenario_id.lower()}-{phase}-{index:03}",
            "parameters": [
                {
                    "position": parameter.position,
                    "type": parameter.type,
                    "value": parameter.value,
                }
                for parameter in _vary_parameters(scenario_id, rng, scale_factor)
            ],
        }
        for index in range(1, count + 1)
    ]


def _vary_parameters(scenario_id: str, rng: random.Random, scale_factor: str) -> list[ParameterValue]:
    match scenario_id:
        case "RW-01":
            start, end = _date_window(rng, days=rng.choice([7, 14, 21]))
            return [_p(1, "date", start), _p(2, "date", end)]
        case "RW-02":
            return [_p(1, "numeric", round(rng.uniform(998.5, 999.9), 2))]
        case "RW-03":
            start, end = _date_window(rng, days=rng.choice([30, 60, 90]))
            return [_p(1, "date", start), _p(2, "date", end)]
        case "RW-04":
            return [_p(1, "text", rng.choice(PART_TYPE_PATTERNS))]
        case "RW-05":
            order_start, _ = _date_window(rng, days=1)
            ship_end = order_start + timedelta(days=rng.randint(30, 120))
            return [
                _p(1, "text", rng.choice(REGIONS)),
                _p(2, "date", order_start),
                _p(3, "date", ship_end),
            ]
        case "RW-06":
            start, end = _date_window(rng, days=rng.choice([7, 14, 21]))
            return [
                _p(1, "text", _clerk(rng, scale_factor)),
                _p(2, "text", rng.choice(ORDER_PRIORITIES)),
                _p(3, "date", start),
                _p(4, "date", end),
            ]
        case "RW-07":
            return [
                _p(1, "numeric", round(rng.uniform(250000, 450000), 2)),
                _p(2, "text", rng.choice(SEGMENTS)),
            ]
        case "RW-08":
            start, end = _date_window(rng, days=rng.choice([7, 14, 21]))
            return [
                _p(1, "text", rng.choice(SHIP_MODES)),
                _p(2, "date", start),
                _p(3, "date", end),
            ]
        case "RW-09":
            return [_p(1, "integer", rng.randint(1, _customer_count(scale_factor)))]
        case "RW-10":
            first, second, third = rng.sample(SHIP_MODES, 3)
            start, end = _date_window(rng, days=rng.choice([14, 30, 60]))
            return [
                _p(1, "text", first),
                _p(2, "text", second),
                _p(3, "text", third),
                _p(4, "date", start),
                _p(5, "date", end),
            ]
        case _:
            raise ValueError(f"Unsupported real-world scenario: {scenario_id}")


def _p(position: int, type_name: str, value: Any) -> ParameterValue:
    if isinstance(value, date):
        value = value.isoformat()
    return ParameterValue(position=f"${position}", type=type_name, value=value)


def _date_window(rng: random.Random, *, days: int) -> tuple[date, date]:
    start = date(rng.randint(1993, 1997), rng.randint(1, 12), rng.randint(1, 28))
    return start, start + timedelta(days=days)


def _clerk(rng: random.Random, scale_factor: str) -> str:
    max_clerk = max(1000, _scaled_count(scale_factor, base_count=1000))
    return f"Clerk#{rng.randint(1, max_clerk):09d}"


def _customer_count(scale_factor: str) -> int:
    return max(1, _scaled_count(scale_factor, base_count=150000))


def _scaled_count(scale_factor: str, *, base_count: int) -> int:
    try:
        scale = Decimal(scale_factor)
    except InvalidOperation:
        scale = Decimal(1)
    return max(1, int((Decimal(base_count) * scale).to_integral_value()))


def _scenario_specs() -> list[ScenarioSpec]:
    return [
        ScenarioSpec(
            "RW-01",
            "correlated scalar subqueries in the SELECT list",
            """
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
""",
        ),
        ScenarioSpec(
            "RW-02",
            "IN subquery over a large relationship table",
            """
SELECT l.l_orderkey, l.l_extendedprice, l.l_discount
FROM lineitem l
WHERE l.l_partkey IN (
  SELECT ps.ps_partkey
  FROM partsupp ps
  WHERE ps.ps_supplycost > $1::numeric
);
""",
        ),
        ScenarioSpec(
            "RW-03",
            "redundant outer aggregation layer",
            """
SELECT nation_name, total_revenue
FROM (
  SELECT
    n.n_name AS nation_name,
    SUM(l.l_extendedprice * (1 - l.l_discount)) AS total_revenue
  FROM lineitem l
  JOIN orders o ON l.l_orderkey = o.o_orderkey
  JOIN customer c ON o.o_custkey = c.c_custkey
  JOIN nation n ON c.c_nationkey = n.n_nationkey
  WHERE o.o_orderdate >= $1::date
    AND o.o_orderdate < $2::date
  GROUP BY n.n_name
) subq
GROUP BY nation_name, total_revenue
ORDER BY total_revenue DESC;
""",
        ),
        ScenarioSpec(
            "RW-04",
            "DISTINCT used to mask duplicate-producing joins",
            """
SELECT DISTINCT
  s.s_name,
  s.s_address,
  s.s_phone
FROM supplier s
JOIN partsupp ps ON s.s_suppkey = ps.ps_suppkey
JOIN part p ON ps.ps_partkey = p.p_partkey
WHERE p.p_type LIKE $1::text;
""",
        ),
        ScenarioSpec(
            "RW-05",
            "filter placement across a multi-table join",
            """
SELECT
  c.c_name,
  o.o_orderdate,
  l.l_extendedprice
FROM customer c
JOIN orders o ON c.c_custkey = o.o_custkey
JOIN lineitem l ON o.o_orderkey = l.l_orderkey
JOIN nation n ON c.c_nationkey = n.n_nationkey
JOIN region r ON n.n_regionkey = r.r_regionkey
WHERE r.r_name = $1::text
  AND o.o_orderdate >= $2::date
  AND l.l_shipdate <= $3::date;
""",
        ),
        ScenarioSpec(
            "RW-06",
            "duplicate-safe OR predicate splitting opportunity",
            """
SELECT o_orderkey, o_orderdate, o_totalprice
FROM orders
WHERE (o_clerk = $1::text OR o_orderpriority = $2::text)
  AND o_orderdate >= $3::date
  AND o_orderdate < $4::date;
""",
        ),
        ScenarioSpec(
            "RW-07",
            "single-reference CTE that can be inlined",
            """
WITH high_value_orders AS (
  SELECT o_orderkey, o_custkey, o_totalprice
  FROM orders
  WHERE o_totalprice > $1::numeric
)
SELECT c.c_name, h.o_totalprice
FROM high_value_orders h
JOIN customer c ON h.o_custkey = c.c_custkey
WHERE c.c_mktsegment = $2::text;
""",
        ),
        ScenarioSpec(
            "RW-08",
            "scalar aggregate subquery in a WHERE predicate",
            """
SELECT l_orderkey, l_extendedprice
FROM lineitem
WHERE l_shipdate >= $2::date
  AND l_shipdate < $3::date
  AND l_extendedprice > (
    SELECT AVG(l_extendedprice)
    FROM lineitem
    WHERE l_shipmode = $1::text
  )
  AND l_shipmode = $1::text;
""",
        ),
        ScenarioSpec(
            "RW-09",
            "self-join pattern for previous order lookup",
            """
SELECT
  o1.o_orderkey,
  o1.o_orderdate,
  o2.o_orderdate AS prev_orderdate
FROM orders o1
LEFT JOIN orders o2 ON o1.o_custkey = o2.o_custkey
  AND o2.o_orderdate < o1.o_orderdate
  AND NOT EXISTS (
    SELECT 1
    FROM orders o3
    WHERE o3.o_custkey = o1.o_custkey
      AND o3.o_orderdate < o1.o_orderdate
      AND o3.o_orderdate > o2.o_orderdate
  )
WHERE o1.o_custkey = $1::integer;
""",
        ),
        ScenarioSpec(
            "RW-10",
            "multiple scalar aggregate passes over lineitem",
            """
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
""",
        ),
    ]
