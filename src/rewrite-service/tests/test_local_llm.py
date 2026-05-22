from __future__ import annotations

from typing import Any

from app.api.models import GenerateCandidatesRequest, GenerationConfig
from app.llm.client import ModelResponse
from app.llm.generator import apply_llm_candidates
from app.llm.prompt_builder import build_single_candidate_prompt
from app.llm.response_parser import extract_sql_candidates, parse_model_response


class _FakeModelClient:
    MODEL_NAME = "qwen3.6:35b-a3b-q4_K_M"

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text

    def generate(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        timeout_seconds: int,
    ) -> ModelResponse:
        assert model == self.MODEL_NAME
        assert "ORIGINAL QUERY" in user_prompt
        assert "REWRITE OPPORTUNITY HINTS" in user_prompt
        assert system_prompt
        assert temperature == 0.5
        assert timeout_seconds == 30
        return ModelResponse(
            text=self.response_text,
            model_name=model,
            model_digest="sha256:test",
            runtime_version="ollama test",
        )

    def list_models(self, *, timeout_seconds: int = 15) -> list[dict[str, Any]]:
        return [{"name": self.MODEL_NAME, "digest": "sha256:test"}]


def _request(sql: str, *, max_llm_candidates: int = 3) -> GenerateCandidatesRequest:
    return GenerateCandidatesRequest(
        request_id="req",
        invocation_id="inv",
        template_fingerprint="tpl",
        normalized_sql=sql,
        config=GenerationConfig(
            model=_FakeModelClient.MODEL_NAME,
            model_provider="ollama",
            temperature=0.5,
            max_llm_candidates=max_llm_candidates,
            enable_llm=True,
            enable_rules=False,
        ),
    )


def test_extract_sql_candidates_strips_markdown_and_explanations() -> None:
    response = """Here is the rewrite:

```sql
SELECT l_orderkey
FROM lineitem;
```
"""

    assert extract_sql_candidates(response, max_candidates=1) == [
        "SELECT l_orderkey\nFROM lineitem;"
    ]


def test_prompt_includes_count_exists_hint_for_small_model_smoke_query() -> None:
    prompt = build_single_candidate_prompt(
        normalized_sql=(
            "SELECT l_orderkey FROM lineitem WHERE ("
            "SELECT COUNT(*) FROM partsupp WHERE ps_supplycost > $1 AND ps_partkey = l_partkey"
            ") > 0;"
        ),
        schema_context={},
        baseline_plan={},
    )

    assert "COUNT(*) > 0 existence tests" in prompt.user_prompt
    assert "WHERE EXISTS" in prompt.user_prompt
    assert "performance context, not semantic proof" in prompt.system_prompt


def test_prompt_includes_schema_columns_indexes_and_row_estimates() -> None:
    prompt = build_single_candidate_prompt(
        normalized_sql="SELECT l_orderkey FROM lineitem WHERE l_partkey = $1;",
        schema_context={
            "snapshotHash": "sha256:test",
            "tables": [
                {
                    "schema": "public",
                    "name": "lineitem",
                    "rowEstimate": 60000,
                    "columns": [
                        {
                            "name": "l_orderkey",
                            "type": "integer",
                            "nullable": False,
                            "ordinalPosition": 1,
                        },
                        {
                            "name": "l_partkey",
                            "type": "integer",
                            "nullable": False,
                            "ordinalPosition": 2,
                        },
                    ],
                    "indexes": [
                        {
                            "name": "idx_smoke_lineitem_partkey",
                            "accessMethod": "btree",
                            "unique": False,
                            "primary": False,
                            "columns": ["l_partkey"],
                            "definition": (
                                "CREATE INDEX idx_smoke_lineitem_partkey "
                                "ON public.lineitem USING btree (l_partkey)"
                            ),
                        }
                    ],
                    "constraints": [],
                }
            ],
        },
        baseline_plan={},
    )

    assert "idx_smoke_lineitem_partkey" in prompt.user_prompt
    assert '"rowEstimate": 60000' in prompt.user_prompt
    assert '"nullable": false' in prompt.user_prompt


def test_response_parser_does_not_treat_prose_with_as_cte_sql() -> None:
    parsed = parse_model_response(
        "with supplycost greater than $1, this query already has a good plan.",
        max_candidates=1,
    )

    assert not parsed.candidates
    assert parsed.rejected[0].reason == "non_sql_output"


def test_llm_candidate_passes_structural_validation_with_local_model_provenance() -> None:
    original = (
        "SELECT l_orderkey FROM lineitem WHERE ("
        "SELECT COUNT(*) FROM partsupp WHERE ps_supplycost > $1 AND ps_partkey = l_partkey"
        ") > 0;"
    )
    candidate = (
        "SELECT l_orderkey FROM lineitem WHERE EXISTS ("
        "SELECT 1 FROM partsupp WHERE ps_supplycost > $1 AND ps_partkey = l_partkey"
        ");"
    )

    result = apply_llm_candidates(
        _request(original),
        ollama_url="http://ollama:11434",
        client=_FakeModelClient(candidate),
    )

    assert len(result.candidates) == 1
    assert not result.rejected
    assert result.candidates[0].source_type == "llm"
    assert result.candidates[0].source_detail == (
        "llm:ollama:qwen3.6:35b-a3b-q4_K_M:candidate-1"
    )
    assert result.candidates[0].structural_validation.passed
    assert result.runtime.digest == "sha256:test"
    assert result.runtime.runtime_version == "ollama test"
    assert result.runtime.prompt_template_version
    assert result.runtime.prompt_hash


def test_llm_candidate_rejects_parameter_change_and_unsafe_output() -> None:
    original = "SELECT l_orderkey FROM lineitem WHERE l_partkey > $1;"
    response = """SELECT l_orderkey FROM lineitem WHERE l_partkey > $2;
---
DELETE FROM lineitem;
"""

    result = apply_llm_candidates(
        _request(original),
        ollama_url="http://ollama:11434",
        client=_FakeModelClient(response),
    )

    assert not result.candidates
    assert [item.rejection_reason for item in result.rejected] == [
        "unsafe_non_select_output",
        "structural_validation_failed",
    ]


def test_llm_no_optimization_response_yields_no_candidates() -> None:
    result = apply_llm_candidates(
        _request("SELECT l_orderkey FROM lineitem;"),
        ollama_url="http://ollama:11434",
        client=_FakeModelClient("NO_OPTIMIZATION"),
    )

    assert not result.candidates
    assert not result.rejected
    assert result.raw_candidates == 0
