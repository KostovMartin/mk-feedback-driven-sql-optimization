from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.rules.common import (
    _operator_name,
    _qualified_column_prefix,
    _range_names,
    _raw,
    _raw_bool_arg,
    _render_select,
    _select_statement,
    _table_aliases,
    _top_level_bool_args,
)


def _apply_implicit_to_explicit_join(sql: str, *, start_from_end: bool = False) -> str | None:
    stmt = _select_statement(sql)
    if not stmt or not stmt.whereClause:
        return None

    from_items = list(stmt.fromClause or [])
    if len(from_items) < 2 or any(item.__class__.__name__ != "RangeVar" for item in from_items):
        return None
    if start_from_end and len(from_items) < 3:
        return None

    conjuncts = _top_level_bool_args(stmt.whereClause, "AND_EXPR")
    if not conjuncts:
        return None

    aliases = _table_aliases(from_items)
    start_index = len(from_items) - 1 if start_from_end else 0
    start_item = from_items[start_index]
    joined_names = set(_range_names(start_item))
    remaining_items = [item for index, item in enumerate(from_items) if index != start_index]
    used_conjunct_indexes: set[int] = set()
    from_sql = _raw(start_item)

    while remaining_items:
        best_item = None
        best_predicates: list[tuple[int, str]] = []

        for item in remaining_items:
            item_names = _range_names(item)
            predicates = [
                (index, _raw(conjunct))
                for index, conjunct in enumerate(conjuncts)
                if index not in used_conjunct_indexes
                and _is_join_equality_between(conjunct, joined_names, item_names, aliases)
            ]
            if not predicates:
                continue
            if not best_predicates or predicates[0][0] < best_predicates[0][0]:
                best_item = item
                best_predicates = predicates

        if best_item is None:
            return None

        from_sql = (
            f"{from_sql} INNER JOIN {_raw(best_item)} "
            f"ON {' AND '.join(predicate for _, predicate in best_predicates)}"
        )
        used_conjunct_indexes.update(index for index, _ in best_predicates)
        joined_names.update(_range_names(best_item))
        remaining_items.remove(best_item)

    if not used_conjunct_indexes:
        return None

    remaining = [
        conjunct for index, conjunct in enumerate(conjuncts) if index not in used_conjunct_indexes
    ]
    where_sql = " AND ".join(_raw_bool_arg(conjunct, "AND_EXPR") for conjunct in remaining)
    return _render_select(stmt, from_sql=from_sql, where_sql=where_sql, omit_where=not where_sql)


def _is_join_equality_between(
    conjunct: Any,
    joined_names: set[str],
    item_names: set[str],
    aliases: Mapping[str, str],
) -> bool:
    if conjunct.__class__.__name__ != "A_Expr" or _operator_name(conjunct) != "=":
        return False
    if conjunct.lexpr.__class__.__name__ != "ColumnRef":
        return False
    if conjunct.rexpr.__class__.__name__ != "ColumnRef":
        return False

    left_ref = _qualified_column_prefix(_raw(conjunct.lexpr), aliases)
    right_ref = _qualified_column_prefix(_raw(conjunct.rexpr), aliases)
    if not left_ref or not right_ref:
        return False

    return (left_ref in joined_names and right_ref in item_names) or (
        left_ref in item_names and right_ref in joined_names
    )
