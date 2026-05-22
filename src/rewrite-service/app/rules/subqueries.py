from __future__ import annotations

from app.rules.common import (
    SchemaContext,
    _column_is_not_null,
    _contains_column_equality,
    _enum_name,
    _exists_sql_from_sublink,
    _has_disallowed_subquery_shape,
    _is_positive_in_sublink,
    _name_tuple,
    _operator_name,
    _raw,
    _render_from_clause,
    _render_select,
    _select_statement,
    _single_from_single_target_select,
    _table_aliases,
)


def _apply_in_to_exists(sql: str) -> str | None:
    stmt = _select_statement(sql)
    if not stmt or not stmt.whereClause:
        return None

    sublink = stmt.whereClause
    if not _is_positive_in_sublink(sublink):
        return None

    exists_sql = _exists_sql_from_sublink(sublink, negated=False)
    if not exists_sql:
        return None

    return _render_select(stmt, where_sql=exists_sql)


def _apply_not_in_to_not_exists(sql: str, schema_context: SchemaContext) -> str | None:
    stmt = _select_statement(sql)
    if not stmt or not stmt.whereClause:
        return None

    if _enum_name(getattr(stmt.whereClause, "boolop", None)) != "NOT_EXPR":
        return None

    args = list(getattr(stmt.whereClause, "args", []) or [])
    if len(args) != 1 or not _is_positive_in_sublink(args[0]):
        return None

    sublink = args[0]
    subquery = sublink.subselect
    sub_col = _raw(subquery.targetList[0].val)
    outer_col = _raw(sublink.testexpr)
    outer_aliases = _table_aliases(stmt.fromClause or [])
    sub_aliases = _table_aliases(subquery.fromClause or [])

    if not _column_is_not_null(schema_context, outer_col, outer_aliases):
        return None
    if not _column_is_not_null(schema_context, sub_col, sub_aliases):
        return None

    exists_sql = _exists_sql_from_sublink(sublink, negated=True)
    if not exists_sql:
        return None

    return _render_select(stmt, where_sql=exists_sql)


def _apply_count_gt_zero_to_exists(sql: str) -> str | None:
    stmt = _select_statement(sql)
    if not stmt or not stmt.whereClause or stmt.whereClause.__class__.__name__ != "A_Expr":
        return None

    expression = stmt.whereClause
    if _operator_name(expression) != ">" or _raw(expression.rexpr) != "0":
        return None

    sublink = expression.lexpr
    if sublink.__class__.__name__ != "SubLink":
        return None
    if _enum_name(getattr(sublink, "subLinkType", None)) != "EXPR_SUBLINK":
        return None

    subquery = sublink.subselect
    if not _single_from_single_target_select(subquery):
        return None
    target = subquery.targetList[0].val
    if target.__class__.__name__ != "FuncCall":
        return None
    if _name_tuple(target.funcname).lower() != "count" or not getattr(target, "agg_star", False):
        return None
    if _has_disallowed_subquery_shape(subquery):
        return None
    if not subquery.whereClause or not _contains_column_equality(subquery.whereClause):
        return None

    where_sql = _raw(subquery.whereClause)
    return _render_select(
        stmt,
        where_sql=(
            "EXISTS (\n"
            "  SELECT 1\n"
            f"  FROM {_render_from_clause(subquery.fromClause)}\n"
            f"  WHERE {where_sql}\n"
            ")"
        ),
    )
