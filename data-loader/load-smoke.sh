#!/usr/bin/env bash
set -euo pipefail

BENCHMARK="${BENCHMARK:-smoke}"
JOB_IMDB_DATA_DIR="${JOB_IMDB_DATA_DIR:-/workspace/data/job-imdb}"
JOB_IMDB_SKIP_TARGET_LOAD="${JOB_IMDB_SKIP_TARGET_LOAD:-false}"
JOB_IMDB_TABLES="aka_name aka_title cast_info char_name comp_cast_type company_name company_type complete_cast info_type keyword kind_type link_type movie_companies movie_info movie_info_idx movie_keyword movie_link name person_info role_type title"

TARGET_DB_HOST="${TARGET_DB_HOST:-target-db}"
TARGET_DB_PORT="${TARGET_DB_PORT:-5432}"
TARGET_DB_NAME="${TARGET_DB_NAME:-tpch}"
TARGET_DB_USER="${TARGET_DB_USER:-postgres}"
TARGET_DB_PASSWORD="${TARGET_DB_PASSWORD:-postgres}"
TARGET_CONNECTION="host=$TARGET_DB_HOST port=$TARGET_DB_PORT dbname=$TARGET_DB_NAME user=$TARGET_DB_USER"

METADATA_DB_HOST="${METADATA_DB_HOST:-metadata-db}"
METADATA_DB_PORT="${METADATA_DB_PORT:-5432}"
METADATA_DB_NAME="${METADATA_DB_NAME:-optimizer}"
METADATA_DB_USER="${METADATA_DB_USER:-postgres}"
METADATA_DB_PASSWORD="${METADATA_DB_PASSWORD:-postgres}"

export PGPASSWORD="$TARGET_DB_PASSWORD"
for attempt in $(seq 1 60); do
  if pg_isready -h "$TARGET_DB_HOST" -p "$TARGET_DB_PORT" -U "$TARGET_DB_USER" -d "$TARGET_DB_NAME"; then
    break
  fi

  if [ "$attempt" -eq 60 ]; then
    echo "Timed out waiting for target database at $TARGET_DB_HOST:$TARGET_DB_PORT." >&2
    exit 1
  fi

  sleep 1
done

load_job_imdb_target() {
  if [ ! -f "$JOB_IMDB_DATA_DIR/schema.sql" ]; then
    echo "Missing JOB/IMDB schema at $JOB_IMDB_DATA_DIR/schema.sql." >&2
    exit 2
  fi

  for table_name in $JOB_IMDB_TABLES; do
    csv_path="$JOB_IMDB_DATA_DIR/${table_name}.csv"
    if [ ! -f "$csv_path" ]; then
      echo "Missing JOB/IMDB CSV file: $csv_path" >&2
      exit 2
    fi
  done

  psql "$TARGET_CONNECTION" -v ON_ERROR_STOP=1 -f "$JOB_IMDB_DATA_DIR/schema.sql"

  for table_name in $JOB_IMDB_TABLES; do
    csv_path="$JOB_IMDB_DATA_DIR/${table_name}.csv"
    echo "Loading JOB/IMDB table ${table_name} from ${csv_path}."
    psql "$TARGET_CONNECTION" \
      -v ON_ERROR_STOP=1 \
      -c "\copy ${table_name} FROM '${csv_path}' WITH (FORMAT csv, ESCAPE '\')"
  done

  if [ -f "$JOB_IMDB_DATA_DIR/fkindexes.sql" ]; then
    psql "$TARGET_CONNECTION" -v ON_ERROR_STOP=1 -f "$JOB_IMDB_DATA_DIR/fkindexes.sql"
  else
    echo "Optional JOB/IMDB fkindexes.sql not found; continuing without foreign-key indexes."
  fi

  psql "$TARGET_CONNECTION" -v ON_ERROR_STOP=1 -c "ANALYZE"
}

case "$BENCHMARK" in
  smoke)
    target_schema=/workspace/tests/smoke/data/schema.sql
    ;;
  tpch)
    target_schema=
    for table_name in region nation part supplier partsupp customer orders lineitem; do
      table_exists="$(psql "$TARGET_CONNECTION" -v ON_ERROR_STOP=1 -Atc "SELECT to_regclass('public.${table_name}') IS NOT NULL")"
      if [ "$table_exists" != "t" ]; then
        echo "Missing PostgreSQL TPC-H table public.${table_name}." >&2
        echo "Run the tpch-generator Compose service before BENCHMARK=tpch." >&2
        exit 2
      fi
    done
    ;;
  job-imdb)
    target_schema=
    if [ "$JOB_IMDB_SKIP_TARGET_LOAD" = "true" ]; then
      echo "Skipping JOB/IMDB target load because JOB_IMDB_SKIP_TARGET_LOAD=true."
    else
      load_job_imdb_target
    fi
    ;;
  *)
    echo "Unsupported BENCHMARK=$BENCHMARK. Expected smoke, tpch, or job-imdb." >&2
    exit 2
    ;;
esac

if [ -n "$target_schema" ]; then
  psql "$TARGET_CONNECTION" \
    -v ON_ERROR_STOP=1 \
    -f "$target_schema"
fi

export PGPASSWORD="$METADATA_DB_PASSWORD"
for attempt in $(seq 1 60); do
  if pg_isready -h "$METADATA_DB_HOST" -p "$METADATA_DB_PORT" -U "$METADATA_DB_USER" -d "$METADATA_DB_NAME"; then
    break
  fi

  if [ "$attempt" -eq 60 ]; then
    echo "Timed out waiting for metadata database at $METADATA_DB_HOST:$METADATA_DB_PORT." >&2
    exit 1
  fi

  sleep 1
done

psql "host=$METADATA_DB_HOST port=$METADATA_DB_PORT dbname=$METADATA_DB_NAME user=$METADATA_DB_USER" \
  -v ON_ERROR_STOP=1 \
  -f /workspace/sql/metadata-schema.sql

echo "$BENCHMARK fixture and metadata schema loaded."
