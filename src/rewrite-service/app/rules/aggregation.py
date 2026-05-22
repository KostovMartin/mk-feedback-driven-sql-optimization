from __future__ import annotations

from app.rules.common import (
    _column_ref_names,
    _group_output_labels,
    _raw,
    _render_select,
    _select_statement,
    _target_label,
    _target_list_has_aggregate_or_window,
)


def _apply_redundant_group_by(sql: str) -> str | None:
    stmt = _select_statement(sql)
    if not stmt or not stmt.groupClause or len(stmt.fromClause or []) != 1:
        return None
    if stmt.distinctClause or stmt.havingClause:
        return None

    source = list(stmt.fromClause)[0]
    if source.__class__.__name__ != "RangeSubselect":
        return None
    subquery = getattr(source, "subquery", None)
    if (
        subquery is None
        or subquery.__class__.__name__ != "SelectStmt"
        or not subquery.groupClause
        or subquery.havingClause
        or subquery.distinctClause
    ):
        return None
    if _target_list_has_aggregate_or_window(stmt.targetList or []):
        return None

    outer_targets = [_target_label(target) for target in stmt.targetList or []]
    inner_targets = [_target_label(target) for target in subquery.targetList or []]
    outer_group = _column_ref_names(stmt.groupClause or [])
    inner_group = _group_output_labels(subquery.groupClause or [], subquery.targetList or [])
    if not outer_targets or outer_targets != inner_targets:
        return None
    if outer_group != outer_targets:
        return None
    if not set(inner_group).issubset(set(outer_group)):
        return None

    return _render_select(stmt, from_sql=_raw(source), omit_group_by=True)
