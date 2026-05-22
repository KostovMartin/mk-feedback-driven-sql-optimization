from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ParameterValue(BaseModel):
    position: str
    type: str
    value: Any


class ParameterSet(BaseModel):
    parameter_set_id: str
    parameters: list[ParameterValue]


class GenerationConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_name: str = Field(default="", alias="model")
    model_provider: str = "ollama"
    temperature: float = 0.7
    max_llm_candidates: int = 3
    max_rule_candidates: int = 50
    max_rewrite_depth: int = 3
    timeout_seconds: int = 30
    allowed_rule_families: list[str] = Field(default_factory=list)
    enable_llm: bool = True
    enable_rules: bool = True


class GenerateCandidatesRequest(BaseModel):
    request_id: str
    invocation_id: str
    template_fingerprint: str
    normalized_sql: str
    baseline_plan: dict[str, Any] = Field(default_factory=dict)
    schema_context: dict[str, Any] = Field(default_factory=dict)
    config: GenerationConfig


class ParameterMapping(BaseModel):
    original_positions: list[str]
    rewritten_positions: list[str]
    mapping: dict[str, str]


class StructuralValidation(BaseModel):
    passed: bool
    in_fragment: bool
    tables_referenced: list[str]
    output_columns_match: bool
    output_labels_match: bool


class CandidateResponse(BaseModel):
    sql_text: str
    canonical_hash: str
    source_type: str
    source_detail: str
    applied_rules: list[str]
    parameter_mapping: ParameterMapping
    structural_validation: StructuralValidation


class RejectedCandidate(BaseModel):
    sql_text: str
    source_type: str
    rejection_reason: str
    rejection_layer: int


class ModelRuntime(BaseModel):
    llm_enabled: bool
    name: str | None = None
    digest: str | None = None
    runtime_version: str | None = None
    prompt_template_version: str | None = None
    prompt_hash: str | None = None


class GenerationStats(BaseModel):
    rule_candidates_raw: int
    llm_candidates_raw: int
    after_dedup: int
    after_structural_validation: int
    returned: int
    rejected: int
    rule_generation_ms: float
    llm_generation_ms: float
    total_ms: float


class GenerateCandidatesResponse(BaseModel):
    request_id: str
    invocation_id: str
    model_runtime: ModelRuntime
    candidates: list[CandidateResponse]
    rejected: list[RejectedCandidate]
    stats: GenerationStats


class EquivalenceConfig(BaseModel):
    comparison_method: str = "auto"
    max_rows_full_compare: int = 100000
    float_epsilon: float = 1e-9
    timeout_ms: int = 60000


class EquivalenceRequest(BaseModel):
    request_id: str
    candidate_id: str
    original_sql: str
    candidate_sql: str
    parameter_sets: list[ParameterSet]
    config: EquivalenceConfig = Field(default_factory=EquivalenceConfig)


class EquivalenceCheckResponse(BaseModel):
    parameter_set_id: str
    passed: bool
    method_used: str
    original_row_count: int
    candidate_row_count: int
    rows_compared: int
    mismatch_detail: dict[str, Any] | None
    original_execution_time_ms: float
    candidate_execution_time_ms: float


class EquivalenceResponse(BaseModel):
    request_id: str
    candidate_id: str
    passed: bool
    method_used: str
    checks: list[EquivalenceCheckResponse]
    parameter_sets_checked: int
    mismatch_detail: dict[str, Any] | None
    checked_at: str


class ParseAnalyzeRequest(BaseModel):
    sql: str
    check_fragment: bool = True


class ParseAnalyzeResponse(BaseModel):
    parsed: bool
    in_supported_fragment: bool
    tables_referenced: list[str]
    columns_in_where: list[str]
    columns_in_join: list[str]
    columns_in_group_by: list[str]
    has_order_by: bool
    has_aggregation: bool
    has_subquery: bool
    has_cte: bool
    parameter_positions: list[str]
    fragment_violations: list[str]
