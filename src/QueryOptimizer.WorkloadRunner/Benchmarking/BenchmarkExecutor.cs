using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Npgsql;
using NpgsqlTypes;

namespace QueryOptimizer.WorkloadRunner;

internal sealed partial class ControlledWorkloadRunner
{
    private async Task<List<BenchmarkPair>> RunPairedBenchmarksAsync(
        NpgsqlConnection metadataConnection,
        NpgsqlConnection targetConnection,
        string templateFingerprint,
        Guid candidateId,
        string candidateSourceDetail,
        string baselineSql,
        string candidateSql,
        IReadOnlyList<ParameterSet> parameterSets,
        string benchmarkPhase,
        WorkloadItem workloadItem,
        string parameterFile,
        CancellationToken cancellationToken)
    {
        ValidateBenchmarkPhase(benchmarkPhase);
        var pairs = new List<BenchmarkPair>();

        for (var i = 0; i < parameterSets.Count; i++)
        {
            var pairNumber = i + 1;
            var parameterSet = parameterSets[i];
            var runPairId = Guid.NewGuid();
            var baselineFirst = pairNumber % 2 == 1;

            QueryMeasurement baseline;
            QueryMeasurement candidate;

            if (baselineFirst)
            {
                baseline = await ExecuteMeasuredAsync(targetConnection, baselineSql, parameterSet, cancellationToken);
                candidate = await ExecuteMeasuredAsync(targetConnection, candidateSql, parameterSet, cancellationToken);
            }
            else
            {
                candidate = await ExecuteMeasuredAsync(targetConnection, candidateSql, parameterSet, cancellationToken);
                baseline = await ExecuteMeasuredAsync(targetConnection, baselineSql, parameterSet, cancellationToken);
            }

            var signedImprovement = ComputeSignedImprovementPercent(baseline.MedianExecutionTime, candidate.MedianExecutionTime);
            if (_options.CaptureExplainPlans)
            {
                baseline = await AttachExplainPlanAsync(targetConnection, baselineSql, parameterSet, baseline, cancellationToken);
                candidate = await AttachExplainPlanAsync(targetConnection, candidateSql, parameterSet, candidate, cancellationToken);
            }

            await InsertBenchmarkRunAsync(
                metadataConnection,
                runPairId,
                templateFingerprint,
                benchmarkPhase,
                candidateId: null,
                parameterSet.ParameterSetId,
                executionOrder: baselineFirst ? 1 : 2,
                baseline,
                signedImprovementPct: null,
                isBaseline: true,
                workloadItem,
                parameterFile,
                baselineSql,
                candidateSql,
                candidateSourceDetail,
                _options,
                cancellationToken);

            await InsertBenchmarkRunAsync(
                metadataConnection,
                runPairId,
                templateFingerprint,
                benchmarkPhase,
                candidateId,
                parameterSet.ParameterSetId,
                executionOrder: baselineFirst ? 2 : 1,
                candidate,
                signedImprovement,
                isBaseline: false,
                workloadItem,
                parameterFile,
                baselineSql,
                candidateSql,
                candidateSourceDetail,
                _options,
                cancellationToken);

            pairs.Add(
                new BenchmarkPair(
                    parameterSet.ParameterSetId,
                    baseline.MeanExecutionTime,
                    baseline.MedianExecutionTime,
                    baseline.P75ExecutionTime,
                    baseline.P95ExecutionTime,
                    candidate.MeanExecutionTime,
                    candidate.MedianExecutionTime,
                    candidate.P75ExecutionTime,
                    candidate.P95ExecutionTime,
                    candidate.RowsReturned,
                    signedImprovement));
        }

        await using var command = metadataConnection.CreateCommand();
        command.CommandText = """
            UPDATE candidates
            SET benchmark_runs = benchmark_runs + @benchmarkRuns,
                updated_at = now()
            WHERE id = @candidateId
              AND @benchmarkPhase = 'search';
            """;
        command.Parameters.AddWithValue("benchmarkRuns", pairs.Count);
        command.Parameters.AddWithValue("candidateId", candidateId);
        command.Parameters.AddWithValue("benchmarkPhase", benchmarkPhase);
        await command.ExecuteNonQueryAsync(cancellationToken);

        return pairs;
    }

    private static void ValidateBenchmarkPhase(string benchmarkPhase)
    {
        if (benchmarkPhase is "search" or "held_out" or "baseline_calibration" or "overhead")
        {
            return;
        }

        throw new ArgumentOutOfRangeException(
            nameof(benchmarkPhase),
            benchmarkPhase,
            "Benchmark phase must match the optimizer metadata schema.");
    }

    private async Task<List<(string ParameterSetId, QueryMeasurement Measurement)>> RunBaselineCalibrationAsync(
        NpgsqlConnection metadataConnection,
        NpgsqlConnection targetConnection,
        string templateFingerprint,
        string baselineSql,
        IReadOnlyList<ParameterSet> parameterSets,
        WorkloadItem workloadItem,
        string parameterFile,
        CancellationToken cancellationToken)
    {
        const string benchmarkPhase = "baseline_calibration";
        var measurements = new List<(string ParameterSetId, QueryMeasurement Measurement)>();

        foreach (var parameterSet in parameterSets)
        {
            var measurement = await ExecuteMeasuredAsync(targetConnection, baselineSql, parameterSet, cancellationToken);
            if (_options.CaptureExplainPlans)
            {
                measurement = await AttachExplainPlanAsync(targetConnection, baselineSql, parameterSet, measurement, cancellationToken);
            }

            await InsertBenchmarkRunAsync(
                metadataConnection,
                Guid.NewGuid(),
                templateFingerprint,
                benchmarkPhase,
                candidateId: null,
                parameterSet.ParameterSetId,
                executionOrder: 1,
                measurement,
                signedImprovementPct: null,
                isBaseline: true,
                workloadItem,
                parameterFile,
                baselineSql,
                baselineSql,
                BaselineOnlyCandidateSourceDetail,
                _options,
                cancellationToken);
            measurements.Add((parameterSet.ParameterSetId, measurement));
        }

        return measurements;
    }

    private async Task<QueryMeasurement> ExecuteMeasuredAsync(
        NpgsqlConnection connection,
        string sql,
        ParameterSet parameterSet,
        CancellationToken cancellationToken)
    {
        var times = new List<double>();
        var rowsReturned = 0;

        for (var i = 0; i < _options.BenchmarkIterations; i++)
        {
            await using var command = connection.CreateCommand();
            command.CommandText = ToNpgsqlSql(sql);
            BindParameters(command, parameterSet);

            var stopwatch = Stopwatch.StartNew();
            await using var reader = await command.ExecuteReaderAsync(cancellationToken);
            var rowCount = 0;
            while (await reader.ReadAsync(cancellationToken))
            {
                rowCount++;
            }

            stopwatch.Stop();
            times.Add(stopwatch.Elapsed.TotalMilliseconds);
            rowsReturned = rowCount;
        }

        return new QueryMeasurement(
            times.ToArray(),
            times.Average(),
            Median(times),
            Percentile(times, 75),
            Percentile(times, 95),
            rowsReturned,
            PlanningTimeMs: null,
            PlanJson: null,
            PlanAnalysisJson: null,
            RowsScanned: null,
            SharedHitBlocks: null,
            SharedReadBlocks: null,
            TempWrittenBlocks: null);
    }

    private static void PrintBenchmarkSummary(
        IReadOnlyList<BenchmarkPair> benchmarkPairs,
        string workloadLabel,
        string benchmarkPhase,
        double improvementThresholdPct)
    {
        if (benchmarkPairs.Count == 0)
        {
            Console.WriteLine($"Controlled benchmark datapoint ({benchmarkPhase}): no benchmark pairs recorded.");
            return;
        }

        var baselineMedian = Median(benchmarkPairs.Select(pair => pair.BaselineMs).ToArray());
        var candidateMedian = Median(benchmarkPairs.Select(pair => pair.CandidateMs).ToArray());
        var baselineMean = benchmarkPairs.Average(pair => pair.BaselineMs);
        var candidateMean = benchmarkPairs.Average(pair => pair.CandidateMs);
        var baselineP75 = Percentile(benchmarkPairs.Select(pair => pair.BaselineMs).ToArray(), 75);
        var candidateP75 = Percentile(benchmarkPairs.Select(pair => pair.CandidateMs).ToArray(), 75);
        var baselineP95 = Percentile(benchmarkPairs.Select(pair => pair.BaselineMs).ToArray(), 95);
        var candidateP95 = Percentile(benchmarkPairs.Select(pair => pair.CandidateMs).ToArray(), 95);
        var aggregateImprovement = ComputeSignedImprovementPercent(baselineMedian, candidateMedian);
        var meanPairImprovement = benchmarkPairs.Average(pair => pair.SignedImprovementPct);

        Console.WriteLine($"Controlled benchmark datapoint ({workloadLabel}, phase={benchmarkPhase}):");
        foreach (var pair in benchmarkPairs)
        {
            var improvedAtThreshold = pair.SignedImprovementPct >= improvementThresholdPct ? "yes" : "no";
            Console.WriteLine(FormattableString.Invariant(
                $"  {pair.ParameterSetId}: rows={pair.RowsReturned}, baseline_mean_ms={pair.BaselineMeanMs:F3}, baseline_median_ms={pair.BaselineMs:F3}, baseline_p75_ms={pair.BaselineP75Ms:F3}, baseline_p95_ms={pair.BaselineP95Ms:F3}, candidate_mean_ms={pair.CandidateMeanMs:F3}, candidate_median_ms={pair.CandidateMs:F3}, candidate_p75_ms={pair.CandidateP75Ms:F3}, candidate_p95_ms={pair.CandidateP95Ms:F3}, signed_improvement_pct={pair.SignedImprovementPct:F2}, improvement_threshold_pct={improvementThresholdPct:F2}, improved_at_threshold={improvedAtThreshold}"));
        }

        Console.WriteLine(FormattableString.Invariant(
            $"  aggregate: pairs={benchmarkPairs.Count}, baseline_mean_ms={baselineMean:F3}, baseline_median_ms={baselineMedian:F3}, baseline_p75_ms={baselineP75:F3}, baseline_p95_ms={baselineP95:F3}, candidate_mean_ms={candidateMean:F3}, candidate_median_ms={candidateMedian:F3}, candidate_p75_ms={candidateP75:F3}, candidate_p95_ms={candidateP95:F3}, signed_improvement_pct={aggregateImprovement:F2}, mean_pair_improvement_pct={meanPairImprovement:F2}"));
    }

    private static void PrintBaselineCalibrationSummary(
        IReadOnlyList<(string ParameterSetId, QueryMeasurement Measurement)> measurements,
        string workloadLabel)
    {
        Console.WriteLine($"Baseline calibration summary for {workloadLabel}:");
        foreach (var (parameterSetId, measurement) in measurements)
        {
            Console.WriteLine(FormattableString.Invariant(
                $"  {parameterSetId}: rows={measurement.RowsReturned}, mean_ms={measurement.MeanExecutionTime:F3}, median_ms={measurement.MedianExecutionTime:F3}, p75_ms={measurement.P75ExecutionTime:F3}, p95_ms={measurement.P95ExecutionTime:F3}"));
        }

        var aggregateMean = measurements.Average(item => item.Measurement.MeanExecutionTime);
        var aggregateMedian = Median(measurements.Select(item => item.Measurement.MedianExecutionTime).ToArray());
        var aggregateP75 = Percentile(measurements.Select(item => item.Measurement.P75ExecutionTime).ToArray(), 75);
        var aggregateP95 = Percentile(measurements.Select(item => item.Measurement.P95ExecutionTime).ToArray(), 95);
        Console.WriteLine(FormattableString.Invariant(
            $"  aggregate: parameter_sets={measurements.Count}, mean_ms={aggregateMean:F3}, median_ms={aggregateMedian:F3}, p75_ms={aggregateP75:F3}, p95_ms={aggregateP95:F3}"));
    }

    private static void BindParameters(NpgsqlCommand command, ParameterSet parameterSet)
    {
        foreach (var parameter in parameterSet.Parameters)
        {
            var name = "p" + parameter.Position.TrimStart('$');
            if (command.Parameters.Contains(name))
            {
                continue;
            }

            command.Parameters.Add(CreateDbParameter(name, parameter));
        }
    }

    private static NpgsqlParameter CreateDbParameter(string name, ParameterValue parameter)
    {
        var dbType = MapParameterType(parameter.Type);
        return dbType.HasValue
            ? new NpgsqlParameter(name, dbType.Value) { Value = ToDbValue(parameter) }
            : new NpgsqlParameter(name, ToDbValue(parameter));
    }

    private static NpgsqlDbType? MapParameterType(string typeName)
    {
        var normalizedType = typeName.Trim().ToLowerInvariant();
        if (normalizedType.Contains("bigint", StringComparison.Ordinal))
        {
            return NpgsqlDbType.Bigint;
        }

        if (normalizedType.Contains("int", StringComparison.Ordinal))
        {
            return NpgsqlDbType.Integer;
        }

        if (normalizedType.Contains("numeric", StringComparison.Ordinal) ||
            normalizedType.Contains("decimal", StringComparison.Ordinal))
        {
            return NpgsqlDbType.Numeric;
        }

        if (normalizedType == "date" || normalizedType.Contains(" date", StringComparison.Ordinal))
        {
            return NpgsqlDbType.Date;
        }

        if (normalizedType.Contains("timestamp", StringComparison.Ordinal))
        {
            return NpgsqlDbType.Timestamp;
        }

        if (normalizedType.Contains("bool", StringComparison.Ordinal))
        {
            return NpgsqlDbType.Boolean;
        }

        if (normalizedType.Contains("double", StringComparison.Ordinal) ||
            normalizedType.Contains("float", StringComparison.Ordinal) ||
            normalizedType.Contains("real", StringComparison.Ordinal))
        {
            return NpgsqlDbType.Double;
        }

        if (normalizedType.Contains("text", StringComparison.Ordinal) ||
            normalizedType.Contains("char", StringComparison.Ordinal) ||
            normalizedType.Contains("varchar", StringComparison.Ordinal))
        {
            return NpgsqlDbType.Text;
        }

        return null;
    }

    private static object ToDbValue(ParameterValue parameter)
    {
        if (parameter.Value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined)
        {
            return DBNull.Value;
        }

        if (parameter.Value.ValueKind == JsonValueKind.Number)
        {
            if (parameter.Type.Contains("numeric", StringComparison.OrdinalIgnoreCase) ||
                parameter.Type.Contains("decimal", StringComparison.OrdinalIgnoreCase))
            {
                return parameter.Value.GetDecimal();
            }

            if (parameter.Type.Contains("int", StringComparison.OrdinalIgnoreCase) &&
                parameter.Value.TryGetInt32(out var intValue))
            {
                return intValue;
            }

            if (parameter.Value.TryGetInt64(out var longValue))
            {
                return longValue;
            }

            return parameter.Value.GetDouble();
        }

        if (parameter.Value.ValueKind == JsonValueKind.String)
        {
            var value = parameter.Value.GetString() ?? string.Empty;
            if (parameter.Type.Equals("date", StringComparison.OrdinalIgnoreCase))
            {
                return DateOnly.Parse(value, CultureInfo.InvariantCulture);
            }

            if (parameter.Type.Contains("timestamp", StringComparison.OrdinalIgnoreCase))
            {
                return DateTime.Parse(value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal);
            }

            if (parameter.Type.Contains("numeric", StringComparison.OrdinalIgnoreCase) ||
                parameter.Type.Contains("decimal", StringComparison.OrdinalIgnoreCase))
            {
                return decimal.Parse(value, CultureInfo.InvariantCulture);
            }

            if (parameter.Type.Contains("bigint", StringComparison.OrdinalIgnoreCase))
            {
                return long.Parse(value, CultureInfo.InvariantCulture);
            }

            if (parameter.Type.Contains("int", StringComparison.OrdinalIgnoreCase))
            {
                return int.Parse(value, CultureInfo.InvariantCulture);
            }

            if (parameter.Type.Contains("bool", StringComparison.OrdinalIgnoreCase))
            {
                return bool.Parse(value);
            }

            return value;
        }

        if (parameter.Value.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            return parameter.Value.GetBoolean();
        }

        return parameter.Value.GetRawText();
    }

    private static string ToNpgsqlSql(string sql)
    {
        return ParameterRegex.Replace(sql, match => "@p" + match.Groups[1].Value);
    }

    private static string NormalizeSql(string sql)
    {
        return string.Join('\n', sql.ReplaceLineEndings("\n").Split('\n').Select(line => line.TrimEnd())).Trim();
    }

    private static string NormalizeIdentifier(string identifier)
    {
        var unqualified = identifier.Split('.', StringSplitOptions.RemoveEmptyEntries).LastOrDefault() ?? identifier;
        return unqualified.Trim().Trim('"').ToLowerInvariant();
    }

    private static string ComputeSha256(string text)
    {
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(text));
        return Convert.ToHexString(hash).ToLowerInvariant();
    }

    private static double Median(IReadOnlyList<double> values)
    {
        if (values.Count == 0)
        {
            throw new ArgumentException("Cannot compute a median over an empty list.", nameof(values));
        }

        var ordered = values.Order().ToArray();
        var middle = ordered.Length / 2;
        return ordered.Length % 2 == 1
            ? ordered[middle]
            : (ordered[middle - 1] + ordered[middle]) / 2.0;
    }

    private static double Percentile(IReadOnlyList<double> values, double percentile)
    {
        if (values.Count == 0)
        {
            throw new ArgumentException("Cannot compute a percentile over an empty list.", nameof(values));
        }

        if (percentile < 0 || percentile > 100)
        {
            throw new ArgumentOutOfRangeException(nameof(percentile), "Percentile must be between 0 and 100.");
        }

        var ordered = values.Order().ToArray();
        if (ordered.Length == 1)
        {
            return ordered[0];
        }

        var position = percentile / 100.0 * (ordered.Length - 1);
        var lower = (int)Math.Floor(position);
        var upper = (int)Math.Ceiling(position);
        if (lower == upper)
        {
            return ordered[lower];
        }

        var weight = position - lower;
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight;
    }

    private static double ComputeSignedImprovementPercent(double baselineMs, double candidateMs)
    {
        return baselineMs <= 0 ? 0 : (baselineMs - candidateMs) / baselineMs * 100.0;
    }

    private static object CreateContainerLimits(string optimizerProfile, bool enableLlm)
    {
        static object Limit(string cpus, string memory) => new
        {
            cpus,
            memory
        };

        var notApplicable = Limit("not-applicable", "not-applicable");
        var isTpch = string.Equals(optimizerProfile, "tpch", StringComparison.OrdinalIgnoreCase);

        return new
        {
            target_db = Limit("8.00", "16g"),
            metadata_db = Limit("2.00", "1g"),
            data_loader = Limit("4.00", "4g"),
            rewrite_service = Limit("2.00", "1g"),
            workload_runner = Limit("2.00", "1g"),
            ollama = enableLlm ? Limit("16.00", "40g") : notApplicable,
            tpch_generator = isTpch ? Limit("4.00", "4g") : notApplicable
        };
    }

    private static string FormatNullable(double? value)
    {
        return value.HasValue
            ? value.Value.ToString("F6", CultureInfo.InvariantCulture)
            : "not-computed";
    }
}
