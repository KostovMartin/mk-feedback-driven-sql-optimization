from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

QUERY_DIR = "queries-parameterized"
PARAMETER_DIR = "parameters-parameterized"
BASELINE_MANIFEST = "tpch-parameterized-baseline-corpus.json"
RULE_MANIFEST = "tpch-parameterized-rule-corpus.json"
LLM_MANIFEST = "tpch-parameterized-local-llm-corpus.json"
MIXED_MANIFEST = "tpch-parameterized-mixed-corpus.json"
MIXED_CANDIDATE_SELECTOR = "candidate-pool"
SEARCH_PARAMETER_COUNT = 70
HELD_OUT_PARAMETER_COUNT = 30

NATIONS = [
    "ALGERIA",
    "ARGENTINA",
    "BRAZIL",
    "CANADA",
    "EGYPT",
    "ETHIOPIA",
    "FRANCE",
    "GERMANY",
    "INDIA",
    "INDONESIA",
    "IRAN",
    "IRAQ",
    "JAPAN",
    "JORDAN",
    "KENYA",
    "MOROCCO",
    "MOZAMBIQUE",
    "PERU",
    "CHINA",
    "ROMANIA",
    "SAUDI ARABIA",
    "VIETNAM",
    "RUSSIA",
    "UNITED KINGDOM",
    "UNITED STATES",
]
REGIONS = ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]
SEGMENTS = ["AUTOMOBILE", "BUILDING", "FURNITURE", "HOUSEHOLD", "MACHINERY"]
SHIP_MODES = ["REG AIR", "AIR", "RAIL", "SHIP", "TRUCK", "MAIL", "FOB"]
TYPE_SUFFIXES = ["TIN", "NICKEL", "BRASS", "STEEL", "COPPER"]
TYPE_MIDDLES = ["ANODIZED", "BURNISHED", "PLATED", "POLISHED", "BRUSHED"]
TYPE_PREFIXES = ["STANDARD", "SMALL", "MEDIUM", "LARGE", "ECONOMY", "PROMO"]
TYPE_NOUNS = ["TIN", "NICKEL", "BRASS", "STEEL", "COPPER"]
COLORS = ["almond", "antique", "aquamarine", "azure", "beige", "black", "blue", "brown", "chartreuse"]
COMMENT_WORDS = ["special", "pending", "unusual", "express", "final", "regular"]
COMMENT_NOUNS = ["requests", "packages", "accounts", "deposits", "instructions", "excuses"]
PHONE_CODES = ["13", "31", "23", "29", "30", "18", "17", "20", "21", "22", "24", "25", "26"]


@dataclass(frozen=True)
class ParameterValue:
    position: str
    type: str
    value: Any


@dataclass(frozen=True)
class QuerySpec:
    query_nr: int
    sql: str
    parameters: list[ParameterValue]

    @property
    def file_name(self) -> str:
        return f"q{self.query_nr:02}.sql"

    @property
    def label(self) -> str:
        return f"tpch_q{self.query_nr:02}_parameterized"


def write_parameterized_workload(scale_root: Path, *, scale_factor: str, seed: int = 20260506) -> None:
    query_dir = scale_root / QUERY_DIR
    parameter_dir = scale_root / PARAMETER_DIR
    query_dir.mkdir(parents=True, exist_ok=True)
    parameter_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(seed)
    all_specs = [_query_spec(query_nr, rng) for query_nr in range(1, 23)]
    search_manifest = []
    baseline_manifest = []
    llm_manifest = []
    mixed_manifest = []

    for spec in all_specs:
        (query_dir / spec.file_name).write_text(spec.sql.strip() + "\n", encoding="utf-8", newline="\n")
        search_params = _parameter_sets(spec, "search", SEARCH_PARAMETER_COUNT, rng)
        held_out_params = _parameter_sets(spec, "held-out", HELD_OUT_PARAMETER_COUNT, rng)
        search_file = f"q{spec.query_nr:02}_search_params.json"
        held_out_file = f"q{spec.query_nr:02}_held_out_params.json"
        _write_json(parameter_dir / search_file, search_params)
        _write_json(parameter_dir / held_out_file, held_out_params)

        baseline_manifest.append(_manifest_entry(spec, search_file, held_out_file, "baseline-only", scale_factor))
        search_manifest.append(_manifest_entry(spec, search_file, held_out_file, "first-rule-candidate", scale_factor))
        llm_manifest.append(_manifest_entry(spec, search_file, held_out_file, "first-llm-candidate", scale_factor))
        mixed_manifest.append(_manifest_entry(spec, search_file, held_out_file, MIXED_CANDIDATE_SELECTOR, scale_factor))

    _write_json(scale_root / BASELINE_MANIFEST, baseline_manifest)
    _write_json(scale_root / RULE_MANIFEST, search_manifest)
    _write_json(scale_root / LLM_MANIFEST, llm_manifest)
    _write_json(scale_root / MIXED_MANIFEST, mixed_manifest)


def parameterized_artifact_paths(scale_root: Path) -> list[Path]:
    files: list[Path] = []
    files.extend(sorted((scale_root / QUERY_DIR).glob("*.sql")))
    files.extend(sorted((scale_root / PARAMETER_DIR).glob("*.json")))
    files.extend(
        [
            scale_root / BASELINE_MANIFEST,
            scale_root / RULE_MANIFEST,
            scale_root / LLM_MANIFEST,
            scale_root / MIXED_MANIFEST,
        ]
    )
    return files


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def _manifest_entry(
    spec: QuerySpec,
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
            f"TPC-H Q{spec.query_nr} parameterized PostgreSQL workload at scale factor "
            f"{scale_factor}; search and held-out parameter sets are deterministic and disjoint."
        ),
    }


def _parameter_sets(
    spec: QuerySpec,
    phase: str,
    count: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    return [
        {
            "parameter_set_id": f"q{spec.query_nr:02}-{phase}-{index:03}",
            "parameters": [
                {
                    "position": parameter.position,
                    "type": parameter.type,
                    "value": parameter.value,
                }
                for parameter in _vary_parameters(spec.query_nr, rng)
            ],
        }
        for index in range(1, count + 1)
    ]


def _query_spec(query_nr: int, rng: random.Random) -> QuerySpec:
    sql = _query_sql(query_nr)
    return QuerySpec(query_nr=query_nr, sql=sql, parameters=_vary_parameters(query_nr, rng))


def _vary_parameters(query_nr: int, rng: random.Random) -> list[ParameterValue]:
    match query_nr:
        case 1:
            return [_p(1, "date", _date_between(rng, date(1998, 8, 1), date(1998, 12, 1)))]
        case 2:
            return [_p(1, "integer", rng.randint(1, 50)), _p(2, "text", rng.choice(TYPE_SUFFIXES)), _p(3, "text", rng.choice(REGIONS))]
        case 3:
            return [_p(1, "text", rng.choice(SEGMENTS)), _p(2, "date", _date_between(rng, date(1995, 1, 1), date(1995, 3, 31)))]
        case 4:
            return [_p(1, "date", _quarter_start(rng))]
        case 5:
            return [_p(1, "text", rng.choice(REGIONS)), _p(2, "date", _year_start(rng, 1993, 1997))]
        case 6:
            return [_p(1, "date", _year_start(rng, 1993, 1997)), _p(2, "numeric", round(rng.uniform(0.02, 0.09), 2)), _p(3, "numeric", rng.randint(24, 26))]
        case 7:
            first, second = _two_distinct(rng, NATIONS)
            return [_p(1, "text", first), _p(2, "text", second)]
        case 8:
            return [_p(1, "text", rng.choice(NATIONS)), _p(2, "text", rng.choice(REGIONS)), _p(3, "text", _part_type(rng))]
        case 9:
            return [_p(1, "text", rng.choice(COLORS))]
        case 10:
            return [_p(1, "date", _quarter_start(rng))]
        case 11:
            return [_p(1, "text", rng.choice(NATIONS)), _p(2, "numeric", 0.0001)]
        case 12:
            first, second = _two_distinct(rng, SHIP_MODES)
            return [_p(1, "text", first), _p(2, "text", second), _p(3, "date", _year_start(rng, 1993, 1997))]
        case 13:
            return [_p(1, "text", rng.choice(COMMENT_WORDS)), _p(2, "text", rng.choice(COMMENT_NOUNS))]
        case 14:
            return [_p(1, "date", _month_start(rng))]
        case 15:
            return [_p(1, "date", _quarter_start(rng))]
        case 16:
            sizes = rng.sample(range(1, 51), 8)
            return [
                _p(1, "text", _brand(rng)),
                _p(2, "text", f"{rng.choice(TYPE_PREFIXES)} {rng.choice(TYPE_MIDDLES)}"),
                *[_p(index + 3, "integer", size) for index, size in enumerate(sizes)],
            ]
        case 17:
            return [_p(1, "text", _brand(rng)), _p(2, "text", rng.choice(["SM CASE", "SM BOX", "MED BAG", "LG PACK"]))]
        case 18:
            return [_p(1, "numeric", rng.randint(250, 350))]
        case 19:
            return [_p(1, "text", _brand(rng)), _p(2, "numeric", rng.randint(1, 10)), _p(3, "text", _brand(rng)), _p(4, "numeric", rng.randint(10, 20)), _p(5, "text", _brand(rng)), _p(6, "numeric", rng.randint(20, 30))]
        case 20:
            return [_p(1, "text", rng.choice(COLORS)), _p(2, "date", _year_start(rng, 1993, 1997)), _p(3, "text", rng.choice(NATIONS))]
        case 21:
            return [_p(1, "text", rng.choice(NATIONS))]
        case 22:
            return [_p(index + 1, "text", code) for index, code in enumerate(rng.sample(PHONE_CODES, 7))]
        case _:
            raise ValueError(f"Unsupported TPC-H query number: {query_nr}")


def _p(position: int, type_name: str, value: Any) -> ParameterValue:
    if isinstance(value, date):
        value = value.isoformat()
    return ParameterValue(position=f"${position}", type=type_name, value=value)


def _date_between(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def _year_start(rng: random.Random, first: int, last: int) -> date:
    return date(rng.randint(first, last), 1, 1)


def _quarter_start(rng: random.Random) -> date:
    return date(rng.randint(1993, 1997), rng.choice([1, 4, 7, 10]), 1)


def _month_start(rng: random.Random) -> date:
    return date(rng.randint(1993, 1997), rng.randint(1, 12), 1)


def _two_distinct(rng: random.Random, values: list[str]) -> tuple[str, str]:
    first, second = rng.sample(values, 2)
    return first, second


def _brand(rng: random.Random) -> str:
    return f"Brand#{rng.randint(1, 5)}{rng.randint(1, 5)}"


def _part_type(rng: random.Random) -> str:
    return f"{rng.choice(TYPE_PREFIXES)} {rng.choice(TYPE_MIDDLES)} {rng.choice(TYPE_NOUNS)}"


def _query_sql(query_nr: int) -> str:
    queries = {
        1: """
SELECT
  l_returnflag,
  l_linestatus,
  SUM(l_quantity) AS sum_qty,
  SUM(l_extendedprice) AS sum_base_price,
  SUM(l_extendedprice * (1 - l_discount)) AS sum_disc_price,
  SUM(l_extendedprice * (1 - l_discount) * (1 + l_tax)) AS sum_charge,
  AVG(l_quantity) AS avg_qty,
  AVG(l_extendedprice) AS avg_price,
  AVG(l_discount) AS avg_disc,
  COUNT(*) AS count_order
FROM lineitem
WHERE l_shipdate <= $1::date
GROUP BY l_returnflag, l_linestatus
ORDER BY l_returnflag, l_linestatus;
""",
        2: """
SELECT
  s_acctbal,
  s_name,
  n_name,
  p_partkey,
  p_mfgr,
  s_address,
  s_phone,
  s_comment
FROM part, supplier, partsupp, nation, region
WHERE p_partkey = ps_partkey
  AND s_suppkey = ps_suppkey
  AND p_size = $1::integer
  AND p_type LIKE '%' || $2::text
  AND s_nationkey = n_nationkey
  AND n_regionkey = r_regionkey
  AND r_name = $3::text
  AND ps_supplycost = (
    SELECT MIN(ps_supplycost)
    FROM partsupp, supplier, nation, region
    WHERE p_partkey = ps_partkey
      AND s_suppkey = ps_suppkey
      AND s_nationkey = n_nationkey
      AND n_regionkey = r_regionkey
      AND r_name = $3::text
  )
ORDER BY s_acctbal DESC, n_name, s_name, p_partkey
LIMIT 100;
""",
        3: """
SELECT
  l_orderkey,
  SUM(l_extendedprice * (1 - l_discount)) AS revenue,
  o_orderdate,
  o_shippriority
FROM customer, orders, lineitem
WHERE c_mktsegment = $1::text
  AND c_custkey = o_custkey
  AND l_orderkey = o_orderkey
  AND o_orderdate < $2::date
  AND l_shipdate > $2::date
GROUP BY l_orderkey, o_orderdate, o_shippriority
ORDER BY revenue DESC, o_orderdate
LIMIT 10;
""",
        4: """
SELECT
  o_orderpriority,
  COUNT(*) AS order_count
FROM orders
WHERE o_orderdate >= $1::date
  AND o_orderdate < $1::date + INTERVAL '3 months'
  AND EXISTS (
    SELECT 1
    FROM lineitem
    WHERE l_orderkey = o_orderkey
      AND l_commitdate < l_receiptdate
  )
GROUP BY o_orderpriority
ORDER BY o_orderpriority;
""",
        5: """
SELECT
  n_name,
  SUM(l_extendedprice * (1 - l_discount)) AS revenue
FROM customer, orders, lineitem, supplier, nation, region
WHERE c_custkey = o_custkey
  AND l_orderkey = o_orderkey
  AND l_suppkey = s_suppkey
  AND c_nationkey = s_nationkey
  AND s_nationkey = n_nationkey
  AND n_regionkey = r_regionkey
  AND r_name = $1::text
  AND o_orderdate >= $2::date
  AND o_orderdate < $2::date + INTERVAL '1 year'
GROUP BY n_name
ORDER BY revenue DESC;
""",
        6: """
SELECT
  SUM(l_extendedprice * l_discount) AS revenue
FROM lineitem
WHERE l_shipdate >= $1::date
  AND l_shipdate < $1::date + INTERVAL '1 year'
  AND l_discount BETWEEN $2::numeric - 0.01 AND $2::numeric + 0.01
  AND l_quantity < $3::numeric;
""",
        7: """
SELECT
  supp_nation,
  cust_nation,
  l_year,
  SUM(volume) AS revenue
FROM (
  SELECT
    n1.n_name AS supp_nation,
    n2.n_name AS cust_nation,
    EXTRACT(YEAR FROM l_shipdate) AS l_year,
    l_extendedprice * (1 - l_discount) AS volume
  FROM supplier, lineitem, orders, customer, nation n1, nation n2
  WHERE s_suppkey = l_suppkey
    AND o_orderkey = l_orderkey
    AND c_custkey = o_custkey
    AND s_nationkey = n1.n_nationkey
    AND c_nationkey = n2.n_nationkey
    AND ((n1.n_name = $1::text AND n2.n_name = $2::text)
      OR (n1.n_name = $2::text AND n2.n_name = $1::text))
    AND l_shipdate BETWEEN DATE '1995-01-01' AND DATE '1996-12-31'
) AS shipping
GROUP BY supp_nation, cust_nation, l_year
ORDER BY supp_nation, cust_nation, l_year;
""",
        8: """
SELECT
  o_year,
  SUM(CASE WHEN nation = $1::text THEN volume ELSE 0 END) / SUM(volume) AS mkt_share
FROM (
  SELECT
    EXTRACT(YEAR FROM o_orderdate) AS o_year,
    l_extendedprice * (1 - l_discount) AS volume,
    n2.n_name AS nation
  FROM part, supplier, lineitem, orders, customer, nation n1, nation n2, region
  WHERE p_partkey = l_partkey
    AND s_suppkey = l_suppkey
    AND l_orderkey = o_orderkey
    AND o_custkey = c_custkey
    AND c_nationkey = n1.n_nationkey
    AND n1.n_regionkey = r_regionkey
    AND r_name = $2::text
    AND s_nationkey = n2.n_nationkey
    AND o_orderdate BETWEEN DATE '1995-01-01' AND DATE '1996-12-31'
    AND p_type = $3::text
) AS all_nations
GROUP BY o_year
ORDER BY o_year;
""",
        9: """
SELECT
  nation,
  o_year,
  SUM(amount) AS sum_profit
FROM (
  SELECT
    n_name AS nation,
    EXTRACT(YEAR FROM o_orderdate) AS o_year,
    l_extendedprice * (1 - l_discount) - ps_supplycost * l_quantity AS amount
  FROM part, supplier, lineitem, partsupp, orders, nation
  WHERE s_suppkey = l_suppkey
    AND ps_suppkey = l_suppkey
    AND ps_partkey = l_partkey
    AND p_partkey = l_partkey
    AND o_orderkey = l_orderkey
    AND s_nationkey = n_nationkey
    AND p_name LIKE '%' || $1::text || '%'
) AS profit
GROUP BY nation, o_year
ORDER BY nation, o_year DESC;
""",
        10: """
SELECT
  c_custkey,
  c_name,
  SUM(l_extendedprice * (1 - l_discount)) AS revenue,
  c_acctbal,
  n_name,
  c_address,
  c_phone,
  c_comment
FROM customer, orders, lineitem, nation
WHERE c_custkey = o_custkey
  AND l_orderkey = o_orderkey
  AND o_orderdate >= $1::date
  AND o_orderdate < $1::date + INTERVAL '3 months'
  AND l_returnflag = 'R'
  AND c_nationkey = n_nationkey
GROUP BY c_custkey, c_name, c_acctbal, c_phone, n_name, c_address, c_comment
ORDER BY revenue DESC
LIMIT 20;
""",
        11: """
SELECT
  ps_partkey,
  SUM(ps_supplycost * ps_availqty) AS value
FROM partsupp, supplier, nation
WHERE ps_suppkey = s_suppkey
  AND s_nationkey = n_nationkey
  AND n_name = $1::text
GROUP BY ps_partkey
HAVING SUM(ps_supplycost * ps_availqty) > (
  SELECT SUM(ps_supplycost * ps_availqty) * $2::numeric
  FROM partsupp, supplier, nation
  WHERE ps_suppkey = s_suppkey
    AND s_nationkey = n_nationkey
    AND n_name = $1::text
)
ORDER BY value DESC;
""",
        12: """
SELECT
  l_shipmode,
  SUM(CASE WHEN o_orderpriority = '1-URGENT' OR o_orderpriority = '2-HIGH' THEN 1 ELSE 0 END) AS high_line_count,
  SUM(CASE WHEN o_orderpriority <> '1-URGENT' AND o_orderpriority <> '2-HIGH' THEN 1 ELSE 0 END) AS low_line_count
FROM orders, lineitem
WHERE o_orderkey = l_orderkey
  AND l_shipmode IN ($1::text, $2::text)
  AND l_commitdate < l_receiptdate
  AND l_shipdate < l_commitdate
  AND l_receiptdate >= $3::date
  AND l_receiptdate < $3::date + INTERVAL '1 year'
GROUP BY l_shipmode
ORDER BY l_shipmode;
""",
        13: """
SELECT
  c_count,
  COUNT(*) AS custdist
FROM (
  SELECT c_custkey, COUNT(o_orderkey) AS c_count
  FROM customer
  LEFT JOIN orders
    ON c_custkey = o_custkey
   AND o_comment NOT LIKE '%' || $1::text || '%' || $2::text || '%'
  GROUP BY c_custkey
) AS c_orders
GROUP BY c_count
ORDER BY custdist DESC, c_count DESC;
""",
        14: """
SELECT
  100.00 * SUM(CASE WHEN p_type LIKE 'PROMO%' THEN l_extendedprice * (1 - l_discount) ELSE 0 END)
  / SUM(l_extendedprice * (1 - l_discount)) AS promo_revenue
FROM lineitem, part
WHERE l_partkey = p_partkey
  AND l_shipdate >= $1::date
  AND l_shipdate < $1::date + INTERVAL '1 month';
""",
        15: """
WITH revenue AS (
  SELECT
    l_suppkey AS supplier_no,
    SUM(l_extendedprice * (1 - l_discount)) AS total_revenue
  FROM lineitem
  WHERE l_shipdate >= $1::date
    AND l_shipdate < $1::date + INTERVAL '3 months'
  GROUP BY l_suppkey
)
SELECT
  s_suppkey,
  s_name,
  s_address,
  s_phone,
  total_revenue
FROM supplier, revenue
WHERE s_suppkey = supplier_no
  AND total_revenue = (SELECT MAX(total_revenue) FROM revenue)
ORDER BY s_suppkey;
""",
        16: """
SELECT
  p_brand,
  p_type,
  p_size,
  COUNT(DISTINCT ps_suppkey) AS supplier_cnt
FROM partsupp, part
WHERE p_partkey = ps_partkey
  AND p_brand <> $1::text
  AND p_type NOT LIKE $2::text || '%'
  AND p_size IN ($3::integer, $4::integer, $5::integer, $6::integer, $7::integer, $8::integer, $9::integer, $10::integer)
  AND ps_suppkey NOT IN (
    SELECT s_suppkey
    FROM supplier
    WHERE s_comment LIKE '%Customer%Complaints%'
  )
GROUP BY p_brand, p_type, p_size
ORDER BY supplier_cnt DESC, p_brand, p_type, p_size;
""",
        17: """
SELECT
  SUM(l_extendedprice) / 7.0 AS avg_yearly
FROM lineitem, part
WHERE p_partkey = l_partkey
  AND p_brand = $1::text
  AND p_container = $2::text
  AND l_quantity < (
    SELECT 0.2 * AVG(l_quantity)
    FROM lineitem
    WHERE l_partkey = p_partkey
  );
""",
        18: """
SELECT
  c_name,
  c_custkey,
  o_orderkey,
  o_orderdate,
  o_totalprice,
  SUM(l_quantity)
FROM customer, orders, lineitem
WHERE o_orderkey IN (
  SELECT l_orderkey
  FROM lineitem
  GROUP BY l_orderkey
  HAVING SUM(l_quantity) > $1::numeric
)
  AND c_custkey = o_custkey
  AND o_orderkey = l_orderkey
GROUP BY c_name, c_custkey, o_orderkey, o_orderdate, o_totalprice
ORDER BY o_totalprice DESC, o_orderdate
LIMIT 100;
""",
        19: """
SELECT
  SUM(l_extendedprice * (1 - l_discount)) AS revenue
FROM lineitem, part
WHERE (
    p_partkey = l_partkey
    AND p_brand = $1::text
    AND p_container IN ('SM CASE', 'SM BOX', 'SM PACK', 'SM PKG')
    AND l_quantity >= $2::numeric AND l_quantity <= $2::numeric + 10
    AND p_size BETWEEN 1 AND 5
    AND l_shipmode IN ('AIR', 'AIR REG')
    AND l_shipinstruct = 'DELIVER IN PERSON'
  )
  OR (
    p_partkey = l_partkey
    AND p_brand = $3::text
    AND p_container IN ('MED BAG', 'MED BOX', 'MED PKG', 'MED PACK')
    AND l_quantity >= $4::numeric AND l_quantity <= $4::numeric + 10
    AND p_size BETWEEN 1 AND 10
    AND l_shipmode IN ('AIR', 'AIR REG')
    AND l_shipinstruct = 'DELIVER IN PERSON'
  )
  OR (
    p_partkey = l_partkey
    AND p_brand = $5::text
    AND p_container IN ('LG CASE', 'LG BOX', 'LG PACK', 'LG PKG')
    AND l_quantity >= $6::numeric AND l_quantity <= $6::numeric + 10
    AND p_size BETWEEN 1 AND 15
    AND l_shipmode IN ('AIR', 'AIR REG')
    AND l_shipinstruct = 'DELIVER IN PERSON'
  );
""",
        20: """
SELECT
  s_name,
  s_address
FROM supplier, nation
WHERE s_suppkey IN (
  SELECT ps_suppkey
  FROM partsupp
  WHERE ps_partkey IN (
    SELECT p_partkey
    FROM part
    WHERE p_name LIKE $1::text || '%'
  )
    AND ps_availqty > (
      SELECT 0.5 * SUM(l_quantity)
      FROM lineitem
      WHERE l_partkey = ps_partkey
        AND l_suppkey = ps_suppkey
        AND l_shipdate >= $2::date
        AND l_shipdate < $2::date + INTERVAL '1 year'
    )
)
  AND s_nationkey = n_nationkey
  AND n_name = $3::text
ORDER BY s_name;
""",
        21: """
SELECT
  s_name,
  COUNT(*) AS numwait
FROM supplier, lineitem l1, orders, nation
WHERE s_suppkey = l1.l_suppkey
  AND o_orderkey = l1.l_orderkey
  AND o_orderstatus = 'F'
  AND l1.l_receiptdate > l1.l_commitdate
  AND EXISTS (
    SELECT 1
    FROM lineitem l2
    WHERE l2.l_orderkey = l1.l_orderkey
      AND l2.l_suppkey <> l1.l_suppkey
  )
  AND NOT EXISTS (
    SELECT 1
    FROM lineitem l3
    WHERE l3.l_orderkey = l1.l_orderkey
      AND l3.l_suppkey <> l1.l_suppkey
      AND l3.l_receiptdate > l3.l_commitdate
  )
  AND s_nationkey = n_nationkey
  AND n_name = $1::text
GROUP BY s_name
ORDER BY numwait DESC, s_name
LIMIT 100;
""",
        22: """
SELECT
  cntrycode,
  COUNT(*) AS numcust,
  SUM(c_acctbal) AS totacctbal
FROM (
  SELECT
    SUBSTRING(c_phone FROM 1 FOR 2) AS cntrycode,
    c_acctbal
  FROM customer
  WHERE SUBSTRING(c_phone FROM 1 FOR 2) IN ($1::text, $2::text, $3::text, $4::text, $5::text, $6::text, $7::text)
    AND c_acctbal > (
      SELECT AVG(c_acctbal)
      FROM customer
      WHERE c_acctbal > 0.00
        AND SUBSTRING(c_phone FROM 1 FOR 2) IN ($1::text, $2::text, $3::text, $4::text, $5::text, $6::text, $7::text)
    )
    AND NOT EXISTS (
      SELECT 1
      FROM orders
      WHERE o_custkey = c_custkey
    )
) AS custsale
GROUP BY cntrycode
ORDER BY cntrycode;
""",
    }
    return queries[query_nr]
