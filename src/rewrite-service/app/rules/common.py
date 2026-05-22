from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from pglast import parse_sql
from pglast.stream import RawStream

SchemaContext = Mapping[str, Any]


def _select_statement(sql: str) -> Any | None:
    try:
        statements = parse_sql(sql)
    except Exception:
        return None
    if len(statements) != 1:
        return None
    stmt = statements[0].stmt
    return stmt if stmt.__class__.__name__ == "SelectStmt" else None


def _has_disallowed_outer_rule_shape(stmt: Any) -> bool:
    return bool(
        getattr(stmt, "distinctClause", None)
        or getattr(stmt, "limitCount", None)
        or getattr(stmt, "limitOffset", None)
        or getattr(stmt, "withClause", None)
        or getattr(stmt, "lockingClause", None)
        or getattr(stmt, "windowClause", None)
        or _enum_name(getattr(stmt, "op", None)) != "SETOP_NONE"
    )


def _render_select(
    stmt: Any,
    *,
    from_sql: str | None = None,
    where_sql: str | None = None,
    omit_where: bool = False,
    omit_group_by: bool = False,
) -> str:
    parts = [f"SELECT {_render_target_list(stmt.targetList)}"]
    if stmt.fromClause:
        parts.append(f"FROM {from_sql or _render_from_clause(stmt.fromClause)}")
    if not omit_where:
        effective_where = where_sql if where_sql is not None else (
            _raw(stmt.whereClause) if stmt.whereClause else ""
        )
        if effective_where:
            parts.append(f"WHERE {effective_where}")
    if stmt.groupClause and not omit_group_by:
        parts.append(f"GROUP BY {_render_node_list(stmt.groupClause)}")
    if stmt.havingClause:
        parts.append(f"HAVING {_raw(stmt.havingClause)}")
    if stmt.sortClause:
        parts.append(f"ORDER BY {_render_node_list(stmt.sortClause)}")
    return "\n".join(parts) + ";"


def _render_select_with_target_sql(stmt: Any, target_sql: str) -> str:
    parts = [f"SELECT {target_sql}"]
    if stmt.fromClause:
        parts.append(f"FROM {_render_from_clause(stmt.fromClause)}")
    if stmt.whereClause:
        parts.append(f"WHERE {_raw(stmt.whereClause)}")
    if stmt.groupClause:
        parts.append(f"GROUP BY {_render_node_list(stmt.groupClause)}")
    if stmt.havingClause:
        parts.append(f"HAVING {_raw(stmt.havingClause)}")
    if stmt.sortClause:
        parts.append(f"ORDER BY {_render_node_list(stmt.sortClause)}")
    if stmt.limitCount:
        parts.append(f"LIMIT {_raw(stmt.limitCount)}")
    if stmt.limitOffset:
        parts.append(f"OFFSET {_raw(stmt.limitOffset)}")
    return "\n".join(parts) + ";"


def _render_target_list(target_list: Iterable[Any]) -> str:
    return ", ".join(_raw(target) for target in target_list)


def _render_from_clause(from_clause: Iterable[Any]) -> str:
    return ", ".join(_raw(item) for item in from_clause)


def _render_node_list(nodes: Iterable[Any]) -> str:
    return ", ".join(_raw(node) for node in nodes)


def _raw(node: Any) -> str:
    return str(RawStream()(node)).strip()  # type: ignore[no-untyped-call]


def _raw_bool_arg(node: Any, parent_boolop: str) -> str:
    raw = _raw(node)
    if node.__class__.__name__ != "BoolExpr":
        return raw

    child_boolop = _enum_name(getattr(node, "boolop", None))
    if (parent_boolop, child_boolop) in {
        ("AND_EXPR", "OR_EXPR"),
        ("OR_EXPR", "AND_EXPR"),
    }:
        return f"({raw})"
    return raw


def _top_level_bool_args(node: Any, boolop: str) -> list[Any]:
    if (
        node.__class__.__name__ == "BoolExpr"
        and _enum_name(getattr(node, "boolop", None)) == boolop
    ):
        return list(node.args or [])
    return [node]


def _deduplicate_predicates(
    nodes: Iterable[Any],
    *,
    parent_boolop: str | None = None,
) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for node in nodes:
        raw = _raw_bool_arg(node, parent_boolop) if parent_boolop else _raw(node)
        key = _normalize_sql_fragment(raw)
        if key in seen:
            continue
        seen.add(key)
        result.append(raw)
    return result


def _simple_column_numeric_comparison(node: Any) -> tuple[str, str, Decimal] | None:
    if node.__class__.__name__ != "A_Expr":
        return None
    operator = _operator_name(node)
    if operator not in {">", "<"}:
        return None
    if node.lexpr.__class__.__name__ != "ColumnRef":
        return None
    column = _raw(node.lexpr)
    try:
        value = Decimal(_raw(node.rexpr))
    except InvalidOperation:
        return None
    return column, operator, value


def _operator_name(node: Any) -> str:
    names = getattr(node, "name", None) or []
    if not names:
        return ""
    return str(getattr(names[0], "sval", ""))


def _enum_name(value: Any) -> str:
    return getattr(value, "name", str(value))


def _is_positive_in_sublink(node: Any) -> bool:
    return (
        node.__class__.__name__ == "SubLink"
        and _enum_name(getattr(node, "subLinkType", None)) == "ANY_SUBLINK"
        and node.testexpr.__class__.__name__ == "ColumnRef"
        and _single_from_single_target_select(node.subselect)
    )


def _single_from_single_target_select(stmt: Any) -> bool:
    return (
        stmt.__class__.__name__ == "SelectStmt"
        and len(stmt.targetList or []) == 1
        and len(stmt.fromClause or []) == 1
        and not _has_disallowed_subquery_shape(stmt)
    )


def _has_disallowed_subquery_shape(stmt: Any) -> bool:
    return bool(
        stmt.distinctClause
        or stmt.groupClause
        or stmt.havingClause
        or stmt.sortClause
        or stmt.limitCount
        or stmt.limitOffset
        or stmt.windowClause
        or _enum_name(getattr(stmt, "op", None)) != "SETOP_NONE"
    )


def _exists_sql_from_sublink(sublink: Any, *, negated: bool) -> str | None:
    subquery = sublink.subselect
    if not _single_from_single_target_select(subquery):
        return None

    sub_col = _raw(subquery.targetList[0].val)
    outer_col = _raw(sublink.testexpr)
    sub_condition = _raw(subquery.whereClause) if subquery.whereClause else ""
    conditions = []
    if sub_condition:
        conditions.append(f"({sub_condition})")
    conditions.append(f"{sub_col} = {outer_col}")
    exists = (
        "EXISTS (\n"
        "  SELECT 1\n"
        f"  FROM {_render_from_clause(subquery.fromClause)}\n"
        f"  WHERE {' AND '.join(conditions)}\n"
        ")"
    )
    return f"NOT {exists}" if negated else exists


def _contains_column_equality(node: Any) -> bool:
    if node.__class__.__name__ == "A_Expr" and _operator_name(node) == "=":
        return bool(
            node.lexpr.__class__.__name__ == "ColumnRef"
            and node.rexpr.__class__.__name__ == "ColumnRef"
        )
    if node.__class__.__name__ == "BoolExpr":
        return any(_contains_column_equality(arg) for arg in node.args or [])
    return False


def _target_list_has_aggregate_or_window(target_list: Iterable[Any]) -> bool:
    target_sql = _render_target_list(target_list)
    return bool(re.search(r"\b(count|sum|avg|min|max)\s*\(|\bover\s*\(", target_sql, re.IGNORECASE))


def _column_ref_names(nodes: Iterable[Any]) -> list[str]:
    names = []
    for node in nodes:
        if node.__class__.__name__ != "ColumnRef":
            return []
        names.append(_raw(node).split(".")[-1].lower())
    return names


def _group_output_labels(group_nodes: Iterable[Any], target_list: Iterable[Any]) -> list[str]:
    targets = list(target_list)
    labels = []
    for group_node in group_nodes:
        group_sql = _normalize_sql_fragment(_raw(group_node))
        matched_label = ""
        for target in targets:
            if _normalize_sql_fragment(_raw(target.val)) == group_sql:
                matched_label = _target_label(target)
                break
        if matched_label:
            labels.append(matched_label)
            continue
        if group_node.__class__.__name__ != "ColumnRef":
            return []
        labels.append(_raw(group_node).split(".")[-1].lower())
    return labels


def _name_tuple(nodes: Iterable[Any]) -> str:
    return ".".join(getattr(node, "sval", "") for node in nodes)


def _table_aliases(from_clause: Iterable[Any]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in from_clause:
        if item.__class__.__name__ != "RangeVar":
            continue
        table_name = getattr(item, "relname", "")
        aliases[table_name] = table_name
        alias = getattr(item, "alias", None)
        if alias is not None:
            aliases[getattr(alias, "aliasname", table_name)] = table_name
    return aliases


def _range_names(range_var: Any) -> set[str]:
    names = {getattr(range_var, "relname", "")}
    alias = getattr(range_var, "alias", None)
    if alias is not None:
        names.add(getattr(alias, "aliasname", ""))
    return names


def _qualified_column_prefix(column_sql: str, aliases: Mapping[str, str]) -> str:
    if "." not in column_sql:
        return ""
    prefix = column_sql.split(".", 1)[0]
    return aliases.get(prefix, prefix)


def _range_subselect_alias_name(source: Any) -> str:
    alias = getattr(source, "alias", None)
    if alias is None:
        return ""
    return str(getattr(alias, "aliasname", ""))


def _has_disallowed_projection_prune_shape(stmt: Any) -> bool:
    if (
        stmt.distinctClause
        or stmt.windowClause
        or _enum_name(getattr(stmt, "op", None)) != "SETOP_NONE"
    ):
        return True

    return bool(
        re.search(
            r"\b(random|clock_timestamp|timeofday|txid_current|nextval|setval)\s*\(",
            _raw(stmt),
            re.IGNORECASE,
        )
    )


def _outer_alias_context_sql(stmt: Any) -> str:
    parts = [_render_target_list(stmt.targetList or [])]
    if stmt.whereClause:
        parts.append(_raw(stmt.whereClause))
    if stmt.groupClause:
        parts.append(_render_node_list(stmt.groupClause))
    if stmt.havingClause:
        parts.append(_raw(stmt.havingClause))
    if stmt.sortClause:
        parts.append(_render_node_list(stmt.sortClause))
    return " ".join(parts)


def _column_is_not_null(
    schema_context: SchemaContext,
    column_sql: str,
    aliases: Mapping[str, str],
) -> bool:
    column_name = column_sql.split(".")[-1].lower()
    table_hint = _qualified_column_prefix(column_sql, aliases)
    if not table_hint:
        distinct_tables = {table for table in aliases.values() if table}
        if len(distinct_tables) == 1:
            table_hint = next(iter(distinct_tables))
    matches = []

    for table in schema_context.get("tables", []):
        if not isinstance(table, Mapping):
            continue
        table_name = str(table.get("name", "")).lower()
        if table_hint and table_name != table_hint.lower():
            continue
        for column in table.get("columns", []):
            if not isinstance(column, Mapping):
                continue
            if str(column.get("name", "")).lower() != column_name:
                continue
            nullable = column.get("nullable", column.get("is_nullable", True))
            matches.append(nullable is False or str(nullable).lower() == "false")

    return len(matches) == 1 and matches[0]


def _output_shape_matches(original_sql: str, candidate_sql: str) -> tuple[bool, bool]:
    original = _select_statement(original_sql)
    candidate = _select_statement(candidate_sql)
    if not original or not candidate:
        return False, False

    original_targets = list(original.targetList or [])
    candidate_targets = list(candidate.targetList or [])
    columns_match = len(original_targets) == len(candidate_targets)
    labels_match = [_target_label(target) for target in original_targets] == [
        _target_label(target) for target in candidate_targets
    ]
    return columns_match, labels_match


def _target_label(target: Any) -> str:
    alias = getattr(target, "name", None)
    if alias:
        return str(alias).lower()
    return _raw(target.val).split(".")[-1].lower()


def _referenced_alias_columns(sql: str, alias: str) -> set[str]:
    return {
        match.group(1).lower()
        for match in re.finditer(rf"\b{re.escape(alias)}\.([a-zA-Z_][\w]*)\b", sql)
    }


def _normalize_sql_fragment(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().rstrip(";")).lower()
