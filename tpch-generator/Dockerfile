FROM python:3.14-slim

ARG DUCKDB_VERSION=1.5.2

RUN pip install --no-cache-dir "duckdb==${DUCKDB_VERSION}"

COPY generate-duckdb-tpch.py tpch_parameterized_workload.py real_world_workload.py /usr/local/bin/

ENTRYPOINT ["python", "/usr/local/bin/generate-duckdb-tpch.py"]
