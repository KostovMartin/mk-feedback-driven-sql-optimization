from __future__ import annotations

from app.rules.common import (
    _has_disallowed_projection_prune_shape,
    _outer_alias_context_sql,
    _range_subselect_alias_name,
    _raw,
    _referenced_alias_columns,
    _render_select,
    _render_select_with_target_sql,
    _select_statement,
    _target_label,
)


def _apply_subquery_column_prune(sql: str) -> str | None:
    stmt = _select_statement(sql)
    if not stmt or len(stmt.fromClause or []) != 1:
        return None

    source = list(stmt.fromClause)[0]
    if source.__class__.__name__ != "RangeSubselect":
        return None
    alias = _range_subselect_alias_name(source)
    if not alias:
        return None
    subquery = getattr(source, "subquery", None)
    if subquery is None or subquery.__class__.__name__ != "SelectStmt":
        return None
    if _has_disallowed_projection_prune_shape(subquery):
        return None

    needed = _referenced_alias_columns(_outer_alias_context_sql(stmt), alias)
    if not needed:
        return None

    inner_targets = list(subquery.targetList or [])
    available = {_target_label(target) for target in inner_targets}
    if not needed.issubset(available):
        return None
    kept_targets = [
        target for target in inner_targets if _target_label(target).lower() in needed
    ]
    if len(kept_targets) == len(inner_targets) or not kept_targets:
        return None

    inner_sql = _render_select_with_target_sql(
        subquery,
        ", ".join(_raw(target) for target in kept_targets),
    ).rstrip(";")
    return _render_select(stmt, from_sql=f"({inner_sql}) AS {alias}")
