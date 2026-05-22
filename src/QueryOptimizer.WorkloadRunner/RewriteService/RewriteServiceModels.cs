using System.Text.Json;
using System.Text.Json.Serialization;

namespace QueryOptimizer.WorkloadRunner;

internal sealed class ParseAnalyzeResponse
{
    [JsonPropertyName("parsed")]
    public bool Parsed { get; init; }

    [JsonPropertyName("in_supported_fragment")]
    public bool InSupportedFragment { get; init; }

    [JsonPropertyName("tables_referenced")]
    public List<string> TablesReferenced { get; init; } = [];

    [JsonPropertyName("fragment_violations")]
    public List<string> FragmentViolations { get; init; } = [];
}

internal sealed class GenerateCandidatesResponse
{
    [JsonPropertyName("model_runtime")]
    public ModelRuntimeDto ModelRuntime { get; init; } = new();

    [JsonPropertyName("candidates")]
    public List<CandidateDto> Candidates { get; init; } = [];

    [JsonPropertyName("rejected")]
    public List<RejectedCandidateDto> Rejected { get; init; } = [];

    [JsonPropertyName("stats")]
    public GenerationStats Stats { get; init; } = new();
}

internal sealed class ModelRuntimeDto
{
    [JsonPropertyName("llm_enabled")]
    public bool LlmEnabled { get; init; }

    [JsonPropertyName("name")]
    public string? Name { get; init; }

    [JsonPropertyName("digest")]
    public string? Digest { get; init; }

    [JsonPropertyName("runtime_version")]
    public string? RuntimeVersion { get; init; }

    [JsonPropertyName("prompt_template_version")]
    public string? PromptTemplateVersion { get; init; }

    [JsonPropertyName("prompt_hash")]
    public string? PromptHash { get; init; }
}

internal sealed class CandidateDto
{
    [JsonPropertyName("sql_text")]
    public required string SqlText { get; init; }

    [JsonPropertyName("canonical_hash")]
    public required string CanonicalHash { get; init; }

    [JsonPropertyName("source_type")]
    public required string SourceType { get; init; }

    [JsonPropertyName("source_detail")]
    public required string SourceDetail { get; init; }

    [JsonPropertyName("applied_rules")]
    public List<string> AppliedRules { get; init; } = [];

    [JsonPropertyName("parameter_mapping")]
    public JsonElement ParameterMapping { get; init; }

    [JsonPropertyName("structural_validation")]
    public JsonElement StructuralValidation { get; init; }
}

internal sealed class RejectedCandidateDto
{
    [JsonPropertyName("sql_text")]
    public string SqlText { get; init; } = string.Empty;

    [JsonPropertyName("source_type")]
    public string SourceType { get; init; } = string.Empty;

    [JsonPropertyName("rejection_reason")]
    public string RejectionReason { get; init; } = string.Empty;

    [JsonPropertyName("rejection_layer")]
    public int RejectionLayer { get; init; }
}

internal sealed class GenerationStats
{
    [JsonPropertyName("rule_candidates_raw")]
    public int RuleCandidatesRaw { get; init; }

    [JsonPropertyName("llm_candidates_raw")]
    public int LlmCandidatesRaw { get; init; }

    [JsonPropertyName("after_dedup")]
    public int AfterDedup { get; init; }

    [JsonPropertyName("after_structural_validation")]
    public int AfterStructuralValidation { get; init; }

    [JsonPropertyName("returned")]
    public int Returned { get; init; }

    [JsonPropertyName("rejected")]
    public int Rejected { get; init; }

    [JsonPropertyName("rule_generation_ms")]
    public double RuleGenerationMs { get; init; }

    [JsonPropertyName("llm_generation_ms")]
    public double LlmGenerationMs { get; init; }
}

internal sealed class EquivalenceResponse
{
    [JsonPropertyName("passed")]
    public bool Passed { get; init; }

    [JsonPropertyName("method_used")]
    public string MethodUsed { get; init; } = "full_comparison";

    [JsonPropertyName("checks")]
    public List<EquivalenceCheckDto> Checks { get; init; } = [];

    [JsonPropertyName("mismatch_detail")]
    public JsonElement MismatchDetail { get; init; }
}

internal sealed class EquivalenceCheckDto
{
    [JsonPropertyName("parameter_set_id")]
    public string ParameterSetId { get; init; } = string.Empty;

    [JsonPropertyName("original_row_count")]
    public int OriginalRowCount { get; init; }

    [JsonPropertyName("candidate_row_count")]
    public int CandidateRowCount { get; init; }

    [JsonPropertyName("rows_compared")]
    public int RowsCompared { get; init; }

    [JsonPropertyName("original_execution_time_ms")]
    public double OriginalExecutionTimeMs { get; init; }

    [JsonPropertyName("candidate_execution_time_ms")]
    public double CandidateExecutionTimeMs { get; init; }
}
