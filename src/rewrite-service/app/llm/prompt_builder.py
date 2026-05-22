from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

PROMPT_TEMPLATE_VERSION = "local-llm-single-candidate-v5"

SYSTEM_PROMPT = """You are a PostgreSQL query optimization expert.
Rewrite SQL queries to improve execution performance while preserving exact semantic equivalence.

Rules:
- Return the same output columns in the same order.
- Preserve result rows, duplicate semantics, NULL semantics, and outer ORDER BY behavior.
- Preserve parameter placeholders exactly, such as $1 and $2.
- Do not introduce non-deterministic functions, side effects, DDL, DML, comments, or explanations.
- Your response must begin with SELECT or WITH and end with a semicolon.
- When a rewrite opportunity hint is supplied, apply that hint if it is semantically valid.
- Treat schema metadata, row estimates, and indexes as performance context, not semantic proof.
- If there is no safe improvement, respond with NO_OPTIMIZATION.

Output only one SQL SELECT query, or exactly NO_OPTIMIZATION."""


@dataclass(frozen=True)
class PromptBundle:
    system_prompt: str
    user_prompt: str
    prompt_version: str
    prompt_hash: str


def build_single_candidate_prompt(
    *,
    normalized_sql: str,
    schema_context: dict[str, Any],
    baseline_plan: dict[str, Any],
) -> PromptBundle:
    schema_text = _schema_context_text(schema_context)
    plan_text = _plan_context_text(baseline_plan)
    hint_text = _rewrite_hint_text(normalized_sql)
    user_prompt = (
        "Optimize this PostgreSQL SELECT query for execution performance.\n\n"
        "SCHEMA CONTEXT:\n"
        f"{schema_text}\n\n"
        "BASELINE PLAN CONTEXT:\n"
        f"{plan_text}\n\n"
        "REWRITE OPPORTUNITY HINTS:\n"
        f"{hint_text}\n\n"
        "ORIGINAL QUERY:\n"
        f"{normalized_sql.strip()}\n\n"
        "If a listed hint is applicable, return the rewritten query directly. "
        "Do not answer that the original query is already optimal when the hinted "
        "rewrite preserves semantics. "
        "Return only a semantically equivalent rewritten SQL SELECT query. "
        "Use the same parameter placeholders."
    )
    prompt_hash = hashlib.sha256(
        f"{PROMPT_TEMPLATE_VERSION}\n{SYSTEM_PROMPT}\n{user_prompt}".encode()
    ).hexdigest()
    return PromptBundle(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        prompt_version=PROMPT_TEMPLATE_VERSION,
        prompt_hash=prompt_hash,
    )


def _schema_context_text(schema_context: dict[str, Any]) -> str:
    if not schema_context:
        return "No schema context supplied."
    return json.dumps(schema_context, sort_keys=True, indent=2)


def _plan_context_text(baseline_plan: dict[str, Any]) -> str:
    if not baseline_plan:
        return "No baseline plan supplied."
    return json.dumps(baseline_plan, sort_keys=True, indent=2)


def _rewrite_hint_text(normalized_sql: str) -> str:
    lowered = " ".join(normalized_sql.lower().split())
    hints = []

    if "count(*)" in lowered and "> 0" in lowered:
        hints.append(
            "Replace scalar COUNT(*) > 0 existence tests with WHERE EXISTS "
            "(SELECT 1 FROM the same subquery predicate) when it preserves parameters, "
            "rows, duplicates, and NULL behavior."
        )

    if " in ( select " in lowered or " in (select " in lowered:
        hints.append(
            "Consider replacing positive IN subqueries with EXISTS or a semijoin when NULL, "
            "duplicate, and parameter behavior is preserved."
        )

    if lowered.startswith("select distinct") and " join " in lowered:
        hints.append(
            "If DISTINCT only removes duplicates introduced by a relationship-table join, "
            "consider replacing the relationship join with WHERE EXISTS while preserving the "
            "same projected columns."
        )

    if lowered.startswith("with ") and " materialized " not in lowered:
        hints.append(
            "For a non-recursive single-use CTE, consider inlining it as a derived table when "
            "that preserves output labels and parameter placeholders."
        )

    if " or " in lowered:
        hints.append(
            "For OR predicates, consider duplicate-safe UNION or disjoint UNION ALL branches. "
            "Do not use UNION ALL unless rows satisfying both predicates are excluded from the "
            "second branch."
        )

    if lowered.count("select sum(") >= 2 and lowered.count(" from lineitem") >= 2:
        hints.append(
            "When a query computes multiple scalar aggregates over the same table, consider a "
            "single scan with conditional aggregation using FILTER or CASE."
        )

    if "select avg(" in lowered and " from lineitem" in lowered:
        hints.append(
            "For scalar aggregate subqueries used in WHERE, consider computing the aggregate "
            "once in a single-row derived table or CTE, then joining it to the outer query."
        )

    if "(select " in lowered and " from orders " in lowered:
        hints.append(
            "For correlated scalar lookup subqueries in the SELECT list, consider replacing "
            "them with joins when each lookup is key-preserving and output columns remain the same."
        )

    if hints:
        return "\n".join(f"- {hint}" for hint in hints)
    return "No pattern-specific hint supplied; propose a rewrite only when it is clearly safe."
