from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.api.models import EquivalenceCheckResponse, EquivalenceRequest, ParameterSet
from app.validation.equivalence import _compare_for_parameter_set


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
    ) -> None:
        self._result = result
        self.description: list[_FakeColumn] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, _values: list[Any] | None = None) -> None:
        if sql.startswith("SET LOCAL"):
            return
        self.description = self._result.columns

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._result.rows


class _FakeConnection:
    def __init__(self, results: list[_FakeQueryResult]) -> None:
        self._results = results

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._results.pop(0))


def _request(
    original_sql: str = "SELECT a FROM t",
    candidate_sql: str = "SELECT a FROM t",
) -> EquivalenceRequest:
    return EquivalenceRequest(
        request_id="req",
        candidate_id="cand",
        original_sql=original_sql,
        candidate_sql=candidate_sql,
        parameter_sets=[ParameterSet(parameter_set_id="p1", parameters=[])],
    )


def _check(
    original_rows: list[tuple[Any, ...]],
    candidate_rows: list[tuple[Any, ...]],
    *,
    original_sql: str = "SELECT a FROM t",
    candidate_sql: str = "SELECT a FROM t",
) -> EquivalenceCheckResponse:
    columns = [_FakeColumn("a", "23")]
    parameter_set = ParameterSet(parameter_set_id="p1", parameters=[])
    connection = _FakeConnection(
        [
            _FakeQueryResult(rows=original_rows, columns=columns),
            _FakeQueryResult(rows=candidate_rows, columns=columns),
        ]
    )
    return _compare_for_parameter_set(
        connection,
        _request(original_sql=original_sql, candidate_sql=candidate_sql),
        parameter_set,
    )


def test_equivalence_rejects_duplicate_dropping_candidate() -> None:
    check = _check(original_rows=[(1,), (1,), (2,)], candidate_rows=[(1,), (2,)])

    assert not check.passed
    assert check.mismatch_detail is not None
    assert check.mismatch_detail["reason"] == "row_multiset_mismatch"


def test_equivalence_rejects_null_value_substitution() -> None:
    check = _check(original_rows=[(None,)], candidate_rows=[(0,)])

    assert not check.passed
    assert check.mismatch_detail is not None
    assert check.mismatch_detail["reason"] == "row_multiset_mismatch"


def test_equivalence_preserves_outer_order_by_order() -> None:
    check = _check(
        original_rows=[(1,), (2,)],
        candidate_rows=[(2,), (1,)],
        original_sql="SELECT a FROM t ORDER BY a",
        candidate_sql="SELECT a FROM t ORDER BY a",
    )

    assert not check.passed
    assert check.mismatch_detail is not None
    assert check.mismatch_detail["reason"] == "ordered_rows_mismatch"


def test_equivalence_rejects_union_all_overlap_duplicate_change() -> None:
    check = _check(original_rows=[(10,), (20,)], candidate_rows=[(10,), (20,), (20,)])

    assert not check.passed
    assert check.mismatch_detail is not None
    assert check.mismatch_detail["reason"] == "row_multiset_mismatch"
