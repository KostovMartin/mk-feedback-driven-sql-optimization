using System.Text.Json;
using Npgsql;

namespace QueryOptimizer.WorkloadRunner;

internal sealed partial class ControlledWorkloadRunner
{
    private async Task<QueryMeasurement> AttachExplainPlanAsync(
        NpgsqlConnection connection,
        string sql,
        ParameterSet parameterSet,
        QueryMeasurement measurement,
        CancellationToken cancellationToken)
    {
        var plan = await CaptureExplainPlanAsync(connection, sql, parameterSet, cancellationToken);
        return measurement with
        {
            PlanningTimeMs = plan?.PlanningTimeMs,
            PlanJson = plan?.PlanJson,
            PlanAnalysisJson = plan?.PlanAnalysisJson,
            RowsScanned = plan?.RowsScanned,
            SharedHitBlocks = plan?.SharedHitBlocks,
            SharedReadBlocks = plan?.SharedReadBlocks,
            TempWrittenBlocks = plan?.TempWrittenBlocks
        };
    }

    private async Task<QueryPlanMeasurement?> CaptureExplainPlanAsync(
        NpgsqlConnection connection,
        string sql,
        ParameterSet parameterSet,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        var explainTarget = ToNpgsqlSql(NormalizeSql(sql).TrimEnd(';'));
        command.CommandText = $"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {explainTarget}";
        command.CommandTimeout = Math.Max(1, (int)Math.Ceiling(_options.EquivalenceTimeoutMs / 1000.0));
        BindParameters(command, parameterSet);

        var result = await command.ExecuteScalarAsync(cancellationToken);
        if (result is null or DBNull)
        {
            return null;
        }

        var planJson = result switch
        {
            string value => value,
            JsonDocument document => document.RootElement.GetRawText(),
            JsonElement element => element.GetRawText(),
            _ => JsonSerializer.Serialize(result, JsonOptions)
        };

        using var parsed = JsonDocument.Parse(planJson);
        var root = parsed.RootElement.ValueKind == JsonValueKind.Array
            ? parsed.RootElement[0]
            : parsed.RootElement;

        if (!root.TryGetProperty("Plan", out var planRoot))
        {
            return new QueryPlanMeasurement(
                PlanningTimeMs: TryGetDouble(root, "Planning Time"),
                ExecutionTimeMs: TryGetDouble(root, "Execution Time"),
                PlanJson: planJson,
                PlanAnalysisJson: "{}",
                RowsScanned: null,
                SharedHitBlocks: null,
                SharedReadBlocks: null,
                TempWrittenBlocks: null);
        }

        var stats = new PlanStats();
        CollectPlanStats(planRoot, stats);
        var planningTime = TryGetDouble(root, "Planning Time");
        var executionTime = TryGetDouble(root, "Execution Time");
        var analysis = JsonSerializer.Serialize(
            new
            {
                planning_time_ms = planningTime,
                execution_time_ms = executionTime,
                total_cost = TryGetDouble(planRoot, "Total Cost"),
                actual_rows = TryGetDouble(planRoot, "Actual Rows"),
                actual_loops = TryGetDouble(planRoot, "Actual Loops"),
                node_types = stats.NodeTypes.Distinct(StringComparer.OrdinalIgnoreCase).ToArray(),
                join_types = stats.JoinTypes.Distinct(StringComparer.OrdinalIgnoreCase).ToArray(),
                scan_types = stats.ScanTypes.Distinct(StringComparer.OrdinalIgnoreCase).ToArray(),
                rows_scanned = stats.RowsScanned,
                shared_hit_blocks = stats.SharedHitBlocks,
                shared_read_blocks = stats.SharedReadBlocks,
                temp_written_blocks = stats.TempWrittenBlocks,
                has_seq_scan = stats.HasSeqScan,
                has_hash_join = stats.HasHashJoin,
                has_nested_loop = stats.HasNestedLoop,
                has_merge_join = stats.HasMergeJoin,
                has_sort = stats.HasSort,
                has_materialize = stats.HasMaterialize
            },
            JsonOptions);

        return new QueryPlanMeasurement(
            planningTime,
            executionTime,
            planJson,
            analysis,
            stats.RowsScanned,
            stats.SharedHitBlocks,
            stats.SharedReadBlocks,
            stats.TempWrittenBlocks);
    }

    private static void CollectPlanStats(JsonElement node, PlanStats stats)
    {
        var nodeType = TryGetString(node, "Node Type");
        if (!string.IsNullOrWhiteSpace(nodeType))
        {
            stats.NodeTypes.Add(nodeType);
            if (nodeType.Contains("Scan", StringComparison.OrdinalIgnoreCase))
            {
                stats.ScanTypes.Add(nodeType);
                stats.RowsScanned += (long)Math.Round(
                    (TryGetDouble(node, "Actual Rows") ?? 0) *
                    Math.Max(1, TryGetDouble(node, "Actual Loops") ?? 1));
            }

            stats.HasSeqScan |= nodeType.Equals("Seq Scan", StringComparison.OrdinalIgnoreCase);
            stats.HasHashJoin |= nodeType.Equals("Hash Join", StringComparison.OrdinalIgnoreCase);
            stats.HasNestedLoop |= nodeType.Equals("Nested Loop", StringComparison.OrdinalIgnoreCase);
            stats.HasMergeJoin |= nodeType.Equals("Merge Join", StringComparison.OrdinalIgnoreCase);
            stats.HasSort |= nodeType.Equals("Sort", StringComparison.OrdinalIgnoreCase);
            stats.HasMaterialize |= nodeType.Equals("Materialize", StringComparison.OrdinalIgnoreCase);
        }

        var joinType = TryGetString(node, "Join Type");
        if (!string.IsNullOrWhiteSpace(joinType))
        {
            stats.JoinTypes.Add(joinType);
        }

        stats.SharedHitBlocks += TryGetLong(node, "Shared Hit Blocks") ?? 0;
        stats.SharedReadBlocks += TryGetLong(node, "Shared Read Blocks") ?? 0;
        stats.TempWrittenBlocks += TryGetLong(node, "Temp Written Blocks") ?? 0;

        if (node.TryGetProperty("Plans", out var children) && children.ValueKind == JsonValueKind.Array)
        {
            foreach (var child in children.EnumerateArray())
            {
                CollectPlanStats(child, stats);
            }
        }
    }

    private static string? TryGetString(JsonElement element, string propertyName)
    {
        return element.TryGetProperty(propertyName, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;
    }

    private static double? TryGetDouble(JsonElement element, string propertyName)
    {
        return element.TryGetProperty(propertyName, out var value) && value.TryGetDouble(out var number)
            ? number
            : null;
    }

    private static long? TryGetLong(JsonElement element, string propertyName)
    {
        return element.TryGetProperty(propertyName, out var value) && value.TryGetInt64(out var number)
            ? number
            : null;
    }
}
