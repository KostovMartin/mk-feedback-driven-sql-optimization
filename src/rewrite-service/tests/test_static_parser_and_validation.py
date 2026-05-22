from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.api.models import (
    EquivalenceConfig,
    EquivalenceRequest,
    ParameterSet,
    ParameterValue,
)
from app.parser.sql_parser import parse_and_analyze
from app.validation.equivalence import (
    _bind_sql,
    _coerce_value,
    _compare_for_parameter_set,
    _ordered_rows_equal,
    _row_counter,
    _run_query,
)


@dataclass(frozen=True)
class _FakeColumn:
    name: str
    type_code: str


@dataclass(frozen=True)
class _FakeQueryResult:
    rows: list[tuple[Any, ...]]
    columns: list[_FakeColumn]


class _FakeCursor:
    def __init__(
        self,
        result: _FakeQueryResult,
        executed_values: list[list[Any] | None],
    ) -> None:
        self._result = result
        self._executed_values = executed_values
        self.description: list[_FakeColumn] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, _values: list[Any] | None = None) -> None:
        if sql.startswith("SET LOCAL"):
            return
        self._executed_values.append(_values)
        self.description = self._result.columns

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._result.rows


class _FailingCursor:
    description: list[_FakeColumn] = []

    def __enter__(self) -> _FailingCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, _values: list[Any] | None = None) -> None:
        if sql.startswith("SET LOCAL"):
            return
        raise RuntimeError("candidate syntax failed")

    def fetchall(self) -> list[tuple[Any, ...]]:
        return []


class _FakeConnection:
    def __init__(self, results: list[_FakeQueryResult]) -> None:
        self._results = results
        self.executed_values: list[list[Any] | None] = []

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._results.pop(0), self.executed_values)


class _CandidateFailingConnection:
    def __init__(self) -> None:
        self._cursor_count = 0

    def cursor(self) -> _FakeCursor | _FailingCursor:
        self._cursor_count += 1
        if self._cursor_count == 1:
            return _FakeCursor(
                _FakeQueryResult(rows=[(1,)], columns=[_FakeColumn("a", "23")]),
                [],
            )
        return _FailingCursor()


def test_parser_rejects_volatile_functions_in_supported_fragment() -> None:
    analysis = parse_and_analyze("SELECT random();", check_fragment=True)

    assert analysis.parsed
    assert not analysis.in_supported_fragment
    assert "contains_volatile_function" in analysis.fragment_violations


def test_parser_accepts_read_only_cte_queries() -> None:
    analysis = parse_and_analyze(
        "WITH x AS (SELECT 1 AS id) SELECT id FROM x;",
        check_fragment=True,
    )

    assert analysis.parsed
    assert analysis.in_supported_fragment
    assert analysis.has_cte


def test_parameter_binding_preserves_repeated_parameter_values() -> None:
    parameter_set = ParameterSet(
        parameter_set_id="repeated",
        parameters=[ParameterValue(position="$1", type="integer", value=10)],
    )

    sql, values = _bind_sql(
        "SELECT l_orderkey FROM lineitem WHERE l_partkey > $1 OR l_partkey = $1",
        parameter_set,
    )

    assert sql == "SELECT l_orderkey FROM lineitem WHERE l_partkey > %s OR l_partkey = %s"
    assert values == [10, 10]


def test_parameter_binding_coerces_declared_types() -> None:
    parameter_set = ParameterSet(
        parameter_set_id="typed",
        parameters=[
            ParameterValue(position="$1", type="date", value="1995-03-15"),
            ParameterValue(position="$2", type="numeric", value="12.50"),
            ParameterValue(position="$3", type="integer", value="7"),
        ],
    )

    sql, values = _bind_sql("SELECT $1, $2, $3", parameter_set)

    assert sql == "SELECT %s, %s, %s"
    assert values == [date(1995, 3, 15), Decimal("12.50"), 7]


def test_declared_date_parameter_rejects_invalid_date_literal() -> None:
    with pytest.raises(ValueError):
        _coerce_value("date", "not-a-date")


def test_parameter_binding_fails_when_parameter_value_is_missing() -> None:
    parameter_set = ParameterSet(parameter_set_id="missing", parameters=[])

    with pytest.raises(ValueError, match=r"Missing parameter value for \$1"):
        _bind_sql("SELECT l_orderkey FROM lineitem WHERE l_partkey > $1", parameter_set)


def test_unparameterized_query_execution_does_not_bind_empty_value_list() -> None:
    parameter_set = ParameterSet(parameter_set_id="literal-percent", parameters=[])
    connection = _FakeConnection(
        [_FakeQueryResult(rows=[("forest%",)], columns=[_FakeColumn("?column?", "25")])]
    )

    _run_query(connection, "SELECT 'forest%';", parameter_set, timeout_ms=1000)

    assert connection.executed_values == [None]


def test_unordered_comparison_preserves_duplicates() -> None:
    original = [(1,), (1,), (2,)]
    candidate = [(1,), (2,), (2,)]

    assert _row_counter(original, 1e-9) != _row_counter(candidate, 1e-9)


def test_ordered_comparison_requires_row_order_when_order_by_is_present() -> None:
    original = [(1,), (2,)]
    candidate = [(2,), (1,)]

    assert not _ordered_rows_equal(original, candidate, 1e-9)


def test_equivalence_rejects_output_schema_mismatch() -> None:
    parameter_set = ParameterSet(parameter_set_id="shape", parameters=[])
    request = EquivalenceRequest(
        request_id="req",
        candidate_id="cand",
        original_sql="SELECT a FROM t",
        candidate_sql="SELECT b FROM t",
        parameter_sets=[parameter_set],
    )
    connection = _FakeConnection(
        [
            _FakeQueryResult(rows=[(1,)], columns=[_FakeColumn("a", "23")]),
            _FakeQueryResult(rows=[(1,)], columns=[_FakeColumn("b", "23")]),
        ]
    )

    check = _compare_for_parameter_set(connection, request, parameter_set)

    assert not check.passed
    assert check.mismatch_detail is not None
    assert check.mismatch_detail["reason"] == "output_schema_mismatch"


def test_equivalence_enforces_full_compare_row_limit() -> None:
    parameter_set = ParameterSet(parameter_set_id="limit", parameters=[])
    request = EquivalenceRequest(
        request_id="req",
        candidate_id="cand",
        original_sql="SELECT a FROM t",
        candidate_sql="SELECT a FROM t",
        parameter_sets=[parameter_set],
        config=EquivalenceConfig(max_rows_full_compare=1),
    )
    columns = [_FakeColumn("a", "23")]
    connection = _FakeConnection(
        [
            _FakeQueryResult(rows=[(1,), (2,)], columns=columns),
            _FakeQueryResult(rows=[(1,), (2,)], columns=columns),
        ]
    )

    check = _compare_for_parameter_set(connection, request, parameter_set)

    assert not check.passed
    assert check.mismatch_detail is not None
    assert check.mismatch_detail["reason"] == "max_rows_full_compare_exceeded"


def test_equivalence_reports_candidate_execution_error_as_failed_check() -> None:
    parameter_set = ParameterSet(parameter_set_id="candidate-error", parameters=[])
    request = EquivalenceRequest(
        request_id="req",
        candidate_id="cand",
        original_sql="SELECT a FROM t",
        candidate_sql="SELECT broken FROM",
        parameter_sets=[parameter_set],
    )

    check = _compare_for_parameter_set(_CandidateFailingConnection(), request, parameter_set)

    assert not check.passed
    assert check.original_row_count == 1
    assert check.candidate_row_count == 0
    assert check.mismatch_detail is not None
    assert check.mismatch_detail["reason"] == "query_execution_error"
    assert check.mismatch_detail["side"] == "candidate"
