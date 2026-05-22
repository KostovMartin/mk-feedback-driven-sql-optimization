from __future__ import annotations

from decimal import Decimal

from app.rules.common import (
    _deduplicate_predicates,
    _enum_name,
    _operator_name,
    _raw,
    _raw_bool_arg,
    _render_select,
    _select_statement,
    _simple_column_numeric_comparison,
    _top_level_bool_args,
)


def _apply_redundant_predicate(sql: str) -> str | None:
    stmt = _select_statement(sql)
    if not stmt or not stmt.whereClause:
        return None

    conjuncts = _top_level_bool_args(stmt.whereClause, "AND_EXPR")
    if len(conjuncts) < 2:
        return None

    keep = [True] * len(conjuncts)
    strongest_by_key: dict[tuple[str, str], tuple[int, Decimal]] = {}
    for index, conjunct in enumerate(conjuncts):
        parsed = _simple_column_numeric_comparison(conjunct)
        if not parsed:
            continue
        column, operator, value = parsed
        key = (column, operator)
        current = strongest_by_key.get(key)
        if current is None:
            strongest_by_key[key] = (index, value)
            continue

        current_index, current_value = current
        if operator == ">" and value > current_value:
            keep[current_index] = False
            strongest_by_key[key] = (index, value)
        elif operator == ">" and value <= current_value:
            keep[index] = False
        elif operator == "<" and value < current_value:
            keep[current_index] = False
            strongest_by_key[key] = (index, value)
        elif operator == "<" and value >= current_value:
            keep[index] = False

    if all(keep):
        return None

    where_sql = " AND ".join(
        _raw_bool_arg(conjunct, "AND_EXPR")
        for conjunct, should_keep in zip(conjuncts, keep, strict=True)
        if should_keep
    )
    return _render_select(stmt, where_sql=where_sql)


def _apply_boolean_simplification(sql: str) -> str | None:
    stmt = _select_statement(sql)
    if not stmt or not stmt.whereClause:
        return None

    and_args = _top_level_bool_args(stmt.whereClause, "AND_EXPR")
    if len(and_args) > 1:
        simplified = _deduplicate_predicates(
            (arg for arg in and_args if _raw(arg).upper() != "TRUE"),
            parent_boolop="AND_EXPR",
        )
        if 0 < len(simplified) < len(and_args):
            return _render_select(stmt, where_sql=" AND ".join(simplified))

    or_args = _top_level_bool_args(stmt.whereClause, "OR_EXPR")
    if len(or_args) > 1:
        simplified = _deduplicate_predicates(
            (arg for arg in or_args if _raw(arg).upper() != "FALSE"),
            parent_boolop="OR_EXPR",
        )
        if 0 < len(simplified) < len(or_args):
            return _render_select(stmt, where_sql=" OR ".join(simplified))

    return None


def _apply_comparison_normalization(sql: str) -> str | None:
    stmt = _select_statement(sql)
    if not stmt or not stmt.whereClause:
        return None

    if _enum_name(getattr(stmt.whereClause, "boolop", None)) != "NOT_EXPR":
        return None

    args = list(getattr(stmt.whereClause, "args", []) or [])
    if len(args) != 1:
        return None

    expression = args[0]
    if expression.__class__.__name__ != "A_Expr":
        return None

    operator = _operator_name(expression)
    replacement = {
        "=": "<>",
        ">": "<=",
        "<": ">=",
        ">=": "<",
        "<=": ">",
        "<>": "=",
        "!=": "=",
    }.get(operator)
    if not replacement:
        return None

    return _render_select(
        stmt,
        where_sql=f"{_raw(expression.lexpr)} {replacement} {_raw(expression.rexpr)}",
    )
