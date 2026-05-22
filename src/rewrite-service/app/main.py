from __future__ import annotations

import time

import psycopg
from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.api.models import (
    CandidateResponse,
    EquivalenceRequest,
    EquivalenceResponse,
    GenerateCandidatesRequest,
    GenerateCandidatesResponse,
    GenerationStats,
    ModelRuntime,
    ParseAnalyzeRequest,
    ParseAnalyzeResponse,
    RejectedCandidate,
)
from app.config import settings
from app.llm.client import OllamaModelClient
from app.llm.generator import apply_llm_candidates
from app.parser.sql_parser import parse_and_analyze
from app.rules.engine import apply_rule_candidates
from app.validation.equivalence import validate_equivalence

app = FastAPI(title="SQL Rewrite Service", version="0.1.0")

REQUESTS = Counter("rewrite_service_requests_total", "Rewrite service requests", ["endpoint"])
LATENCY = Histogram(
    "rewrite_service_request_seconds",
    "Rewrite service request latency",
    ["endpoint"],
)


@app.post("/parse-and-analyze", response_model=ParseAnalyzeResponse)
def parse_and_analyze_endpoint(request: ParseAnalyzeRequest) -> ParseAnalyzeResponse:
    REQUESTS.labels("parse-and-analyze").inc()
    with LATENCY.labels("parse-and-analyze").time():
        return parse_and_analyze(request.sql, check_fragment=request.check_fragment)


@app.post("/generate-candidates", response_model=GenerateCandidatesResponse)
def generate_candidates(request: GenerateCandidatesRequest) -> GenerateCandidatesResponse:
    REQUESTS.labels("generate-candidates").inc()
    started = time.perf_counter()
    rule_candidates: list[CandidateResponse] = []
    llm_candidates: list[CandidateResponse] = []
    rejected: list[RejectedCandidate] = []
    model_runtime = ModelRuntime(llm_enabled=False)
    rule_generation_ms = 0.0
    llm_generation_ms = 0.0
    llm_candidates_raw = 0

    with LATENCY.labels("generate-candidates").time():
        if request.config.enable_rules and settings.enable_rules:
            rule_candidates, rule_generation_ms = apply_rule_candidates(
                request.normalized_sql,
                request.config.max_rule_candidates,
                request.config.allowed_rule_families,
                request.schema_context,
            )
        if request.config.enable_llm and settings.enable_llm:
            llm_result = apply_llm_candidates(request, ollama_url=settings.ollama_url)
            llm_candidates = llm_result.candidates
            rejected.extend(llm_result.rejected)
            model_runtime = llm_result.runtime
            llm_generation_ms = llm_result.elapsed_ms
            llm_candidates_raw = llm_result.raw_candidates

    candidates = []
    seen_hashes: set[str] = set()
    for candidate in [*rule_candidates, *llm_candidates]:
        if candidate.canonical_hash in seen_hashes:
            rejected.append(
                RejectedCandidate(
                    sql_text=candidate.sql_text,
                    source_type=candidate.source_type,
                    rejection_reason="duplicate_candidate",
                    rejection_layer=1,
                )
            )
            continue
        seen_hashes.add(candidate.canonical_hash)
        candidates.append(candidate)

    total_ms = (time.perf_counter() - started) * 1000
    return GenerateCandidatesResponse(
        request_id=request.request_id,
        invocation_id=request.invocation_id,
        model_runtime=model_runtime,
        candidates=candidates,
        rejected=rejected,
        stats=GenerationStats(
            rule_candidates_raw=len(rule_candidates),
            llm_candidates_raw=llm_candidates_raw,
            after_dedup=len(candidates),
            after_structural_validation=len(candidates),
            returned=len(candidates),
            rejected=len(rejected),
            rule_generation_ms=rule_generation_ms,
            llm_generation_ms=llm_generation_ms,
            total_ms=total_ms,
        ),
    )


@app.post("/validate-equivalence", response_model=EquivalenceResponse)
def validate_equivalence_endpoint(request: EquivalenceRequest) -> EquivalenceResponse:
    REQUESTS.labels("validate-equivalence").inc()
    with LATENCY.labels("validate-equivalence").time():
        return validate_equivalence(settings.target_db_connection, request)


@app.get("/health")
def health() -> dict[str, object]:
    target_connected = False
    models_loaded: list[dict[str, object]] = []
    ollama_connected = False
    try:
        with (
            psycopg.connect(settings.target_db_connection, connect_timeout=2) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute("SELECT 1")
            target_connected = cursor.fetchone() == (1,)
    except Exception:
        target_connected = False

    if settings.enable_llm and settings.model_provider == "ollama":
        try:
            models_loaded = OllamaModelClient(settings.ollama_url).list_models(
                timeout_seconds=2
            )
            ollama_connected = True
        except Exception:
            ollama_connected = False

    return {
        "status": "healthy" if target_connected else "degraded",
        "ollama_connected": ollama_connected,
        "target_db_connected": target_connected,
        "models_loaded": models_loaded,
    }


@app.get("/models")
def models() -> dict[str, list[dict[str, object]]]:
    if not settings.enable_llm or settings.model_provider != "ollama":
        return {"models": []}
    return {"models": OllamaModelClient(settings.ollama_url).list_models()}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
