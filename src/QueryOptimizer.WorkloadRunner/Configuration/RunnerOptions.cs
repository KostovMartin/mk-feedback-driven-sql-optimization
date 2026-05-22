using System.Globalization;

namespace QueryOptimizer.WorkloadRunner;

internal sealed record RunnerOptions(
    string TargetDbConnection,
    string MetadataDbConnection,
    string RewriteServiceUrl,
    int RewriteServiceHttpTimeoutSeconds,
    string QueriesPath,
    string ParametersPath,
    string WorkloadManifestFile,
    string ExperimentRunId,
    IReadOnlyList<string> AllowedRuleFamilies,
    string DefaultModel,
    string ModelProvider,
    double Temperature,
    int MaxLlmCandidates,
    int ModelTimeoutSeconds,
    bool EnableLlm,
    bool EnableRules,
    int EquivalenceTimeoutMs,
    int EquivalenceMaxRowsFullCompare,
    int ValidationParameterSetLimit,
    int BenchmarkIterations,
    bool CaptureExplainPlans,
    int MinimumPromotionPairs,
    double PromotionAlpha,
    double PromotionMinimumImprovementPct,
    double PromotionMaxCoefficientOfVariation,
    bool RunHeldOutEvaluation,
    string BanditStrategy,
    int BanditRandomSeed,
    double BanditObservationVariance,
    double UcbExplorationCoefficient,
    int BackgroundOptimizerRounds,
    int BackgroundOptimizerParameterLimit,
    string OptimizerProfile,
    string HostCpuModel,
    string HostLogicalProcessorCount,
    string HostTotalMemoryBytes,
    string HostOsDescription,
    string DockerVersion,
    string DockerComposeVersion)
{
    public static RunnerOptions FromEnvironment()
    {
        var enableLlm = ParseBool("ENABLE_LLM", false);
        var defaultModel = Environment.GetEnvironmentVariable("DEFAULT_MODEL") ?? string.Empty;
        if (enableLlm && string.IsNullOrWhiteSpace(defaultModel))
        {
            throw new InvalidOperationException("DEFAULT_MODEL must be supplied when ENABLE_LLM is true.");
        }

        return new RunnerOptions(
            Required("TARGET_DB_CONNECTION"),
            Required("METADATA_DB_CONNECTION"),
            Environment.GetEnvironmentVariable("REWRITE_SERVICE_URL") ?? "http://localhost:8081",
            ParseInt("REWRITE_SERVICE_HTTP_TIMEOUT_SECONDS", 120),
            Environment.GetEnvironmentVariable("QUERIES_PATH") ?? "tests/smoke/queries",
            Environment.GetEnvironmentVariable("PARAMETERS_PATH") ?? "tests/smoke/parameters",
            Required("WORKLOAD_MANIFEST_FILE"),
            Environment.GetEnvironmentVariable("EXPERIMENT_RUN_ID") ?? "local-run",
            SplitCsv("ALLOWED_RULE_FAMILIES", ["A", "B", "C", "D", "E"]),
            defaultModel.Trim(),
            Environment.GetEnvironmentVariable("MODEL_PROVIDER") ?? "ollama",
            ParseDouble("TEMPERATURE", 0.7),
            ParseInt("MAX_LLM_CANDIDATES", 0),
            ParseInt("MODEL_TIMEOUT_SECONDS", 30),
            enableLlm,
            ParseBool("ENABLE_RULES", true),
            ParseInt("EQUIVALENCE_TIMEOUT_MS", 60000),
            ParseInt("EQUIVALENCE_MAX_ROWS_FULL_COMPARE", 100000),
            ParseInt("VALIDATION_PARAMETER_SET_LIMIT", 3),
            ParseInt("BENCHMARK_ITERATIONS", 1),
            ParseBool("CAPTURE_EXPLAIN_PLANS", false),
            ParseInt("OPTIMIZER_MIN_EXECUTION_COUNT", 10),
            ParseDouble("PROMOTION_ALPHA", 0.05),
            ParseDouble("PROMOTION_MIN_IMPROVEMENT_PCT", 2.0),
            ParseDouble("PROMOTION_MAX_CANDIDATE_CV", 0.3),
            ParseBool("RUN_HELD_OUT_EVALUATION", false),
            NormalizeBanditStrategy(Environment.GetEnvironmentVariable("BANDIT_STRATEGY") ?? "thompson"),
            ParseInt("BANDIT_RANDOM_SEED", 12345),
            ParseDouble("BANDIT_OBSERVATION_VARIANCE", 0.1),
            ParseDouble("UCB1_EXPLORATION_COEFFICIENT", Math.Sqrt(2.0)),
            ParseInt("BACKGROUND_OPTIMIZER_ROUNDS", 1),
            ParseInt("BACKGROUND_OPTIMIZER_PARAMETER_LIMIT", 5),
            Environment.GetEnvironmentVariable("OPTIMIZER_PROFILE") ?? "controlled",
            Optional("HOST_CPU_MODEL"),
            Optional("HOST_LOGICAL_PROCESSOR_COUNT"),
            Optional("HOST_TOTAL_MEMORY_BYTES"),
            Optional("HOST_OS_DESCRIPTION"),
            Optional("DOCKER_VERSION"),
            Optional("DOCKER_COMPOSE_VERSION"));
    }

    private static string Required(string name)
    {
        var value = Environment.GetEnvironmentVariable(name);
        if (string.IsNullOrWhiteSpace(value))
        {
            throw new InvalidOperationException($"Required environment variable {name} is not set.");
        }

        return value;
    }

    private static string Optional(string name, string defaultValue = "unknown")
    {
        var value = Environment.GetEnvironmentVariable(name);
        return string.IsNullOrWhiteSpace(value) ? defaultValue : value;
    }

    private static string NormalizeBanditStrategy(string value)
    {
        return value.Trim().ToLowerInvariant() switch
        {
            "thompson" => "thompson",
            "ucb1" => "ucb1",
            _ => throw new InvalidOperationException("BANDIT_STRATEGY must be 'thompson' or 'ucb1'.")
        };
    }

    private static IReadOnlyList<string> SplitCsv(string name, IReadOnlyList<string> defaultValue)
    {
        var value = Environment.GetEnvironmentVariable(name);
        return string.IsNullOrWhiteSpace(value)
            ? defaultValue
            : value.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    }

    private static int ParseInt(string name, int defaultValue)
    {
        return int.TryParse(Environment.GetEnvironmentVariable(name), NumberStyles.Integer, CultureInfo.InvariantCulture, out var value)
            ? value
            : defaultValue;
    }

    private static bool ParseBool(string name, bool defaultValue)
    {
        var value = Environment.GetEnvironmentVariable(name);
        return string.IsNullOrWhiteSpace(value)
            ? defaultValue
            : value.Equals("1", StringComparison.OrdinalIgnoreCase)
              || value.Equals("true", StringComparison.OrdinalIgnoreCase)
              || value.Equals("yes", StringComparison.OrdinalIgnoreCase)
              || value.Equals("on", StringComparison.OrdinalIgnoreCase);
    }

    private static double ParseDouble(string name, double defaultValue)
    {
        return double.TryParse(Environment.GetEnvironmentVariable(name), NumberStyles.Float, CultureInfo.InvariantCulture, out var value)
            ? value
            : defaultValue;
    }
}
