from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg

from app.api.models import (
    EquivalenceCheckResponse,
    EquivalenceRequest,
    EquivalenceResponse,
    ParameterSet,
)

_PARAMETER_RE = re.compile(r"\$(\d+)")


@dataclass(frozen=True)
class ColumnShape:
    name: str
    type_code: str


@dataclass(frozen=True)
class QueryResult:
    rows: list[tuple[Any, ...]]
    columns: list[ColumnShape]
    execution_time_ms: float


def validate_equivalence(
    connection_string: str,
    request: EquivalenceRequest,
) -> EquivalenceResponse:
    checks: list[EquivalenceCheckResponse] = []
    aggregate_mismatch: dict[str, Any] | None = None

    with psycopg.connect(connection_string, autocommit=True) as connection:
        connection.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        transaction_open = True
        try:
            for parameter_set in request.parameter_sets:
                check = _compare_for_parameter_set(connection, request, parameter_set)
                checks.append(check)
                if not check.passed and aggregate_mismatch is None:
                    aggregate_mismatch = check.mismatch_detail
                if _is_query_execution_error(check):
                    connection.execute("ROLLBACK")
                    transaction_open = False
                    break
            if transaction_open:
                connection.execute("COMMIT")
        except Exception:
            if transaction_open:
                connection.execute("ROLLBACK")
            raise

    passed = all(check.passed for check in checks)
    return EquivalenceResponse(
        request_id=request.request_id,
        candidate_id=request.candidate_id,
        passed=passed,
        method_used="full_comparison",
        checks=checks,
        parameter_sets_checked=len(checks),
        mismatch_detail=aggregate_mismatch,
        checked_at=datetime.now(UTC).isoformat(),
    )


def _compare_for_parameter_set(
    connection: psycopg.Connection[Any],
    request: EquivalenceRequest,
    parameter_set: ParameterSet,
) -> EquivalenceCheckResponse:
    try:
        original = _run_query(
            connection,
            request.original_sql,
            parameter_set,
            request.config.timeout_ms,
        )
    except Exception as exc:
        return _query_execution_error_check(parameter_set, "original", exc)

    try:
        candidate = _run_query(
            connection,
            request.candidate_sql,
            parameter_set,
            request.config.timeout_ms,
        )
    except Exception as exc:
        return _query_execution_error_check(parameter_set, "candidate", exc, original)

    if original.columns != candidate.columns:
        return EquivalenceCheckResponse(
            parameter_set_id=parameter_set.parameter_set_id,
            passed=False,
            method_used="full_comparison",
            original_row_count=len(original.rows),
            candidate_row_count=len(candidate.rows),
            rows_compared=0,
            mismatch_detail={
                "reason": "output_schema_mismatch",
                "original_columns": [column.__dict__ for column in original.columns],
                "candidate_columns": [column.__dict__ for column in candidate.columns],
            },
            original_execution_time_ms=original.execution_time_ms,
            candidate_execution_time_ms=candidate.execution_time_ms,
        )

    rows_compared = max(len(original.rows), len(candidate.rows))
    if rows_compared > request.config.max_rows_full_compare:
        return EquivalenceCheckResponse(
            parameter_set_id=parameter_set.parameter_set_id,
            passed=False,
            method_used="full_comparison",
            original_row_count=len(original.rows),
            candidate_row_count=len(candidate.rows),
            rows_compared=rows_compared,
            mismatch_detail={
                "reason": "max_rows_full_compare_exceeded",
                "max_rows_full_compare": request.config.max_rows_full_compare,
            },
            original_execution_time_ms=original.execution_time_ms,
            candidate_execution_time_ms=candidate.execution_time_ms,
        )

    mismatch: dict[str, Any] | None
    if _has_outer_order_by(request.original_sql):
        passed = _ordered_rows_equal(original.rows, candidate.rows, request.config.float_epsilon)
        mismatch = None if passed else {"reason": "ordered_rows_mismatch"}
    else:
        original_counter = _row_counter(original.rows, request.config.float_epsilon)
        candidate_counter = _row_counter(candidate.rows, request.config.float_epsilon)
        passed = original_counter == candidate_counter
        mismatch = None
        if not passed:
            only_in_original = original_counter - candidate_counter
            only_in_candidate = candidate_counter - original_counter
            mismatch = {
                "reason": "row_multiset_mismatch",
                "only_in_original": [list(row) for row in only_in_original],
                "only_in_candidate": [list(row) for row in only_in_candidate],
            }

    return EquivalenceCheckResponse(
        parameter_set_id=parameter_set.parameter_set_id,
        passed=passed,
        method_used="full_comparison",
        original_row_count=len(original.rows),
        candidate_row_count=len(candidate.rows),
        rows_compared=rows_compared,
        mismatch_detail=mismatch,
        original_execution_time_ms=original.execution_time_ms,
        candidate_execution_time_ms=candidate.execution_time_ms,
    )


def _query_execution_error_check(
    parameter_set: ParameterSet,
    side: str,
    error: Exception,
    original: QueryResult | None = None,
) -> EquivalenceCheckResponse:
    original_row_count = len(original.rows) if original is not None else 0
    return EquivalenceCheckResponse(
        parameter_set_id=parameter_set.parameter_set_id,
        passed=False,
        method_used="full_comparison",
        original_row_count=original_row_count,
        candidate_row_count=0,
        rows_compared=original_row_count if side == "candidate" else 0,
        mismatch_detail={
            "reason": "query_execution_error",
            "side": side,
            "error_type": type(error).__name__,
            "message": str(error),
        },
        original_execution_time_ms=original.execution_time_ms if original is not None else 0.0,
        candidate_execution_time_ms=0.0,
    )


def _is_query_execution_error(check: EquivalenceCheckResponse) -> bool:
    return (
        check.mismatch_detail is not None
        and check.mismatch_detail.get("reason") == "query_execution_error"
    )


def _run_query(
    connection: psycopg.Connection[Any],
    sql: str,
    parameter_set: ParameterSet,
    timeout_ms: int,
) -> QueryResult:
    converted_sql, values = _bind_sql(sql, parameter_set)
    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
        if values:
            cursor.execute(converted_sql, values)
        else:
            cursor.execute(converted_sql)
        rows = cursor.fetchall()
        columns = [
            ColumnShape(name=column.name, type_code=str(column.type_code))
            for column in (cursor.description or [])
        ]
    elapsed_ms = (time.perf_counter() - started) * 1000
    return QueryResult(
        rows=[tuple(row) for row in rows],
        columns=columns,
        execution_time_ms=elapsed_ms,
    )


def _bind_sql(sql: str, parameter_set: ParameterSet) -> tuple[str, list[Any]]:
    values_by_position = {
        parameter.position: _coerce_value(parameter.type, parameter.value)
        for parameter in parameter_set.parameters
    }
    values: list[Any] = []

    def replace(match: re.Match[str]) -> str:
        position = f"${match.group(1)}"
        if position not in values_by_position:
            raise ValueError(f"Missing parameter value for {position}")
        values.append(values_by_position[position])
        return "%s"

    return _PARAMETER_RE.sub(replace, sql), values


def _coerce_value(type_name: str, value: Any) -> Any:
    if value is None:
        return None
    normalized_type = type_name.lower()
    if "numeric" in normalized_type or "decimal" in normalized_type:
        return Decimal(str(value))
    if normalized_type == "date":
        return date.fromisoformat(str(value))
    if "int" in normalized_type:
        return int(value)
    return value


def _has_outer_order_by(sql: str) -> bool:
    try:
        from pglast import parse_sql
    except Exception:
        return bool(re.search(r"\border\s+by\b", sql, re.IGNORECASE))

    try:
        statements = parse_sql(sql)
    except Exception:
        return bool(re.search(r"\border\s+by\b", sql, re.IGNORECASE))
    if len(statements) != 1:
        return False
    return bool(getattr(statements[0].stmt, "sortClause", None))


def _ordered_rows_equal(
    original_rows: list[tuple[Any, ...]],
    candidate_rows: list[tuple[Any, ...]],
    epsilon: float,
) -> bool:
    if len(original_rows) != len(candidate_rows):
        return False
    return all(
        len(original) == len(candidate)
        and all(
            _values_equal(left, right, epsilon)
            for left, right in zip(original, candidate, strict=True)
        )
        for original, candidate in zip(original_rows, candidate_rows, strict=True)
    )


def _values_equal(left: Any, right: Any, epsilon: float) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, float) or isinstance(right, float):
        return abs(float(left) - float(right)) <= epsilon
    return bool(left == right)


def _row_counter(rows: list[tuple[Any, ...]], epsilon: float) -> Counter[tuple[str, ...]]:
    return Counter(tuple(_serialize_value(value, epsilon) for value in row) for row in rows)


def _serialize_value(value: Any, epsilon: float) -> str:
    if value is None:
        return "<NULL>"
    if isinstance(value, float):
        bucket = round(value / epsilon) if epsilon > 0 else value
        return f"float:{bucket}"
    if isinstance(value, Decimal):
        return f"decimal:{value.normalize()}"
    return f"{type(value).__name__}:{value}"
