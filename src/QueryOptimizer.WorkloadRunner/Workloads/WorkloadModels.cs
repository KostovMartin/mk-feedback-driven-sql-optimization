using System.Text.Json;
using System.Text.Json.Serialization;

namespace QueryOptimizer.WorkloadRunner;

internal sealed record WorkloadItem(
    [property: JsonPropertyName("query_file")] string QueryFile,
    [property: JsonPropertyName("parameter_file")] string ParameterFile,
    [property: JsonPropertyName("held_out_parameter_file")] string? HeldOutParameterFile,
    [property: JsonPropertyName("expected_candidate_source_detail")] string ExpectedCandidateSourceDetail,
    [property: JsonPropertyName("workload_label")] string WorkloadLabel,
    [property: JsonPropertyName("workload_description")] string WorkloadDescription)
{
    public void Validate(string manifestPath)
    {
        Required(QueryFile, nameof(QueryFile), manifestPath);
        Required(ParameterFile, nameof(ParameterFile), manifestPath);
        Required(ExpectedCandidateSourceDetail, nameof(ExpectedCandidateSourceDetail), manifestPath);
        Required(WorkloadLabel, nameof(WorkloadLabel), manifestPath);
        Required(WorkloadDescription, nameof(WorkloadDescription), manifestPath);
    }

    private static void Required(string? value, string name, string manifestPath)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidOperationException($"Workload manifest {manifestPath} contains an entry with missing {name}.");
        }
    }
}

internal sealed record ParameterSet(
    [property: JsonPropertyName("parameter_set_id")] string ParameterSetId,
    [property: JsonPropertyName("parameters")] IReadOnlyList<ParameterValue> Parameters);

internal sealed record ParameterValue(
    [property: JsonPropertyName("position")] string Position,
    [property: JsonPropertyName("type")] string Type,
    [property: JsonPropertyName("value")] JsonElement Value);
