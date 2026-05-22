from __future__ import annotations

import hashlib
import re

from pglast import parse_sql, prettify

from app.api.models import ParseAnalyzeResponse

_FORBIDDEN_PREFIXES = ("insert", "update", "delete", "merge", "create", "alter", "drop", "truncate")
_DISALLOWED_STATEMENT_RE = re.compile(
    r"\b(insert|update|delete|merge|create|alter|drop|truncate)\b",
    re.IGNORECASE,
)
_VOLATILE_FUNCTION_RE = re.compile(
    r"\b(random|clock_timestamp|timeofday|txid_current|nextval|setval)\s*\(",
    re.IGNORECASE,
)
_PARAMETER_RE = re.compile(r"\$(\d+)")
_TABLE_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][\w.]*)", re.IGNORECASE)


def parse_and_analyze(sql: str, check_fragment: bool = True) -> ParseAnalyzeResponse:
    violations: list[str] = []
    parsed = True

    try:
        statements = parse_sql(sql)
    except Exception as exc:  # pglast exposes parser-specific exceptions across versions.
        return ParseAnalyzeResponse(
            parsed=False,
            in_supported_fragment=False,
            tables_referenced=[],
            columns_in_where=[],
            columns_in_join=[],
            columns_in_group_by=[],
            has_order_by=False,
            has_aggregation=False,
            has_subquery=False,
            has_cte=False,
            parameter_positions=extract_parameter_positions(sql),
            fragment_violations=[f"parse_error: {exc}"],
        )

    if len(statements) != 1:
        violations.append("multiple_statements")

    lowered = sql.strip().lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        violations.append("not_select")

    if lowered.startswith(_FORBIDDEN_PREFIXES):
        violations.append("side_effecting_statement")

    if check_fragment and _DISALLOWED_STATEMENT_RE.search(lowered):
        violations.append("contains_disallowed_statement")

    if check_fragment and _VOLATILE_FUNCTION_RE.search(lowered):
        violations.append("contains_volatile_function")

    return ParseAnalyzeResponse(
        parsed=parsed,
        in_supported_fragment=len(violations) == 0,
        tables_referenced=extract_tables(sql),
        columns_in_where=[],
        columns_in_join=[],
        columns_in_group_by=[],
        has_order_by=bool(re.search(r"\border\s+by\b", sql, re.IGNORECASE)),
        has_aggregation=bool(re.search(r"\b(count|sum|avg|min|max)\s*\(", sql, re.IGNORECASE)),
        has_subquery=bool(re.search(r"\(\s*select\b", sql, re.IGNORECASE)),
        has_cte=bool(re.search(r"^\s*with\b", sql, re.IGNORECASE)),
        parameter_positions=extract_parameter_positions(sql),
        fragment_violations=violations,
    )


def canonical_hash(sql: str) -> str:
    canonical = canonicalize(sql)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonicalize(sql: str) -> str:
    try:
        return prettify(sql, safety_belt=True).strip()
    except Exception:
        return "\n".join(line.rstrip() for line in sql.replace("\r\n", "\n").split("\n")).strip()


def extract_parameter_positions(sql: str) -> list[str]:
    positions = {int(match.group(1)) for match in _PARAMETER_RE.finditer(sql)}
    return [f"${position}" for position in sorted(positions)]


def extract_tables(sql: str) -> list[str]:
    tables = {match.group(1).split(".")[-1] for match in _TABLE_RE.finditer(sql)}
    return sorted(tables)
