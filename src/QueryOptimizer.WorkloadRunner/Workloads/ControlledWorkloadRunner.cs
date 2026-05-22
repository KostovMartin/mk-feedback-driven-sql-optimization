using System.Text.Json;
using System.Text.RegularExpressions;
using Npgsql;

namespace QueryOptimizer.WorkloadRunner;

internal sealed partial class ControlledWorkloadRunner
{
    private const string BaselineOnlyCandidateSourceDetail = "baseline-only";
    private const string CandidatePoolSourceDetail = "candidate-pool";
    private const string FirstLlmCandidateSourceDetail = "first-llm-candidate";
    private const string FirstRuleCandidateSourceDetail = "first-rule-candidate";

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        PropertyNameCaseInsensitive = true
    };

    private static readonly Regex ParameterRegex = new(@"\$(\d+)", RegexOptions.Compiled);

    private readonly RunnerOptions _options;
    private readonly HttpClient _httpClient;

    public ControlledWorkloadRunner(RunnerOptions options)
    {
        _options = options;
        _httpClient = new HttpClient
        {
            BaseAddress = new Uri(options.RewriteServiceUrl.TrimEnd('/') + "/", UriKind.Absolute),
            Timeout = TimeSpan.FromSeconds(options.RewriteServiceHttpTimeoutSeconds)
        };
    }

    public async Task RunAsync(CancellationToken cancellationToken)
    {
        var workloadItems = await LoadWorkloadItemsAsync(cancellationToken);

        await using var metadataConnection = await OpenWithRetryAsync(_options.MetadataDbConnection, cancellationToken);
        await using var targetConnection = await OpenWithRetryAsync(_options.TargetDbConnection, cancellationToken);

        var completed = 0;
        var failed = 0;

        foreach (var workloadItem in workloadItems)
        {
            var result = await RunTemplateAsync(metadataConnection, targetConnection, workloadItem, cancellationToken);
            await InsertWorkloadCaseResultAsync(metadataConnection, workloadItem, result, cancellationToken);

            if (result.Status == "completed")
            {
                completed++;
                continue;
            }

            failed++;
            Console.Error.WriteLine(
                $"Controlled template {workloadItem.WorkloadLabel} recorded {result.Outcome} at {result.FailureStage}: {result.FailureReason}.");
        }

        Console.WriteLine(
            $"Controlled workload run completed: {completed} completed, {failed} failed, {workloadItems.Count} total.");
    }

    private async Task<WorkloadCaseResult> RunTemplateAsync(
        NpgsqlConnection metadataConnection,
        NpgsqlConnection targetConnection,
        WorkloadItem workloadItem,
        CancellationToken cancellationToken)
    {
        var startedAt = DateTimeOffset.UtcNow;
        var stage = "load_query";
        string? templateFingerprint = null;
        Guid? invocationId = null;
        Guid? candidateId = null;
        GenerateCandidatesResponse? generation = null;
        var searchParameterSetCount = 0;
        var heldOutParameterSetCount = 0;

        try
        {
            var normalizedSql = await File.ReadAllTextAsync(Path.Combine(_options.QueriesPath, workloadItem.QueryFile), cancellationToken);
            var searchParameterSets = await LoadParameterSetsAsync(workloadItem.ParameterFile, "search", cancellationToken);
            searchParameterSetCount = searchParameterSets.Count;
            templateFingerprint = ComputeSha256(NormalizeSql(normalizedSql));

            Console.WriteLine($"Starting controlled template {workloadItem.WorkloadLabel} ({workloadItem.QueryFile}).");

            stage = "parse";
            var parse = await PostJsonAsync<ParseAnalyzeResponse>(
                "parse-and-analyze",
                new { sql = normalizedSql, check_fragment = true },
                cancellationToken);

            if (!parse.Parsed || !parse.InSupportedFragment)
            {
                return CreateCaseResult(
                    status: "failed",
                    outcome: "parse_rejected",
                    failureStage: stage,
                    failureReason: "controlled_workload_query_rejected",
                    failureDetail: string.Join(", ", parse.FragmentViolations),
                    templateFingerprint,
                    invocationId,
                    candidateId,
                    searchParameterSetCount,
                    heldOutParameterSetCount,
                    generation,
                    equivalencePassed: null,
                    benchmarkPairs: [],
                    heldOutPairs: [],
                    baselineMedianMs: null,
                    candidateMedianMs: null,
                    improvementPct: null,
                    startedAt,
                    details: new { parse.Parsed, parse.InSupportedFragment, parse.FragmentViolations });
            }

            await UpsertTemplateAsync(metadataConnection, templateFingerprint, normalizedSql, parse.TablesReferenced, cancellationToken);

            stage = "schema_context";
            var schemaContext = await LoadSchemaContextAsync(targetConnection, parse.TablesReferenced, cancellationToken);

            invocationId = Guid.NewGuid();
            await InsertInvocationAsync(
                metadataConnection,
                invocationId.Value,
                templateFingerprint,
                schemaContext.SnapshotHash,
                workloadItem,
                cancellationToken);

            if (IsBaselineOnly(workloadItem))
            {
                stage = "baseline_calibration";
                var baselineMeasurements = await RunBaselineCalibrationAsync(
                    metadataConnection,
                    targetConnection,
                    templateFingerprint,
                    normalizedSql,
                    searchParameterSets,
                    workloadItem,
                    workloadItem.ParameterFile,
                    cancellationToken);

                await CompleteInvocationAsync(metadataConnection, invocationId.Value, new GenerateCandidatesResponse(), cancellationToken);
                PrintBaselineCalibrationSummary(baselineMeasurements, workloadItem.WorkloadLabel);
                Console.WriteLine("Baseline-only template completed; candidate generation, equivalence validation, and promotion were skipped.");

                return CreateCaseResult(
                    status: "completed",
                    outcome: "baseline_calibration_completed",
                    failureStage: null,
                    failureReason: null,
                    failureDetail: null,
                    templateFingerprint,
                    invocationId,
                    candidateId,
                    searchParameterSetCount,
                    heldOutParameterSetCount,
                    generation,
                    equivalencePassed: null,
                    benchmarkPairs: [],
                    heldOutPairs: [],
                    baselineMedianMs: Median(baselineMeasurements.Select(item => item.Measurement.MedianExecutionTime).ToArray()),
                    candidateMedianMs: null,
                    improvementPct: null,
                    startedAt,
                    details: new { baseline_measurements = baselineMeasurements.Count });
            }

            stage = "candidate_generation";
            generation = await GenerateCandidatesAsync(
                invocationId.Value,
                templateFingerprint,
                normalizedSql,
                schemaContext,
                cancellationToken);
            await CompleteInvocationAsync(metadataConnection, invocationId.Value, generation, cancellationToken);

            stage = "candidate_selection";
            var candidateIntake = SelectCandidateIntake(generation, workloadItem);
            if (candidateIntake.Count == 0)
            {
                return CreateCaseResult(
                    status: "completed",
                    outcome: "no_candidate",
                    failureStage: null,
                    failureReason: null,
                    failureDetail: null,
                    templateFingerprint,
                    invocationId,
                    candidateId,
                    searchParameterSetCount,
                    heldOutParameterSetCount,
                    generation,
                    equivalencePassed: null,
                    benchmarkPairs: [],
                    heldOutPairs: [],
                    baselineMedianMs: null,
                    candidateMedianMs: null,
                    improvementPct: null,
                    startedAt,
                    details: CreateGenerationDetails(generation));
            }

            var validationParameterSets = SelectValidationParameterSets(searchParameterSets, _options);
            var validatedCandidates = new List<ValidatedCandidate>();
            var equivalenceFailures = 0;
            var preflightFailures = 0;

            foreach (var intakeCandidate in candidateIntake)
            {
                var intakeCandidateId = await UpsertCandidateAsync(
                    metadataConnection,
                    templateFingerprint,
                    invocationId.Value,
                    intakeCandidate,
                    cancellationToken);
                candidateId ??= intakeCandidateId;

                stage = "equivalence_validation";
                var equivalence = await ValidateEquivalenceAsync(
                    intakeCandidateId,
                    normalizedSql,
                    intakeCandidate.SqlText,
                    validationParameterSets,
                    cancellationToken);
                await PersistEquivalenceAsync(metadataConnection, intakeCandidateId, equivalence, cancellationToken);

                if (!equivalence.Passed)
                {
                    equivalenceFailures++;
                    await MarkCandidateSemanticStatusAsync(metadataConnection, intakeCandidateId, validated: false, cancellationToken);
                    continue;
                }

                stage = "candidate_preflight";
                var preflight = await PreflightCandidateSqlAsync(
                    targetConnection,
                    normalizedSql,
                    intakeCandidate.SqlText,
                    validationParameterSets,
                    cancellationToken);

                if (!preflight.Passed)
                {
                    preflightFailures++;
                    await MarkCandidateSemanticStatusAsync(metadataConnection, intakeCandidateId, validated: false, cancellationToken);
                    continue;
                }

                await MarkCandidateSemanticStatusAsync(metadataConnection, intakeCandidateId, validated: true, cancellationToken);
                validatedCandidates.Add(new ValidatedCandidate(
                    intakeCandidateId,
                    intakeCandidate.SqlText,
                    intakeCandidate.SourceDetail));
            }

            if (validatedCandidates.Count == 0)
            {
                var noValidatedOutcome = preflightFailures > 0 && equivalenceFailures == 0
                    ? "typed_preflight_failed"
                    : "no_validated_candidate";
                var noValidatedReason = noValidatedOutcome == "typed_preflight_failed"
                    ? "candidate_failed_typed_preflight"
                    : "no_candidate_passed_validation_and_preflight";
                return CreateCaseResult(
                    status: "completed",
                    outcome: noValidatedOutcome,
                    failureStage: null,
                    failureReason: null,
                    failureDetail: null,
                    templateFingerprint,
                    invocationId,
                    candidateId,
                    searchParameterSetCount,
                    heldOutParameterSetCount,
                    generation,
                    equivalencePassed: equivalenceFailures == 0,
                    benchmarkPairs: [],
                    heldOutPairs: [],
                    baselineMedianMs: null,
                    candidateMedianMs: null,
                    improvementPct: null,
                    startedAt,
                    details: new
                    {
                        candidate_intake_count = candidateIntake.Count,
                        equivalence_failures = equivalenceFailures,
                        preflight_failures = preflightFailures,
                        result_detail = noValidatedReason
                    });
            }

            stage = "search_benchmark";
            var backgroundResult = await RunBackgroundOptimizerAsync(
                metadataConnection,
                targetConnection,
                templateFingerprint,
                normalizedSql,
                searchParameterSets,
                workloadItem,
                cancellationToken);
            candidateId = backgroundResult.SelectedCandidateId;
            var benchmarkPairs = backgroundResult.EvaluationBenchmarkPairs;
            var latestBenchmarkPairs = backgroundResult.LatestBenchmarkPairs;
            var promotionEvaluation = backgroundResult.PromotionEvaluation;

            PrintBenchmarkSummary(
                latestBenchmarkPairs,
                workloadItem.WorkloadLabel,
                "search",
                _options.PromotionMinimumImprovementPct);

            stage = "promotion";
            if (!promotionEvaluation.Promoted)
            {
                Console.WriteLine(
                    FormattableString.Invariant(
                        $"Controlled pipeline completed. No promotion: {promotionEvaluation.Reason} (pairs={promotionEvaluation.PairCount}, p_value={FormatNullable(promotionEvaluation.PValue)}, median_improvement_pct={promotionEvaluation.ImprovementPct:F2}, stability_cv={promotionEvaluation.StabilityCoefficientOfVariation:F3})."));

                return CreateCaseResult(
                    status: "completed",
                    outcome: "candidate_pool",
                    failureStage: null,
                    failureReason: null,
                    failureDetail: null,
                    templateFingerprint,
                    invocationId,
                    candidateId,
                    searchParameterSetCount,
                    heldOutParameterSetCount,
                    generation,
                    equivalencePassed: true,
                        benchmarkPairs,
                        heldOutPairs: [],
                        baselineMedianMs: promotionEvaluation.BaselineMedianMs,
                    candidateMedianMs: promotionEvaluation.CandidateMedianMs,
                        improvementPct: promotionEvaluation.ImprovementPct,
                        startedAt,
                        details: new
                        {
                            promotion = promotionEvaluation,
                            background_optimizer = backgroundResult.Summary
                        });
            }

            await PromoteCandidateAsync(metadataConnection, templateFingerprint, candidateId.Value, promotionEvaluation, cancellationToken);
            Console.WriteLine(
                FormattableString.Invariant(
                    $"Controlled pipeline completed. Promoted candidate {candidateId} with paired evidence: pairs={promotionEvaluation.PairCount}, p_value={promotionEvaluation.PValue:F6}, median_improvement_pct={promotionEvaluation.ImprovementPct:F2}, stability_cv={promotionEvaluation.StabilityCoefficientOfVariation:F3}."));

            if (!_options.RunHeldOutEvaluation)
            {
                return CreateCaseResult(
                    status: "completed",
                    outcome: "promoted",
                    failureStage: null,
                    failureReason: null,
                    failureDetail: null,
                    templateFingerprint,
                    invocationId,
                    candidateId,
                    searchParameterSetCount,
                    heldOutParameterSetCount,
                    generation,
                    equivalencePassed: true,
                    benchmarkPairs,
                    heldOutPairs: [],
                    baselineMedianMs: promotionEvaluation.BaselineMedianMs,
                    candidateMedianMs: promotionEvaluation.CandidateMedianMs,
                    improvementPct: promotionEvaluation.ImprovementPct,
                    startedAt,
                    details: new
                    {
                        promotion = promotionEvaluation,
                        background_optimizer = backgroundResult.Summary
                    });
            }

            if (string.IsNullOrWhiteSpace(workloadItem.HeldOutParameterFile))
            {
                return CreateCaseResult(
                    status: "failed",
                    outcome: "held_out_missing_parameters",
                    failureStage: "held_out_setup",
                    failureReason: "RUN_HELD_OUT_EVALUATION is enabled but no held-out parameter file is set",
                    failureDetail: workloadItem.QueryFile,
                    templateFingerprint,
                    invocationId,
                    candidateId,
                    searchParameterSetCount,
                    heldOutParameterSetCount,
                    generation,
                    equivalencePassed: true,
                    benchmarkPairs,
                    heldOutPairs: [],
                    baselineMedianMs: promotionEvaluation.BaselineMedianMs,
                    candidateMedianMs: promotionEvaluation.CandidateMedianMs,
                    improvementPct: promotionEvaluation.ImprovementPct,
                    startedAt,
                    details: new
                    {
                        promotion = promotionEvaluation,
                        background_optimizer = backgroundResult.Summary
                    });
            }

            var heldOutParameterFile = workloadItem.HeldOutParameterFile;
            var heldOutParameterSets = await LoadParameterSetsAsync(heldOutParameterFile, "held-out", cancellationToken);
            heldOutParameterSetCount = heldOutParameterSets.Count;

            stage = "held_out_benchmark";
            var heldOutPairs = await RunPairedBenchmarksAsync(
                metadataConnection,
                targetConnection,
                templateFingerprint,
                candidateId.Value,
                backgroundResult.SelectedCandidateSourceDetail,
                normalizedSql,
                backgroundResult.SelectedCandidateSql,
                heldOutParameterSets,
                benchmarkPhase: "held_out",
                workloadItem,
                parameterFile: heldOutParameterFile,
                cancellationToken);

            PrintBenchmarkSummary(
                heldOutPairs,
                workloadItem.WorkloadLabel,
                "held_out",
                _options.PromotionMinimumImprovementPct);

            return CreateCaseResult(
                status: "completed",
                outcome: "held_out_completed",
                failureStage: null,
                failureReason: null,
                failureDetail: null,
                templateFingerprint,
                invocationId,
                candidateId,
                searchParameterSetCount,
                heldOutParameterSetCount,
                generation,
                equivalencePassed: true,
                benchmarkPairs,
                heldOutPairs,
                baselineMedianMs: promotionEvaluation.BaselineMedianMs,
                candidateMedianMs: promotionEvaluation.CandidateMedianMs,
                improvementPct: promotionEvaluation.ImprovementPct,
                startedAt,
                details: new
                {
                    promotion = promotionEvaluation,
                    background_optimizer = backgroundResult.Summary
                });
        }
        catch (Exception ex)
        {
            return CreateCaseResult(
                status: "failed",
                outcome: "exception",
                failureStage: stage,
                failureReason: ex.Message,
                failureDetail: ex.ToString(),
                templateFingerprint,
                invocationId,
                candidateId,
                searchParameterSetCount,
                heldOutParameterSetCount,
                generation,
                equivalencePassed: null,
                benchmarkPairs: [],
                heldOutPairs: [],
                baselineMedianMs: null,
                candidateMedianMs: null,
                improvementPct: null,
                startedAt,
                details: new { exception_type = ex.GetType().FullName });
        }
    }

    private WorkloadCaseResult CreateCaseResult(
        string status,
        string outcome,
        string? failureStage,
        string? failureReason,
        string? failureDetail,
        string? templateFingerprint,
        Guid? invocationId,
        Guid? candidateId,
        int searchParameterSetCount,
        int heldOutParameterSetCount,
        GenerateCandidatesResponse? generation,
        bool? equivalencePassed,
        IReadOnlyList<BenchmarkPair> benchmarkPairs,
        IReadOnlyList<BenchmarkPair> heldOutPairs,
        double? baselineMedianMs,
        double? candidateMedianMs,
        double? improvementPct,
        DateTimeOffset startedAt,
        object details)
    {
        return new WorkloadCaseResult(
            status,
            outcome,
            failureStage,
            failureReason,
            failureDetail,
            templateFingerprint,
            invocationId,
            candidateId,
            searchParameterSetCount,
            heldOutParameterSetCount,
            generation is null ? 0 : generation.Stats.RuleCandidatesRaw + generation.Stats.LlmCandidatesRaw,
            generation?.Stats.Returned ?? 0,
            generation?.Stats.Rejected ?? 0,
            generation?.Stats.AfterDedup ?? 0,
            generation?.Stats.AfterStructuralValidation ?? 0,
            equivalencePassed,
            benchmarkPairs.Count,
            heldOutPairs.Count,
            baselineMedianMs,
            candidateMedianMs,
            improvementPct,
            startedAt,
            DateTimeOffset.UtcNow,
            JsonSerializer.Serialize(
                new
                {
                    stage_details = details,
                    run = new
                    {
                        experiment_run_id = _options.ExperimentRunId,
                        optimizer_profile = _options.OptimizerProfile,
                        workload_manifest_file = _options.WorkloadManifestFile,
                        enable_llm = _options.EnableLlm,
                        enable_rules = _options.EnableRules,
                        model_provider = _options.ModelProvider,
                        model = _options.DefaultModel,
                        max_llm_candidates = _options.MaxLlmCandidates,
                        model_timeout_seconds = _options.ModelTimeoutSeconds,
                        rewrite_service_http_timeout_seconds = _options.RewriteServiceHttpTimeoutSeconds,
                        equivalence_timeout_ms = _options.EquivalenceTimeoutMs,
                        equivalence_max_rows_full_compare = _options.EquivalenceMaxRowsFullCompare,
                        benchmark_iterations = _options.BenchmarkIterations,
                        run_held_out_evaluation = _options.RunHeldOutEvaluation,
                        allowed_rule_families = _options.AllowedRuleFamilies,
                        bandit = new
                        {
                            strategy = _options.BanditStrategy,
                            random_seed = _options.BanditRandomSeed,
                            observation_variance = _options.BanditObservationVariance,
                            ucb1_exploration_coefficient = _options.UcbExplorationCoefficient,
                            background_optimizer_rounds = _options.BackgroundOptimizerRounds,
                            background_optimizer_parameter_limit = _options.BackgroundOptimizerParameterLimit
                        },
                        promotion = new
                        {
                            minimum_pairs = _options.MinimumPromotionPairs,
                            alpha = _options.PromotionAlpha,
                            minimum_improvement_pct = _options.PromotionMinimumImprovementPct,
                            maximum_candidate_coefficient_of_variation = _options.PromotionMaxCoefficientOfVariation
                        }
                    },
                    host = new
                    {
                        cpu_model = _options.HostCpuModel,
                        logical_processor_count = _options.HostLogicalProcessorCount,
                        total_memory_bytes = _options.HostTotalMemoryBytes,
                        os_description = _options.HostOsDescription,
                        docker_version = _options.DockerVersion,
                        docker_compose_version = _options.DockerComposeVersion
                    },
                    container_limits = CreateContainerLimits(_options.OptimizerProfile, _options.EnableLlm)
                },
                JsonOptions));
    }

    private async Task<IReadOnlyList<WorkloadItem>> LoadWorkloadItemsAsync(CancellationToken cancellationToken)
    {
        await using var stream = File.OpenRead(_options.WorkloadManifestFile);
        var manifest = await JsonSerializer.DeserializeAsync<List<WorkloadItem>>(stream, JsonOptions, cancellationToken);
        if (manifest is not { Count: > 0 })
        {
            throw new InvalidOperationException($"No workload entries found in manifest {_options.WorkloadManifestFile}.");
        }

        foreach (var item in manifest)
        {
            item.Validate(_options.WorkloadManifestFile);
        }

        Console.WriteLine($"Loaded {manifest.Count} controlled workload entries from {_options.WorkloadManifestFile}.");
        return manifest;
    }
}
