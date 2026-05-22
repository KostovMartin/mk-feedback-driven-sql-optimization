from __future__ import annotations

import time
from dataclasses import dataclass

from app.api.models import (
    CandidateResponse,
    GenerateCandidatesRequest,
    ModelRuntime,
    RejectedCandidate,
)
from app.llm.client import ModelClient, ModelResponse, OllamaModelClient
from app.llm.prompt_builder import build_single_candidate_prompt
from app.llm.response_parser import parse_model_response
from app.parser.sql_parser import canonical_hash
from app.rules.engine import build_candidate_response


@dataclass(frozen=True)
class LlmGenerationResult:
    candidates: list[CandidateResponse]
    rejected: list[RejectedCandidate]
    runtime: ModelRuntime
    raw_candidates: int
    elapsed_ms: float


def apply_llm_candidates(
    request: GenerateCandidatesRequest,
    *,
    ollama_url: str,
    client: ModelClient | None = None,
) -> LlmGenerationResult:
    started = time.perf_counter()
    provider = request.config.model_provider.lower()
    model_name = request.config.model_name.strip()

    if not model_name:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return LlmGenerationResult(
            candidates=[],
            rejected=[
                RejectedCandidate(
                    sql_text="",
                    source_type="llm",
                    rejection_reason="model_not_configured",
                    rejection_layer=0,
                )
            ],
            runtime=ModelRuntime(llm_enabled=True),
            raw_candidates=0,
            elapsed_ms=elapsed_ms,
        )

    if provider != "ollama":
        return _unsupported_provider_result(provider, model_name, started)

    model_client = client or OllamaModelClient(ollama_url)
    prompt = build_single_candidate_prompt(
        normalized_sql=request.normalized_sql,
        schema_context=request.schema_context,
        baseline_plan=request.baseline_plan,
    )

    try:
        response = model_client.generate(
            model=model_name,
            system_prompt=prompt.system_prompt,
            user_prompt=prompt.user_prompt,
            temperature=request.config.temperature,
            timeout_seconds=request.config.timeout_seconds,
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return LlmGenerationResult(
            candidates=[],
            rejected=[
                RejectedCandidate(
                    sql_text="",
                    source_type="llm",
                    rejection_reason=f"llm_unavailable:{exc.__class__.__name__}",
                    rejection_layer=0,
                )
            ],
            runtime=ModelRuntime(
                llm_enabled=True,
                name=model_name,
                prompt_template_version=prompt.prompt_version,
                prompt_hash=prompt.prompt_hash,
            ),
            raw_candidates=0,
            elapsed_ms=elapsed_ms,
        )

    parsed_response = parse_model_response(response.text, request.config.max_llm_candidates)
    sql_candidates = parsed_response.candidates
    candidates: list[CandidateResponse] = []
    rejected = [
        _rejected(fragment.text, fragment.reason, 1)
        for fragment in parsed_response.rejected
    ]
    original_hash = canonical_hash(request.normalized_sql)

    for index, candidate_sql in enumerate(sql_candidates, start=1):
        if canonical_hash(candidate_sql) == original_hash:
            rejected.append(_rejected(candidate_sql, "candidate_same_as_original", 1))
            continue
        try:
            candidate = build_candidate_response(
                request.normalized_sql,
                candidate_sql,
                source_type="llm",
                source_detail=f"llm:ollama:{response.model_name}:candidate-{index}",
                applied_rules=[],
            )
        except Exception:
            rejected.append(_rejected(candidate_sql, "parse_or_structural_validation_failed", 1))
            continue
        if not candidate.structural_validation.passed:
            rejected.append(_rejected(candidate_sql, "structural_validation_failed", 1))
            continue
        candidates.append(candidate)

    elapsed_ms = (time.perf_counter() - started) * 1000
    return LlmGenerationResult(
        candidates=candidates,
        rejected=rejected,
        runtime=_runtime_from_response(response, prompt.prompt_version, prompt.prompt_hash),
        raw_candidates=len(sql_candidates) + len(parsed_response.rejected),
        elapsed_ms=elapsed_ms,
    )


def _runtime_from_response(
    response: ModelResponse,
    prompt_version: str,
    prompt_hash: str,
) -> ModelRuntime:
    return ModelRuntime(
        llm_enabled=True,
        name=response.model_name,
        digest=response.model_digest,
        runtime_version=response.runtime_version,
        prompt_template_version=prompt_version,
        prompt_hash=prompt_hash,
    )


def _unsupported_provider_result(
    provider: str,
    model_name: str,
    started: float,
) -> LlmGenerationResult:
    elapsed_ms = (time.perf_counter() - started) * 1000
    return LlmGenerationResult(
        candidates=[],
        rejected=[
            RejectedCandidate(
                sql_text="",
                source_type="llm",
                rejection_reason=f"unsupported_model_provider:{provider}",
                rejection_layer=0,
            )
        ],
        runtime=ModelRuntime(llm_enabled=True, name=model_name),
        raw_candidates=0,
        elapsed_ms=elapsed_ms,
    )


def _rejected(sql_text: str, reason: str, layer: int) -> RejectedCandidate:
    return RejectedCandidate(
        sql_text=sql_text,
        source_type="llm",
        rejection_reason=reason,
        rejection_layer=layer,
    )
