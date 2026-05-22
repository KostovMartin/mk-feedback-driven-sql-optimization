using System.Globalization;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.RegularExpressions;
using Npgsql;
using QueryOptimizer.WorkloadRunner.Bandits;

namespace QueryOptimizer.WorkloadRunner;

internal sealed partial class ControlledWorkloadRunner
{
    private static bool IsBaselineOnly(WorkloadItem workloadItem)
    {
        return string.Equals(
            workloadItem.ExpectedCandidateSourceDetail,
            BaselineOnlyCandidateSourceDetail,
            StringComparison.OrdinalIgnoreCase);
    }

    private static IReadOnlyList<CandidateDto> SelectCandidateIntake(
        GenerateCandidatesResponse generation,
        WorkloadItem workloadItem)
    {
        if (string.Equals(
                workloadItem.ExpectedCandidateSourceDetail,
                CandidatePoolSourceDetail,
                StringComparison.OrdinalIgnoreCase))
        {
            return generation.Candidates.ToArray();
        }

        if (string.Equals(
                workloadItem.ExpectedCandidateSourceDetail,
                FirstLlmCandidateSourceDetail,
                StringComparison.OrdinalIgnoreCase))
        {
            return generation.Candidates
                .Where(candidate => string.Equals(candidate.SourceType, "llm", StringComparison.OrdinalIgnoreCase))
                .ToArray();
        }

        if (string.Equals(
                workloadItem.ExpectedCandidateSourceDetail,
                FirstRuleCandidateSourceDetail,
                StringComparison.OrdinalIgnoreCase))
        {
            return generation.Candidates
                .Where(candidate => string.Equals(candidate.SourceType, "rule", StringComparison.OrdinalIgnoreCase))
                .ToArray();
        }

        return generation.Candidates
            .Where(candidate => string.Equals(
                candidate.SourceDetail,
                workloadItem.ExpectedCandidateSourceDetail,
                StringComparison.Ordinal))
            .ToArray();
    }

    private static object CreateGenerationDetails(GenerateCandidatesResponse generation)
    {
        return new
        {
            model_runtime = generation.ModelRuntime,
            stats = generation.Stats,
            returned = generation.Candidates.Select(candidate => candidate.SourceDetail).ToArray(),
            rejected = generation.Rejected.Select(candidate => new
            {
                candidate.SourceType,
                candidate.RejectionReason,
                candidate.RejectionLayer,
                sql_text = TruncateForLog(candidate.SqlText, maxLength: 500)
            }).ToArray()
        };
    }

    private static string DescribeGenerationFailure(GenerateCandidatesResponse generation)
    {
        var returned = generation.Candidates.Count > 0
            ? string.Join(", ", generation.Candidates.Select(item => item.SourceDetail))
            : "none";
        var rejected = generation.Rejected.Count > 0
            ? string.Join(
                "; ",
                generation.Rejected.Take(5).Select(item =>
                    $"{item.SourceType}:{item.RejectionReason}: {TruncateForLog(item.SqlText)}"))
            : "none";

        return string.Create(
            CultureInfo.InvariantCulture,
            $"returned [{returned}], rejected [{rejected}], stats(rule_raw={generation.Stats.RuleCandidatesRaw}, llm_raw={generation.Stats.LlmCandidatesRaw}, returned={generation.Stats.Returned}, rejected={generation.Stats.Rejected}, after_dedup={generation.Stats.AfterDedup}, after_structural_validation={generation.Stats.AfterStructuralValidation})");
    }

    private static string TruncateForLog(string value, int maxLength = 160)
    {
        var normalized = Regex.Replace(value, @"\s+", " ").Trim();
        if (string.IsNullOrWhiteSpace(normalized))
        {
            return "(empty)";
        }

        return normalized.Length <= maxLength
            ? normalized
            : normalized[..maxLength] + "...";
    }

    private async Task<IReadOnlyList<ParameterSet>> LoadParameterSetsAsync(
        string parameterFile,
        string role,
        CancellationToken cancellationToken)
    {
        var path = Path.Combine(_options.ParametersPath, parameterFile);
        await using var stream = File.OpenRead(path);
        var parameterSets = await JsonSerializer.DeserializeAsync<List<ParameterSet>>(stream, JsonOptions, cancellationToken);
        return parameterSets is { Count: > 0 }
            ? parameterSets
            : throw new InvalidOperationException($"No {role} parameter sets found in {path}.");
    }

    private static IReadOnlyList<ParameterSet> SelectValidationParameterSets(
        IReadOnlyList<ParameterSet> searchParameterSets,
        RunnerOptions options)
    {
        if (options.ValidationParameterSetLimit <= 0 ||
            searchParameterSets.Count <= options.ValidationParameterSetLimit)
        {
            return searchParameterSets;
        }

        return searchParameterSets.Take(options.ValidationParameterSetLimit).ToArray();
    }

    private async Task<GenerateCandidatesResponse> GenerateCandidatesAsync(
        Guid invocationId,
        string templateFingerprint,
        string normalizedSql,
        SchemaContextPayload schemaContext,
        CancellationToken cancellationToken)
    {
        return await PostJsonAsync<GenerateCandidatesResponse>(
            "generate-candidates",
            new
            {
                request_id = Guid.NewGuid(),
                invocation_id = invocationId,
                template_fingerprint = templateFingerprint,
                normalized_sql = normalizedSql,
                baseline_plan = new { },
                schema_context = schemaContext,
                config = new
                {
                    model = _options.DefaultModel,
                    model_provider = _options.ModelProvider,
                    temperature = _options.Temperature,
                    max_llm_candidates = _options.MaxLlmCandidates,
                    max_rule_candidates = 50,
                    max_rewrite_depth = 1,
                    timeout_seconds = _options.ModelTimeoutSeconds,
                    allowed_rule_families = _options.AllowedRuleFamilies,
                    enable_llm = _options.EnableLlm,
                    enable_rules = _options.EnableRules
                }
            },
            cancellationToken);
    }

    private async Task<EquivalenceResponse> ValidateEquivalenceAsync(
        Guid candidateId,
        string originalSql,
        string candidateSql,
        IReadOnlyList<ParameterSet> parameterSets,
        CancellationToken cancellationToken)
    {
        return await PostJsonAsync<EquivalenceResponse>(
            "validate-equivalence",
            new
            {
                request_id = Guid.NewGuid(),
                candidate_id = candidateId,
                original_sql = originalSql,
                candidate_sql = candidateSql,
                parameter_sets = parameterSets,
                config = new
                {
                    comparison_method = "full_comparison",
                    max_rows_full_compare = _options.EquivalenceMaxRowsFullCompare,
                    float_epsilon = 1e-9,
                    timeout_ms = _options.EquivalenceTimeoutMs
                }
            },
            cancellationToken);
    }

    private async Task<T> PostJsonAsync<T>(string relativeUrl, object request, CancellationToken cancellationToken)
    {
        using var response = await _httpClient.PostAsJsonAsync(relativeUrl, request, JsonOptions, cancellationToken);
        var body = await response.Content.ReadAsStringAsync(cancellationToken);
        if (!response.IsSuccessStatusCode)
        {
            throw new InvalidOperationException($"{relativeUrl} failed with {(int)response.StatusCode}: {body}");
        }

        return JsonSerializer.Deserialize<T>(body, JsonOptions)
            ?? throw new InvalidOperationException($"{relativeUrl} returned an empty or invalid response.");
    }

    private async Task<CandidatePreflightResult> PreflightCandidateSqlAsync(
        NpgsqlConnection connection,
        string baselineSql,
        string candidateSql,
        IReadOnlyList<ParameterSet> parameterSets,
        CancellationToken cancellationToken)
    {
        var checkedParameterSetIds = new List<string>();

        foreach (var parameterSet in parameterSets)
        {
            try
            {
                await PreflightSqlAsync(connection, baselineSql, parameterSet, cancellationToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                return CandidatePreflightResult.Failed(
                    "baseline",
                    parameterSet.ParameterSetId,
                    ex.Message,
                    ex.ToString(),
                    checkedParameterSetIds);
            }

            try
            {
                await PreflightSqlAsync(connection, candidateSql, parameterSet, cancellationToken);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                return CandidatePreflightResult.Failed(
                    "candidate",
                    parameterSet.ParameterSetId,
                    ex.Message,
                    ex.ToString(),
                    checkedParameterSetIds);
            }

            checkedParameterSetIds.Add(parameterSet.ParameterSetId);
        }

        return CandidatePreflightResult.Succeeded(checkedParameterSetIds);
    }

    private async Task PreflightSqlAsync(
        NpgsqlConnection connection,
        string sql,
        ParameterSet parameterSet,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        var explainTarget = ToNpgsqlSql(NormalizeSql(sql).TrimEnd(';'));
        command.CommandText = $"EXPLAIN {explainTarget}";
        command.CommandTimeout = Math.Max(1, (int)Math.Ceiling(_options.EquivalenceTimeoutMs / 1000.0));
        BindParameters(command, parameterSet);

        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            // Drain EXPLAIN output so provider errors surface before benchmarking starts.
        }
    }

    private async Task<BackgroundOptimizerResult> RunBackgroundOptimizerAsync(
        NpgsqlConnection metadataConnection,
        NpgsqlConnection targetConnection,
        string templateFingerprint,
        string baselineSql,
        IReadOnlyList<ParameterSet> searchParameterSets,
        WorkloadItem workloadItem,
        CancellationToken cancellationToken)
    {
        var options = CreateBanditOptions(_options);
        var policy = new BanditPolicy(new Random(_options.BanditRandomSeed));
        var rounds = Math.Max(1, _options.BackgroundOptimizerRounds);
        var benchmarkParameterSets = searchParameterSets
            .Take(Math.Max(1, _options.BackgroundOptimizerParameterLimit))
            .ToArray();

        if (benchmarkParameterSets.Length == 0)
        {
            throw new InvalidOperationException("Background optimizer requires at least one search parameter set.");
        }

        Guid? selectedCandidateId = null;
        string? selectedCandidateSql = null;
        string? selectedCandidateSourceDetail = null;
        BanditSelection? lastSelection = null;
        IReadOnlyList<BenchmarkPair> latestPairs = [];
        IReadOnlyList<BenchmarkPair> evaluationPairs = [];
        PromotionEvaluation? promotionEvaluation = null;
        var roundsExecuted = 0;
        var candidatePoolSize = 0;

        for (var round = 0; round < rounds; round++)
        {
            await EnsureBanditStateRowsAsync(metadataConnection, templateFingerprint, _options.BanditStrategy, cancellationToken);
            var arms = await LoadBanditArmsAsync(metadataConnection, templateFingerprint, cancellationToken);
            candidatePoolSize = arms.Count;
            if (arms.Count == 0)
            {
                throw new InvalidOperationException($"Template {templateFingerprint} has no validated candidates to benchmark.");
            }

            var selection = policy.SelectCandidate(arms, options);
            var selectedArm = arms.Single(arm =>
                string.Equals(arm.CandidateId.ToString(), selection.CandidateId.ToString(), StringComparison.Ordinal));
            var selectedGuid = Guid.Parse(selection.CandidateId.ToString());

            Console.WriteLine(
                FormattableString.Invariant(
                    $"Background optimizer selected candidate {selectedGuid} using {_options.BanditStrategy} ({selection.Reason}, score={FormatNullable(selection.Score)})."));

            latestPairs = await RunPairedBenchmarksAsync(
                metadataConnection,
                targetConnection,
                templateFingerprint,
                selectedGuid,
                selectedArm.SourceDetail,
                baselineSql,
                selectedArm.SqlText,
                benchmarkParameterSets,
                benchmarkPhase: "search",
                workloadItem,
                parameterFile: workloadItem.ParameterFile,
                cancellationToken);

            await UpdateBanditStateAsync(metadataConnection, selectedArm, latestPairs, options, cancellationToken);
            evaluationPairs = await LoadSearchBenchmarkPairsAsync(
                metadataConnection,
                templateFingerprint,
                selectedGuid,
                cancellationToken);
            promotionEvaluation = PromotionEvaluator.Evaluate(evaluationPairs, _options);
            selectedCandidateId = selectedGuid;
            selectedCandidateSql = selectedArm.SqlText;
            selectedCandidateSourceDetail = selectedArm.SourceDetail;
            lastSelection = selection;
            roundsExecuted++;

            if (promotionEvaluation.Promoted)
            {
                break;
            }
        }

        if (!selectedCandidateId.HasValue ||
            selectedCandidateSql is null ||
            selectedCandidateSourceDetail is null ||
            lastSelection is null ||
            promotionEvaluation is null)
        {
            throw new InvalidOperationException("Background optimizer did not execute a benchmark round.");
        }

        return new BackgroundOptimizerResult(
            selectedCandidateId.Value,
            selectedCandidateSql,
            selectedCandidateSourceDetail,
            latestPairs,
            evaluationPairs,
            promotionEvaluation,
            new BackgroundOptimizerSummary(
                _options.BanditStrategy,
                rounds,
                roundsExecuted,
                selectedCandidateId.Value,
                lastSelection.Reason.ToString(),
                lastSelection.Score,
                candidatePoolSize,
                evaluationPairs.Count));
    }

    private static BanditOptions CreateBanditOptions(RunnerOptions options)
    {
        var strategy = options.BanditStrategy == "ucb1"
            ? BanditStrategy.Ucb1
            : BanditStrategy.Thompson;

        return new BanditOptions(
            strategy,
            options.BanditObservationVariance,
            options.UcbExplorationCoefficient);
    }
}
