from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import duckdb

from tpch_parameterized_workload import (
    BASELINE_MANIFEST,
    LLM_MANIFEST,
    MIXED_MANIFEST,
    PARAMETER_DIR,
    QUERY_DIR,
    RULE_MANIFEST,
    parameterized_artifact_paths,
    write_parameterized_workload,
)
from real_world_workload import (
    BASELINE_MANIFEST as REAL_WORLD_BASELINE_MANIFEST,
    LLM_MANIFEST as REAL_WORLD_LLM_MANIFEST,
    MIXED_MANIFEST as REAL_WORLD_MIXED_MANIFEST,
    RULE_MANIFEST as REAL_WORLD_RULE_MANIFEST,
    real_world_artifact_paths,
    write_real_world_workload,
)

TABLES = (
    "region",
    "nation",
    "part",
    "supplier",
    "partsupp",
    "customer",
    "orders",
    "lineitem",
)

ARTIFACT_MANIFEST = "tpch-duckdb-corpus.json"
LLM_ARTIFACT_MANIFEST = "tpch-local-llm-corpus.json"
FIRST_LLM_CANDIDATE_SOURCE_DETAIL = "first-llm-candidate"
POSTGRES_ALIAS = "target_pg"

POSTLOAD_SQL = (
    "ALTER TABLE region ADD CONSTRAINT region_pkey PRIMARY KEY (r_regionkey)",
    "ALTER TABLE nation ADD CONSTRAINT nation_pkey PRIMARY KEY (n_nationkey)",
    "ALTER TABLE part ADD CONSTRAINT part_pkey PRIMARY KEY (p_partkey)",
    "ALTER TABLE supplier ADD CONSTRAINT supplier_pkey PRIMARY KEY (s_suppkey)",
    "ALTER TABLE partsupp ADD CONSTRAINT partsupp_pkey PRIMARY KEY (ps_partkey, ps_suppkey)",
    "ALTER TABLE customer ADD CONSTRAINT customer_pkey PRIMARY KEY (c_custkey)",
    "ALTER TABLE orders ADD CONSTRAINT orders_pkey PRIMARY KEY (o_orderkey)",
    "ALTER TABLE lineitem ADD CONSTRAINT lineitem_pkey PRIMARY KEY (l_orderkey, l_linenumber)",
    "CREATE INDEX idx_tpch_nation_regionkey ON nation (n_regionkey)",
    "CREATE INDEX idx_tpch_supplier_nationkey ON supplier (s_nationkey)",
    "CREATE INDEX idx_tpch_customer_nationkey ON customer (c_nationkey)",
    "CREATE INDEX idx_tpch_orders_custkey ON orders (o_custkey)",
    "CREATE INDEX idx_tpch_orders_orderdate ON orders (o_orderdate)",
    "CREATE INDEX idx_tpch_lineitem_orderkey ON lineitem (l_orderkey)",
    "CREATE INDEX idx_tpch_lineitem_partkey ON lineitem (l_partkey)",
    "CREATE INDEX idx_tpch_lineitem_suppkey ON lineitem (l_suppkey)",
    "CREATE INDEX idx_tpch_lineitem_shipdate ON lineitem (l_shipdate)",
    "CREATE INDEX idx_tpch_partsupp_suppkey ON partsupp (ps_suppkey)",
    "CREATE INDEX idx_tpch_partsupp_supplycost_partkey ON partsupp (ps_supplycost, ps_partkey)",
    "ANALYZE region",
    "ANALYZE nation",
    "ANALYZE part",
    "ANALYZE supplier",
    "ANALYZE partsupp",
    "ANALYZE customer",
    "ANALYZE orders",
    "ANALYZE lineitem",
)


def main() -> int:
    scale_factor = read_scale_factor(os.environ.get("TPCH_SCALE_FACTOR", "1"))
    output_root = Path(os.environ.get("TPCH_OUTPUT_ROOT", "/workspace/tpch")).resolve()
    scale_root = output_root / f"sf{scale_factor}"
    force = read_bool("TPCH_FORCE_GENERATE", default=False)

    con = duckdb.connect(database=":memory:")
    load_extension(con, "tpch")
    load_extension(con, "postgres")
    attach_postgres(con, postgres_connection_string())

    if not force and artifacts_complete(scale_root) and postgres_tables_ready(con):
        print(f"DuckDB-generated TPC-H SF{scale_factor} artifacts and PostgreSQL tables already exist.")
        print("Set TPCH_FORCE_GENERATE=true to regenerate them.")
        return 0

    generate_postgres_database(con, scale_factor=scale_factor)
    write_workload_artifacts(con, scale_root=scale_root, scale_factor=scale_factor)

    print(f"DuckDB-generated TPC-H SF{scale_factor} loaded into PostgreSQL.")
    print(f"Generated workload artifacts written to {scale_root}.")
    return 0


def read_scale_factor(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise SystemExit("TPCH_SCALE_FACTOR must be a positive numeric value.")

    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise SystemExit("TPCH_SCALE_FACTOR must be a positive numeric value.") from exc

    if not parsed.is_finite() or parsed <= 0:
        raise SystemExit("TPCH_SCALE_FACTOR must be greater than zero.")

    return value


def read_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise SystemExit(f"{name} must be true or false, got {raw!r}.")


def postgres_connection_string() -> str:
    explicit = os.environ.get("TPCH_TARGET_DB_CONNECTION")
    if explicit:
        return explicit

    values = {
        "host": os.environ.get("TARGET_DB_HOST", "target-db"),
        "port": os.environ.get("TARGET_DB_PORT", "5432"),
        "dbname": os.environ.get("TARGET_DB_NAME", "tpch"),
        "user": os.environ.get("TARGET_DB_USER", "postgres"),
        "password": os.environ.get("TARGET_DB_PASSWORD", "postgres"),
    }
    return " ".join(f"{key}={libpq_quote(value)}" for key, value in values.items())


def libpq_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def load_extension(con: duckdb.DuckDBPyConnection, name: str) -> None:
    try:
        con.execute(f"LOAD {name}")
    except duckdb.Error:
        con.execute(f"INSTALL {name}")
        con.execute(f"LOAD {name}")


def attach_postgres(con: duckdb.DuckDBPyConnection, connection_string: str) -> None:
    con.execute(f"ATTACH {sql_string(connection_string)} AS {POSTGRES_ALIAS} (TYPE postgres)")


def artifacts_complete(scale_root: Path) -> bool:
    required = [
        *[scale_root / "queries" / f"q{query_nr:02}.sql" for query_nr in range(1, 23)],
        *[scale_root / "parameters" / f"q{query_nr:02}_params.json" for query_nr in range(1, 23)],
        *[scale_root / QUERY_DIR / f"q{query_nr:02}.sql" for query_nr in range(1, 23)],
        *[
            scale_root / PARAMETER_DIR / f"q{query_nr:02}_{phase}_params.json"
            for query_nr in range(1, 23)
            for phase in ("search", "held_out")
        ],
        scale_root / ARTIFACT_MANIFEST,
        scale_root / LLM_ARTIFACT_MANIFEST,
        scale_root / BASELINE_MANIFEST,
        scale_root / RULE_MANIFEST,
        scale_root / LLM_MANIFEST,
        *real_world_artifact_paths(scale_root),
        scale_root / "DUCKDB_TPCH_METADATA.json",
        scale_root / "SHA256SUMS",
    ]
    return all(path.is_file() and path.stat().st_size > 0 for path in required)


def postgres_tables_ready(con: duckdb.DuckDBPyConnection) -> bool:
    table_list = ", ".join(f"'{table_name}'" for table_name in TABLES)
    table_count = postgres_scalar(
        con,
        "SELECT COUNT(*) "
        "FROM information_schema.tables "
        "WHERE table_schema = 'public' "
        f"AND table_name IN ({table_list})",
    )
    if int(table_count) != len(TABLES):
        return False

    lineitem_count = postgres_scalar(con, "SELECT COUNT(*) FROM lineitem")
    return int(lineitem_count) > 0


def postgres_scalar(con: duckdb.DuckDBPyConnection, sql: str) -> object:
    row = con.execute(f"SELECT * FROM postgres_query('{POSTGRES_ALIAS}', ?)", [sql]).fetchone()
    if row is None:
        raise RuntimeError(f"PostgreSQL query returned no rows: {sql}")
    return row[0]


def postgres_execute(con: duckdb.DuckDBPyConnection, sql: str) -> None:
    con.execute(f"CALL postgres_execute('{POSTGRES_ALIAS}', ?)", [normalize_statement(sql)])


def generate_postgres_database(con: duckdb.DuckDBPyConnection, *, scale_factor: str) -> None:
    print(f"Generating TPC-H SF{scale_factor} data with DuckDB tpch extension.")
    drop_postgres_tables(con)

    for table_name in TABLES:
        con.execute(f"DROP TABLE IF EXISTS {table_name}")
    con.execute(f"CALL dbgen(sf = {scale_factor})")

    for table_name in TABLES:
        print(f"Creating and loading PostgreSQL table {table_name}.")
        con.execute(f"CREATE TABLE {POSTGRES_ALIAS}.{table_name} AS SELECT * FROM {table_name}")

    for statement in POSTLOAD_SQL:
        postgres_execute(con, statement)


def drop_postgres_tables(con: duckdb.DuckDBPyConnection) -> None:
    for table_name in reversed(TABLES):
        postgres_execute(con, f"DROP TABLE IF EXISTS {table_name} CASCADE")


def write_workload_artifacts(
    con: duckdb.DuckDBPyConnection,
    *,
    scale_root: Path,
    scale_factor: str,
) -> None:
    query_dir = scale_root / "queries"
    parameter_dir = scale_root / "parameters"
    clean_directory(query_dir)
    clean_directory(parameter_dir)

    queries = load_queries(con)
    write_queries_and_parameters(
        queries=queries,
        query_dir=query_dir,
        parameter_dir=parameter_dir,
        scale_factor=scale_factor,
    )
    write_parameterized_workload(scale_root, scale_factor=scale_factor)
    write_real_world_workload(scale_root, scale_factor=scale_factor)
    write_manifest(scale_root=scale_root, scale_factor=scale_factor)
    write_llm_manifest(scale_root=scale_root, scale_factor=scale_factor)
    write_metadata(scale_root=scale_root, scale_factor=scale_factor)
    write_checksums(scale_root=scale_root)


def clean_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def load_queries(con: duckdb.DuckDBPyConnection) -> dict[int, str]:
    rows = con.execute("SELECT query_nr, query FROM tpch_queries() ORDER BY query_nr").fetchall()
    queries = {int(query_nr): normalize_line_endings(str(query)) for query_nr, query in rows}
    missing = [query_nr for query_nr in range(1, 23) if query_nr not in queries]
    if missing:
        missing_list = ", ".join(f"q{query_nr:02}" for query_nr in missing)
        raise SystemExit(f"DuckDB tpch_queries() did not return all TPC-H queries; missing {missing_list}.")
    return queries


def normalize_line_endings(sql: str) -> str:
    return sql.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def write_queries_and_parameters(
    *,
    queries: dict[int, str],
    query_dir: Path,
    parameter_dir: Path,
    scale_factor: str,
) -> None:
    for query_nr in range(1, 23):
        query_file = query_dir / f"q{query_nr:02}.sql"
        parameter_file = parameter_dir / f"q{query_nr:02}_params.json"
        query_file.write_text(queries[query_nr], encoding="utf-8", newline="\n")
        parameter_sets = [
            {
                "parameter_set_id": f"q{query_nr:02}-duckdb-fixed-sf{scale_factor}-001",
                "parameters": [],
            }
        ]
        parameter_file.write_text(
            json.dumps(parameter_sets, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def write_manifest(*, scale_root: Path, scale_factor: str) -> None:
    manifest = [
        {
            "query_file": f"q{query_nr:02}.sql",
            "parameter_file": f"q{query_nr:02}_params.json",
            "held_out_parameter_file": None,
            "expected_candidate_source_detail": "baseline-only",
            "workload_label": f"tpch_q{query_nr:02}_duckdb_fixed_sf{scale_factor}",
            "workload_description": (
                f"TPC-H Q{query_nr} DuckDB fixed-substitution SQL baseline calibration "
                f"on PostgreSQL at scale factor {scale_factor}."
            ),
        }
        for query_nr in range(1, 23)
    ]
    (scale_root / ARTIFACT_MANIFEST).write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_llm_manifest(*, scale_root: Path, scale_factor: str) -> None:
    manifest = [
        {
            "query_file": f"q{query_nr:02}.sql",
            "parameter_file": f"q{query_nr:02}_params.json",
            "held_out_parameter_file": None,
            "expected_candidate_source_detail": FIRST_LLM_CANDIDATE_SOURCE_DETAIL,
            "workload_label": f"tpch_q{query_nr:02}_duckdb_fixed_sf{scale_factor}_local_llm",
            "workload_description": (
                f"TPC-H Q{query_nr} DuckDB fixed-substitution SQL local LLM candidate search "
                f"on PostgreSQL at scale factor {scale_factor}."
            ),
        }
        for query_nr in range(1, 23)
    ]
    (scale_root / LLM_ARTIFACT_MANIFEST).write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_metadata(*, scale_root: Path, scale_factor: str) -> None:
    metadata = {
        "generator": "duckdb-tpch-extension",
        "loader": "duckdb-postgres-extension",
        "target_engine": "postgresql",
        "duckdb_version": duckdb.__version__,
        "scale_factor": scale_factor,
        "source_functions": ["dbgen", "tpch_queries"],
        "parameter_policy": "DuckDB TPC-H fixed substitution values; generated parameter sets are empty.",
        "manifests": {
            ARTIFACT_MANIFEST: "baseline-only calibration for all 22 fixed DuckDB TPC-H query instances",
            LLM_ARTIFACT_MANIFEST: "local LLM candidate search for all 22 fixed DuckDB TPC-H query instances",
            BASELINE_MANIFEST: "baseline-only calibration for all 22 parameterized TPC-H templates",
            RULE_MANIFEST: "rule-candidate search for all 22 parameterized TPC-H templates with 70/30 search-held-out split",
            LLM_MANIFEST: "local LLM candidate search for all 22 parameterized TPC-H templates with 70/30 search-held-out split",
            MIXED_MANIFEST: "mixed candidate-pool search for all 22 parameterized TPC-H templates with 70/30 search-held-out split",
            REAL_WORLD_BASELINE_MANIFEST: "baseline-only calibration for ten custom real-world anti-pattern scenarios",
            REAL_WORLD_RULE_MANIFEST: "rule-candidate search for ten custom real-world anti-pattern scenarios",
            REAL_WORLD_LLM_MANIFEST: "local LLM candidate search for ten custom real-world anti-pattern scenarios",
            REAL_WORLD_MIXED_MANIFEST: "mixed candidate-pool search for ten custom real-world anti-pattern scenarios",
        },
        "parameterized_policy": {
            "search_parameter_sets": 70,
            "held_out_parameter_sets": 30,
            "seed": 20260506,
            "note": "Parameter values are deterministic local substitutions for PostgreSQL TPC-H parameterized evaluation runs; fixed DuckDB query instances remain separate pilot artifacts.",
        },
    }
    (scale_root / "DUCKDB_TPCH_METADATA.json").write_text(
        json.dumps(metadata, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_checksums(*, scale_root: Path) -> None:
    files = [
        *sorted((scale_root / "queries").glob("*.sql")),
        *sorted((scale_root / "parameters").glob("*.json")),
        *parameterized_artifact_paths(scale_root),
        *real_world_artifact_paths(scale_root),
        scale_root / ARTIFACT_MANIFEST,
        scale_root / LLM_ARTIFACT_MANIFEST,
        scale_root / "DUCKDB_TPCH_METADATA.json",
    ]
    lines = [f"{sha256(path)}  {path.relative_to(scale_root).as_posix()}" for path in files]
    (scale_root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def normalize_statement(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().removesuffix(";")


if __name__ == "__main__":
    sys.exit(main())
