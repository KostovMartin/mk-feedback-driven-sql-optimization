from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from app.api.models import CandidateResponse, ParameterMapping, StructuralValidation
from app.parser.sql_parser import (
    canonical_hash,
    extract_parameter_positions,
    extract_tables,
    parse_and_analyze,
)
from app.rules.aggregation import _apply_redundant_group_by
from app.rules.common import (
    SchemaContext,
    _has_disallowed_outer_rule_shape,
    _output_shape_matches,
    _select_statement,
)
from app.rules.joins import _apply_implicit_to_explicit_join
from app.rules.predicates import (
    _apply_boolean_simplification,
    _apply_comparison_normalization,
    _apply_redundant_predicate,
)
from app.rules.projection import _apply_subquery_column_prune
from app.rules.subqueries import (
    _apply_count_gt_zero_to_exists,
    _apply_in_to_exists,
    _apply_not_in_to_not_exists,
)

RuleFunction = Callable[[str, SchemaContext], str | None]


@dataclass(frozen=True)
class RuleSpec:
    rule_id: str
    family: str
    apply: RuleFunction


_RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        "predicate_redundant_elimination",
        "A",
        lambda sql, _: _apply_redundant_predicate(sql),
    ),
    RuleSpec("boolean_simplification", "A", lambda sql, _: _apply_boolean_simplification(sql)),
    RuleSpec("comparison_normalization", "A", lambda sql, _: _apply_comparison_normalization(sql)),
    RuleSpec("subquery_column_prune", "E", lambda sql, _: _apply_subquery_column_prune(sql)),
    RuleSpec("in_to_exists", "B", lambda sql, _: _apply_in_to_exists(sql)),
    RuleSpec(
        "not_in_to_not_exists",
        "B",
        lambda sql, context: _apply_not_in_to_not_exists(sql, context),
    ),
    RuleSpec("count_gt_zero_to_exists", "B", lambda sql, _: _apply_count_gt_zero_to_exists(sql)),
    RuleSpec(
        "implicit_to_explicit_join",
        "C",
        lambda sql, _: _apply_implicit_to_explicit_join(sql),
    ),
    RuleSpec(
        "implicit_to_explicit_join_alternate_order",
        "C",
        lambda sql, _: _apply_implicit_to_explicit_join(sql, start_from_end=True),
    ),
    RuleSpec("redundant_group_by_elimination", "D", lambda sql, _: _apply_redundant_group_by(sql)),
)


def apply_rule_candidates(
    sql: str,
    max_candidates: int,
    allowed_rule_families: Iterable[str] | None = None,
    schema_context: SchemaContext | None = None,
) -> tuple[list[CandidateResponse], float]:
    started = time.perf_counter()
    candidates: list[CandidateResponse] = []
    seen_hashes: set[str] = set()
    allowed = {family.upper() for family in allowed_rule_families or []}
    context = schema_context or {}
    stmt = _select_statement(sql)

    if not stmt or _has_disallowed_outer_rule_shape(stmt):
        elapsed_ms = (time.perf_counter() - started) * 1000
        return candidates, elapsed_ms

    for rule in _RULES:
        if len(candidates) >= max_candidates:
            break
        if allowed and rule.family not in allowed:
            continue

        candidate_sql = rule.apply(sql, context)
        if not candidate_sql:
            continue

        candidate_hash = canonical_hash(candidate_sql)
        if candidate_hash in seen_hashes:
            continue

        seen_hashes.add(candidate_hash)
        candidates.append(_build_rule_candidate(sql, candidate_sql, rule.rule_id))

    elapsed_ms = (time.perf_counter() - started) * 1000
    return candidates, elapsed_ms


def build_candidate_response(
    sql: str,
    candidate_sql: str,
    *,
    source_type: str,
    source_detail: str,
    applied_rules: list[str],
) -> CandidateResponse:
    original_positions = extract_parameter_positions(sql)
    rewritten_positions = extract_parameter_positions(candidate_sql)
    candidate_analysis = parse_and_analyze(candidate_sql, check_fragment=True)
    output_columns_match, output_labels_match = _output_shape_matches(sql, candidate_sql)
    return CandidateResponse(
        sql_text=candidate_sql,
        canonical_hash=canonical_hash(candidate_sql),
        source_type=source_type,
        source_detail=source_detail,
        applied_rules=applied_rules,
        parameter_mapping=ParameterMapping(
            original_positions=original_positions,
            rewritten_positions=rewritten_positions,
            mapping={position: position for position in original_positions},
        ),
        structural_validation=StructuralValidation(
            passed=(
                candidate_analysis.parsed
                and candidate_analysis.in_supported_fragment
                and original_positions == rewritten_positions
                and output_columns_match
                and output_labels_match
            ),
            in_fragment=candidate_analysis.in_supported_fragment,
            tables_referenced=extract_tables(candidate_sql),
            output_columns_match=output_columns_match,
            output_labels_match=output_labels_match,
        ),
    )


def _build_rule_candidate(sql: str, candidate_sql: str, rule_id: str) -> CandidateResponse:
    return build_candidate_response(
        sql,
        candidate_sql,
        source_type="rule",
        source_detail=f"rule:{rule_id}",
        applied_rules=[rule_id],
    )
