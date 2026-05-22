using System.Text.Json;
using Npgsql;
using NpgsqlTypes;
using QueryOptimizer.WorkloadRunner.Bandits;

namespace QueryOptimizer.WorkloadRunner;

internal sealed partial class ControlledWorkloadRunner
{
    private static async Task<NpgsqlConnection> OpenWithRetryAsync(string connectionString, CancellationToken cancellationToken)
    {
        Exception? lastError = null;
        for (var attempt = 1; attempt <= 30; attempt++)
        {
            try
            {
                var connection = new NpgsqlConnection(connectionString);
                await connection.OpenAsync(cancellationToken);
                return connection;
            }
            catch (Exception ex) when (attempt < 30)
            {
                lastError = ex;
                await Task.Delay(TimeSpan.FromSeconds(1), cancellationToken);
            }
        }

        throw new InvalidOperationException("Could not connect to PostgreSQL.", lastError);
    }

    private static async Task UpsertTemplateAsync(
        NpgsqlConnection connection,
        string templateFingerprint,
        string normalizedSql,
        IReadOnlyList<string> tablesReferenced,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO query_templates (
                template_fingerprint,
                normalized_sql,
                execution_count,
                is_eligible,
                tables_referenced,
                updated_at
            )
            VALUES (@templateFingerprint, @normalizedSql, 0, true, @tablesReferenced, now())
            ON CONFLICT (template_fingerprint) DO UPDATE SET
                normalized_sql = EXCLUDED.normalized_sql,
                is_eligible = true,
                tables_referenced = EXCLUDED.tables_referenced,
                last_seen = now(),
                updated_at = now();
            """;
        command.Parameters.AddWithValue("templateFingerprint", templateFingerprint);
        command.Parameters.AddWithValue("normalizedSql", normalizedSql);
        command.Parameters.AddWithValue("tablesReferenced", tablesReferenced.ToArray());
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private async Task InsertInvocationAsync(
        NpgsqlConnection connection,
        Guid invocationId,
        string templateFingerprint,
        string schemaSnapshotHash,
        WorkloadItem workloadItem,
        CancellationToken cancellationToken)
    {
        var generationParameters = JsonSerializer.Serialize(
            new
            {
                enable_llm = _options.EnableLlm,
                enable_rules = _options.EnableRules,
                model_provider = _options.ModelProvider,
                model = _options.DefaultModel,
                max_llm_candidates = _options.MaxLlmCandidates,
                model_timeout_seconds = _options.ModelTimeoutSeconds,
                bandit = new
                {
                    strategy = _options.BanditStrategy,
                    random_seed = _options.BanditRandomSeed,
                    observation_variance = _options.BanditObservationVariance,
                    ucb1_exploration_coefficient = _options.UcbExplorationCoefficient,
                    background_optimizer_rounds = _options.BackgroundOptimizerRounds,
                    background_optimizer_parameter_limit = _options.BackgroundOptimizerParameterLimit
                },
                profile = _options.OptimizerProfile,
                experiment_run_id = _options.ExperimentRunId,
                workload_manifest_file = _options.WorkloadManifestFile,
                query_file = workloadItem.QueryFile,
                workload_label = workloadItem.WorkloadLabel,
                expected_candidate_source_detail = workloadItem.ExpectedCandidateSourceDetail,
                allowed_rule_families = _options.AllowedRuleFamilies,
                search_parameter_file = workloadItem.ParameterFile,
                held_out_parameter_file = workloadItem.HeldOutParameterFile,
                validation_parameter_set_limit = _options.ValidationParameterSetLimit,
                run_held_out_evaluation = _options.RunHeldOutEvaluation,
                capture_explain_plans = _options.CaptureExplainPlans,
                promotion = new
                {
                    minimum_pairs = _options.MinimumPromotionPairs,
                    alpha = _options.PromotionAlpha,
                    minimum_improvement_pct = _options.PromotionMinimumImprovementPct,
                    maximum_candidate_coefficient_of_variation = _options.PromotionMaxCoefficientOfVariation
                }
            },
            JsonOptions);

        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO invocations (
                id,
                experiment_run_id,
                template_fingerprint,
                trigger_type,
                generation_parameters,
                schema_snapshot_hash
            )
            VALUES (@id, @experimentRunId, @templateFingerprint, 'manual', @generationParameters, @schemaSnapshotHash);
            """;
        command.Parameters.AddWithValue("id", invocationId);
        command.Parameters.AddWithValue("experimentRunId", _options.ExperimentRunId);
        command.Parameters.AddWithValue("templateFingerprint", templateFingerprint);
        command.Parameters.Add(new NpgsqlParameter("generationParameters", NpgsqlDbType.Jsonb) { Value = generationParameters });
        command.Parameters.AddWithValue("schemaSnapshotHash", schemaSnapshotHash);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static async Task<Guid> UpsertCandidateAsync(
        NpgsqlConnection connection,
        string templateFingerprint,
        Guid invocationId,
        CandidateDto candidate,
        CancellationToken cancellationToken)
    {
        var generationMetadata = JsonSerializer.Serialize(
            new
            {
                candidate.StructuralValidation,
                rule_certified = CandidateGenerationMetadata.IsRuleCertified(
                    candidate.SourceType,
                    candidate.AppliedRules)
            },
            JsonOptions);

        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO candidates (
                id,
                template_fingerprint,
                sql_text,
                canonical_hash,
                source_type,
                source_detail,
                generation_metadata,
                applied_rules,
                invocation_id,
                safety_status,
                semantic_status,
                parameter_mapping
            )
            VALUES (
                @id,
                @templateFingerprint,
                @sqlText,
                @canonicalHash,
                @sourceType,
                @sourceDetail,
                @generationMetadata,
                @appliedRules,
                @invocationId,
                'safe',
                'unvalidated',
                @parameterMapping
            )
            ON CONFLICT (template_fingerprint, canonical_hash) DO UPDATE SET
                sql_text = EXCLUDED.sql_text,
                source_type = EXCLUDED.source_type,
                source_detail = EXCLUDED.source_detail,
                generation_metadata = EXCLUDED.generation_metadata,
                applied_rules = EXCLUDED.applied_rules,
                invocation_id = EXCLUDED.invocation_id,
                safety_status = 'safe',
                semantic_status = 'unvalidated',
                parameter_mapping = EXCLUDED.parameter_mapping,
                updated_at = now()
            RETURNING id;
            """;
        command.Parameters.AddWithValue("id", Guid.NewGuid());
        command.Parameters.AddWithValue("templateFingerprint", templateFingerprint);
        command.Parameters.AddWithValue("sqlText", candidate.SqlText);
        command.Parameters.AddWithValue("canonicalHash", candidate.CanonicalHash);
        command.Parameters.AddWithValue("sourceType", candidate.SourceType);
        command.Parameters.AddWithValue("sourceDetail", candidate.SourceDetail);
        command.Parameters.Add(new NpgsqlParameter("generationMetadata", NpgsqlDbType.Jsonb) { Value = generationMetadata });
        command.Parameters.AddWithValue("appliedRules", candidate.AppliedRules.ToArray());
        command.Parameters.AddWithValue("invocationId", invocationId);
        command.Parameters.Add(new NpgsqlParameter("parameterMapping", NpgsqlDbType.Jsonb) { Value = candidate.ParameterMapping.GetRawText() });

        var result = await command.ExecuteScalarAsync(cancellationToken);
        return result is Guid id ? id : throw new InvalidOperationException("Candidate insert did not return an id.");
    }

    private static async Task PersistEquivalenceAsync(
        NpgsqlConnection connection,
        Guid candidateId,
        EquivalenceResponse equivalence,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO equivalence_checks (
                candidate_id,
                check_type,
                passed,
                method,
                parameter_set_ids,
                checks,
                original_row_count,
                candidate_row_count,
                rows_compared,
                mismatch_detail,
                execution_time_ms
            )
            VALUES (
                @candidateId,
                'initial',
                @passed,
                @method,
                @parameterSetIds,
                @checks,
                @originalRowCount,
                @candidateRowCount,
                @rowsCompared,
                @mismatchDetail,
                @executionTimeMs
            );
            """;

        var checksJson = JsonSerializer.Serialize(equivalence.Checks, JsonOptions);
        command.Parameters.AddWithValue("candidateId", candidateId);
        command.Parameters.AddWithValue("passed", equivalence.Passed);
        command.Parameters.AddWithValue("method", equivalence.MethodUsed);
        command.Parameters.AddWithValue("parameterSetIds", equivalence.Checks.Select(check => check.ParameterSetId).ToArray());
        command.Parameters.Add(new NpgsqlParameter("checks", NpgsqlDbType.Jsonb) { Value = checksJson });
        command.Parameters.AddWithValue("originalRowCount", equivalence.Checks.Sum(check => check.OriginalRowCount));
        command.Parameters.AddWithValue("candidateRowCount", equivalence.Checks.Sum(check => check.CandidateRowCount));
        command.Parameters.AddWithValue("rowsCompared", equivalence.Checks.Sum(check => check.RowsCompared));
        command.Parameters.Add(new NpgsqlParameter("mismatchDetail", NpgsqlDbType.Jsonb)
        {
            Value = equivalence.MismatchDetail.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null
                ? DBNull.Value
                : equivalence.MismatchDetail.GetRawText()
        });
        command.Parameters.AddWithValue("executionTimeMs", equivalence.Checks.Sum(check => check.OriginalExecutionTimeMs + check.CandidateExecutionTimeMs));
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static async Task MarkCandidateSemanticStatusAsync(
        NpgsqlConnection connection,
        Guid candidateId,
        bool validated,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE candidates
            SET semantic_status = @semanticStatus,
                safety_status = @safetyStatus,
                updated_at = now()
            WHERE id = @candidateId;
            """;
        command.Parameters.AddWithValue("semanticStatus", validated ? "validated" : "failed");
        command.Parameters.AddWithValue("safetyStatus", validated ? "safe" : "rejected");
        command.Parameters.AddWithValue("candidateId", candidateId);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static async Task EnsureBanditStateRowsAsync(
        NpgsqlConnection connection,
        string templateFingerprint,
        string strategy,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO bandit_state (
                candidate_id,
                template_fingerprint,
                strategy,
                total_pulls,
                total_reward,
                mean_reward,
                reward_variance,
                updated_at
            )
            SELECT
                id,
                template_fingerprint,
                @strategy,
                0,
                0,
                0,
                1.0,
                now()
            FROM candidates
            WHERE template_fingerprint = @templateFingerprint
              AND semantic_status = 'validated'
              AND promotion_status IN ('pool', 'promoted')
            ON CONFLICT (candidate_id) DO UPDATE SET
                strategy = EXCLUDED.strategy,
                updated_at = now();
            """;
        command.Parameters.AddWithValue("strategy", strategy);
        command.Parameters.AddWithValue("templateFingerprint", templateFingerprint);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static async Task<IReadOnlyList<BanditArm>> LoadBanditArmsAsync(
        NpgsqlConnection connection,
        string templateFingerprint,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT
                c.id,
                c.template_fingerprint,
                c.sql_text,
                c.source_detail,
                c.created_at,
                bs.total_pulls,
                bs.total_reward,
                bs.mean_reward,
                bs.reward_variance,
                bs.last_pulled_at
            FROM candidates c
            JOIN bandit_state bs ON bs.candidate_id = c.id
            WHERE c.template_fingerprint = @templateFingerprint
              AND c.semantic_status = 'validated'
              AND c.promotion_status IN ('pool', 'promoted')
            ORDER BY c.created_at, c.id;
            """;
        command.Parameters.AddWithValue("templateFingerprint", templateFingerprint);

        var arms = new List<BanditArm>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            var candidateId = reader.GetGuid(0);
            arms.Add(new BanditArm(
                new CandidateId(candidateId.ToString("D")),
                reader.GetString(1),
                reader.GetString(2),
                reader.GetString(3),
                reader.GetInt32(5),
                reader.GetDouble(6),
                reader.GetDouble(7),
                reader.GetDouble(8),
                ReadDateTimeOffset(reader, 4),
                reader.IsDBNull(9) ? null : ReadDateTimeOffset(reader, 9)));
        }

        return arms;
    }

    private static async Task UpdateBanditStateAsync(
        NpgsqlConnection connection,
        BanditArm priorArm,
        IReadOnlyList<BenchmarkPair> benchmarkPairs,
        BanditOptions options,
        CancellationToken cancellationToken)
    {
        var observations = benchmarkPairs
            .Select(pair => new BanditObservation(pair.ParameterSetId, pair.BaselineMs, pair.CandidateMs))
            .ToArray();
        var update = BanditStateUpdater.Update(priorArm, observations, options, DateTimeOffset.UtcNow);
        var candidateId = Guid.Parse(priorArm.CandidateId.ToString());

        await using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE bandit_state
            SET strategy = @strategy,
                total_pulls = @totalPulls,
                total_reward = @totalReward,
                mean_reward = @meanReward,
                reward_variance = @rewardVariance,
                ucb_score = @ucbScore,
                last_pulled_at = @lastPulledAt,
                updated_at = now()
            WHERE candidate_id = @candidateId;
            """;
        command.Parameters.AddWithValue("strategy", options.Strategy == BanditStrategy.Ucb1 ? "ucb1" : "thompson");
        command.Parameters.AddWithValue("totalPulls", update.TotalPulls);
        command.Parameters.AddWithValue("totalReward", update.TotalReward);
        command.Parameters.AddWithValue("meanReward", update.MeanReward);
        command.Parameters.AddWithValue("rewardVariance", update.RewardVariance);
        command.Parameters.Add(new NpgsqlParameter("ucbScore", NpgsqlDbType.Double)
        {
            Value = update.UcbScore.HasValue ? update.UcbScore.Value : DBNull.Value
        });
        command.Parameters.AddWithValue("lastPulledAt", update.LastPulledAt);
        command.Parameters.AddWithValue("candidateId", candidateId);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static async Task<IReadOnlyList<BenchmarkPair>> LoadSearchBenchmarkPairsAsync(
        NpgsqlConnection connection,
        string templateFingerprint,
        Guid candidateId,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT
                candidate.parameter_set_id,
                baseline.mean_execution_time,
                baseline.median_execution_time,
                baseline.p75_execution_time,
                baseline.p95_execution_time,
                candidate.mean_execution_time,
                candidate.median_execution_time,
                candidate.p75_execution_time,
                candidate.p95_execution_time,
                candidate.rows_returned,
                candidate.signed_improvement_pct
            FROM benchmark_runs candidate
            JOIN benchmark_runs baseline ON baseline.run_pair_id = candidate.run_pair_id
                AND baseline.is_baseline = true
            WHERE candidate.template_fingerprint = @templateFingerprint
              AND candidate.candidate_id = @candidateId
              AND candidate.is_baseline = false
              AND candidate.benchmark_phase = 'search'
              AND baseline.benchmark_phase = 'search'
            ORDER BY candidate.run_at, candidate.id;
            """;
        command.Parameters.AddWithValue("templateFingerprint", templateFingerprint);
        command.Parameters.AddWithValue("candidateId", candidateId);

        var pairs = new List<BenchmarkPair>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            var baselineMedian = reader.GetDouble(2);
            var candidateMedian = reader.GetDouble(6);
            var signedImprovement = reader.IsDBNull(10)
                ? ComputeSignedImprovementPercent(baselineMedian, candidateMedian)
                : reader.GetDouble(10);
            pairs.Add(new BenchmarkPair(
                reader.IsDBNull(0) ? "unparameterized" : reader.GetString(0),
                reader.GetDouble(1),
                baselineMedian,
                reader.GetDouble(3),
                reader.GetDouble(4),
                reader.GetDouble(5),
                candidateMedian,
                reader.GetDouble(7),
                reader.GetDouble(8),
                reader.IsDBNull(9) ? 0 : reader.GetInt32(9),
                signedImprovement));
        }

        return pairs;
    }

    private static DateTimeOffset ReadDateTimeOffset(NpgsqlDataReader reader, int ordinal)
    {
        var value = reader.GetDateTime(ordinal);
        return value.Kind == DateTimeKind.Unspecified
            ? new DateTimeOffset(DateTime.SpecifyKind(value, DateTimeKind.Utc))
            : new DateTimeOffset(value.ToUniversalTime());
    }

    private static async Task InsertBenchmarkRunAsync(
        NpgsqlConnection connection,
        Guid runPairId,
        string templateFingerprint,
        string benchmarkPhase,
        Guid? candidateId,
        string parameterSetId,
        int executionOrder,
        QueryMeasurement measurement,
        double? signedImprovementPct,
        bool isBaseline,
        WorkloadItem workloadItem,
        string parameterFile,
        string baselineSql,
        string candidateSql,
        string candidateSourceDetail,
        RunnerOptions options,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO benchmark_runs (
                experiment_run_id,
                candidate_id,
                template_fingerprint,
                benchmark_phase,
                run_pair_id,
                parameter_set_id,
                execution_order,
                execution_times_ms,
                mean_execution_time,
                median_execution_time,
                p75_execution_time,
                p95_execution_time,
                planning_time_ms,
                rows_returned,
                rows_scanned,
                shared_hit_blocks,
                shared_read_blocks,
                temp_written_blocks,
                plan_json,
                plan_analysis,
                signed_improvement_pct,
                reproducibility_metadata,
                is_baseline,
                warm_cache
            )
            VALUES (
                @experimentRunId,
                @candidateId,
                @templateFingerprint,
                @benchmarkPhase,
                @runPairId,
                @parameterSetId,
                @executionOrder,
                @executionTimesMs,
                @meanExecutionTime,
                @medianExecutionTime,
                @p75ExecutionTime,
                @p95ExecutionTime,
                @planningTimeMs,
                @rowsReturned,
                @rowsScanned,
                @sharedHitBlocks,
                @sharedReadBlocks,
                @tempWrittenBlocks,
                @planJson,
                @planAnalysis,
                @signedImprovementPct,
                @reproducibilityMetadata,
                @isBaseline,
                true
            );
            """;

        command.Parameters.AddWithValue("experimentRunId", options.ExperimentRunId);
        var candidateParameter = command.Parameters.Add("candidateId", NpgsqlDbType.Uuid);
        candidateParameter.Value = candidateId.HasValue ? candidateId.Value : DBNull.Value;
        command.Parameters.AddWithValue("templateFingerprint", templateFingerprint);
        command.Parameters.AddWithValue("benchmarkPhase", benchmarkPhase);
        command.Parameters.AddWithValue("runPairId", runPairId);
        command.Parameters.AddWithValue("parameterSetId", parameterSetId);
        command.Parameters.AddWithValue("executionOrder", executionOrder);
        command.Parameters.AddWithValue("executionTimesMs", measurement.ExecutionTimesMs);
        command.Parameters.AddWithValue("meanExecutionTime", measurement.MeanExecutionTime);
        command.Parameters.AddWithValue("medianExecutionTime", measurement.MedianExecutionTime);
        command.Parameters.AddWithValue("p75ExecutionTime", measurement.P75ExecutionTime);
        command.Parameters.AddWithValue("p95ExecutionTime", measurement.P95ExecutionTime);
        command.Parameters.Add(new NpgsqlParameter("planningTimeMs", NpgsqlDbType.Double)
        {
            Value = measurement.PlanningTimeMs.HasValue ? measurement.PlanningTimeMs.Value : DBNull.Value
        });
        command.Parameters.AddWithValue("rowsReturned", measurement.RowsReturned);
        command.Parameters.Add(new NpgsqlParameter("rowsScanned", NpgsqlDbType.Bigint)
        {
            Value = measurement.RowsScanned.HasValue ? measurement.RowsScanned.Value : DBNull.Value
        });
        command.Parameters.Add(new NpgsqlParameter("sharedHitBlocks", NpgsqlDbType.Bigint)
        {
            Value = measurement.SharedHitBlocks.HasValue ? measurement.SharedHitBlocks.Value : DBNull.Value
        });
        command.Parameters.Add(new NpgsqlParameter("sharedReadBlocks", NpgsqlDbType.Bigint)
        {
            Value = measurement.SharedReadBlocks.HasValue ? measurement.SharedReadBlocks.Value : DBNull.Value
        });
        command.Parameters.Add(new NpgsqlParameter("tempWrittenBlocks", NpgsqlDbType.Bigint)
        {
            Value = measurement.TempWrittenBlocks.HasValue ? measurement.TempWrittenBlocks.Value : DBNull.Value
        });
        command.Parameters.Add(new NpgsqlParameter("planJson", NpgsqlDbType.Jsonb)
        {
            Value = string.IsNullOrWhiteSpace(measurement.PlanJson) ? DBNull.Value : measurement.PlanJson
        });
        command.Parameters.Add(new NpgsqlParameter("planAnalysis", NpgsqlDbType.Jsonb)
        {
            Value = string.IsNullOrWhiteSpace(measurement.PlanAnalysisJson) ? DBNull.Value : measurement.PlanAnalysisJson
        });
        var improvementParameter = command.Parameters.Add("signedImprovementPct", NpgsqlDbType.Double);
        improvementParameter.Value = signedImprovementPct.HasValue ? signedImprovementPct.Value : DBNull.Value;
        command.Parameters.Add(new NpgsqlParameter("reproducibilityMetadata", NpgsqlDbType.Jsonb)
        {
            Value = JsonSerializer.Serialize(
                new
                {
                    benchmark_order = "deterministic_ab_ba",
                    benchmark_phase = benchmarkPhase,
                    experiment_run_id = options.ExperimentRunId,
                    workload_label = workloadItem.WorkloadLabel,
                    workload_description = workloadItem.WorkloadDescription,
                    query_file = workloadItem.QueryFile,
                    parameter_file = parameterFile,
                    measured_role = isBaseline ? "baseline" : "candidate",
                    capture_explain_plans = options.CaptureExplainPlans,
                    measured_sql = NormalizeSql(isBaseline ? baselineSql : candidateSql),
                    baseline_sql = NormalizeSql(baselineSql),
                    candidate_sql = NormalizeSql(candidateSql),
                    candidate_source_detail = candidateSourceDetail,
                    bandit = new
                    {
                        strategy = options.BanditStrategy,
                        random_seed = options.BanditRandomSeed,
                        observation_variance = options.BanditObservationVariance,
                        ucb1_exploration_coefficient = options.UcbExplorationCoefficient,
                        background_optimizer_rounds = options.BackgroundOptimizerRounds,
                        background_optimizer_parameter_limit = options.BackgroundOptimizerParameterLimit
                    },
                    host = new
                    {
                        cpu_model = options.HostCpuModel,
                        logical_processor_count = options.HostLogicalProcessorCount,
                        total_memory_bytes = options.HostTotalMemoryBytes,
                        os_description = options.HostOsDescription,
                        docker_version = options.DockerVersion,
                        docker_compose_version = options.DockerComposeVersion
                    },
                    container_limits = CreateContainerLimits(options.OptimizerProfile, options.EnableLlm)
                },
                JsonOptions)
        });
        command.Parameters.AddWithValue("isBaseline", isBaseline);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static async Task CompleteInvocationAsync(
        NpgsqlConnection connection,
        Guid invocationId,
        GenerateCandidatesResponse generation,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE invocations
            SET candidates_generated = @candidatesGenerated,
                candidates_after_dedup = @candidatesAfterDedup,
                candidates_after_safety = @candidatesAfterSafety,
                model_name = @modelName,
                model_digest = @modelDigest,
                model_runtime_version = @modelRuntimeVersion,
                prompt_template_version = @promptTemplateVersion,
                prompt_hash = @promptHash,
                rule_latency_ms = @ruleLatencyMs,
                llm_latency_ms = @llmLatencyMs,
                completed_at = now()
            WHERE id = @invocationId;
            """;
        command.Parameters.AddWithValue("candidatesGenerated", generation.Stats.RuleCandidatesRaw + generation.Stats.LlmCandidatesRaw);
        command.Parameters.AddWithValue("candidatesAfterDedup", generation.Stats.AfterDedup);
        command.Parameters.AddWithValue("candidatesAfterSafety", generation.Stats.AfterStructuralValidation);
        command.Parameters.Add(new NpgsqlParameter("modelName", NpgsqlDbType.Text)
        {
            Value = string.IsNullOrWhiteSpace(generation.ModelRuntime.Name)
                ? DBNull.Value
                : generation.ModelRuntime.Name
        });
        command.Parameters.Add(new NpgsqlParameter("modelDigest", NpgsqlDbType.Text)
        {
            Value = string.IsNullOrWhiteSpace(generation.ModelRuntime.Digest)
                ? DBNull.Value
                : generation.ModelRuntime.Digest
        });
        command.Parameters.Add(new NpgsqlParameter("modelRuntimeVersion", NpgsqlDbType.Text)
        {
            Value = string.IsNullOrWhiteSpace(generation.ModelRuntime.RuntimeVersion)
                ? DBNull.Value
                : generation.ModelRuntime.RuntimeVersion
        });
        command.Parameters.Add(new NpgsqlParameter("promptTemplateVersion", NpgsqlDbType.Text)
        {
            Value = string.IsNullOrWhiteSpace(generation.ModelRuntime.PromptTemplateVersion)
                ? DBNull.Value
                : generation.ModelRuntime.PromptTemplateVersion
        });
        command.Parameters.Add(new NpgsqlParameter("promptHash", NpgsqlDbType.Text)
        {
            Value = string.IsNullOrWhiteSpace(generation.ModelRuntime.PromptHash)
                ? DBNull.Value
                : generation.ModelRuntime.PromptHash
        });
        command.Parameters.AddWithValue("ruleLatencyMs", generation.Stats.RuleGenerationMs);
        command.Parameters.AddWithValue("llmLatencyMs", generation.Stats.LlmGenerationMs);
        command.Parameters.AddWithValue("invocationId", invocationId);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private async Task InsertWorkloadCaseResultAsync(
        NpgsqlConnection connection,
        WorkloadItem workloadItem,
        WorkloadCaseResult result,
        CancellationToken cancellationToken)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO workload_case_results (
                experiment_run_id,
                workload_manifest_file,
                workload_label,
                workload_description,
                query_file,
                parameter_file,
                held_out_parameter_file,
                expected_candidate_source_detail,
                template_fingerprint,
                invocation_id,
                candidate_id,
                status,
                outcome,
                failure_stage,
                failure_reason,
                failure_detail,
                search_parameter_sets,
                held_out_parameter_sets,
                candidates_generated,
                candidates_returned,
                candidates_rejected,
                candidates_after_dedup,
                candidates_after_safety,
                equivalence_passed,
                benchmark_pairs,
                held_out_benchmark_pairs,
                baseline_median_ms,
                candidate_median_ms,
                improvement_pct,
                details,
                started_at,
                completed_at
            )
            VALUES (
                @experimentRunId,
                @workloadManifestFile,
                @workloadLabel,
                @workloadDescription,
                @queryFile,
                @parameterFile,
                @heldOutParameterFile,
                @expectedCandidateSourceDetail,
                @templateFingerprint,
                @invocationId,
                @candidateId,
                @status,
                @outcome,
                @failureStage,
                @failureReason,
                @failureDetail,
                @searchParameterSets,
                @heldOutParameterSets,
                @candidatesGenerated,
                @candidatesReturned,
                @candidatesRejected,
                @candidatesAfterDedup,
                @candidatesAfterSafety,
                @equivalencePassed,
                @benchmarkPairs,
                @heldOutBenchmarkPairs,
                @baselineMedianMs,
                @candidateMedianMs,
                @improvementPct,
                @details,
                @startedAt,
                @completedAt
            );
            """;

        command.Parameters.AddWithValue("experimentRunId", _options.ExperimentRunId);
        command.Parameters.AddWithValue("workloadManifestFile", _options.WorkloadManifestFile);
        command.Parameters.AddWithValue("workloadLabel", workloadItem.WorkloadLabel);
        command.Parameters.AddWithValue("workloadDescription", workloadItem.WorkloadDescription);
        command.Parameters.AddWithValue("queryFile", workloadItem.QueryFile);
        command.Parameters.AddWithValue("parameterFile", workloadItem.ParameterFile);
        command.Parameters.Add(new NpgsqlParameter("heldOutParameterFile", NpgsqlDbType.Text)
        {
            Value = string.IsNullOrWhiteSpace(workloadItem.HeldOutParameterFile)
                ? DBNull.Value
                : workloadItem.HeldOutParameterFile
        });
        command.Parameters.AddWithValue("expectedCandidateSourceDetail", workloadItem.ExpectedCandidateSourceDetail);
        command.Parameters.Add(new NpgsqlParameter("templateFingerprint", NpgsqlDbType.Text)
        {
            Value = string.IsNullOrWhiteSpace(result.TemplateFingerprint)
                ? DBNull.Value
                : result.TemplateFingerprint
        });
        command.Parameters.Add(new NpgsqlParameter("invocationId", NpgsqlDbType.Uuid)
        {
            Value = result.InvocationId.HasValue ? result.InvocationId.Value : DBNull.Value
        });
        command.Parameters.Add(new NpgsqlParameter("candidateId", NpgsqlDbType.Uuid)
        {
            Value = result.CandidateId.HasValue ? result.CandidateId.Value : DBNull.Value
        });
        command.Parameters.AddWithValue("status", result.Status);
        command.Parameters.AddWithValue("outcome", result.Outcome);
        command.Parameters.Add(new NpgsqlParameter("failureStage", NpgsqlDbType.Text)
        {
            Value = string.IsNullOrWhiteSpace(result.FailureStage) ? DBNull.Value : result.FailureStage
        });
        command.Parameters.Add(new NpgsqlParameter("failureReason", NpgsqlDbType.Text)
        {
            Value = string.IsNullOrWhiteSpace(result.FailureReason) ? DBNull.Value : result.FailureReason
        });
        command.Parameters.Add(new NpgsqlParameter("failureDetail", NpgsqlDbType.Text)
        {
            Value = string.IsNullOrWhiteSpace(result.FailureDetail) ? DBNull.Value : result.FailureDetail
        });
        command.Parameters.AddWithValue("searchParameterSets", result.SearchParameterSetCount);
        command.Parameters.AddWithValue("heldOutParameterSets", result.HeldOutParameterSetCount);
        command.Parameters.AddWithValue("candidatesGenerated", result.CandidatesGenerated);
        command.Parameters.AddWithValue("candidatesReturned", result.CandidatesReturned);
        command.Parameters.AddWithValue("candidatesRejected", result.CandidatesRejected);
        command.Parameters.AddWithValue("candidatesAfterDedup", result.CandidatesAfterDedup);
        command.Parameters.AddWithValue("candidatesAfterSafety", result.CandidatesAfterSafety);
        command.Parameters.Add(new NpgsqlParameter("equivalencePassed", NpgsqlDbType.Boolean)
        {
            Value = result.EquivalencePassed.HasValue ? result.EquivalencePassed.Value : DBNull.Value
        });
        command.Parameters.AddWithValue("benchmarkPairs", result.BenchmarkPairs);
        command.Parameters.AddWithValue("heldOutBenchmarkPairs", result.HeldOutBenchmarkPairs);
        command.Parameters.Add(new NpgsqlParameter("baselineMedianMs", NpgsqlDbType.Double)
        {
            Value = result.BaselineMedianMs.HasValue ? result.BaselineMedianMs.Value : DBNull.Value
        });
        command.Parameters.Add(new NpgsqlParameter("candidateMedianMs", NpgsqlDbType.Double)
        {
            Value = result.CandidateMedianMs.HasValue ? result.CandidateMedianMs.Value : DBNull.Value
        });
        command.Parameters.Add(new NpgsqlParameter("improvementPct", NpgsqlDbType.Double)
        {
            Value = result.ImprovementPct.HasValue ? result.ImprovementPct.Value : DBNull.Value
        });
        command.Parameters.Add(new NpgsqlParameter("details", NpgsqlDbType.Jsonb) { Value = result.DetailsJson });
        command.Parameters.AddWithValue("startedAt", result.StartedAt);
        command.Parameters.AddWithValue("completedAt", result.CompletedAt);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }

    private static async Task PromoteCandidateAsync(
        NpgsqlConnection connection,
        string templateFingerprint,
        Guid candidateId,
        PromotionEvaluation evaluation,
        CancellationToken cancellationToken)
    {
        await using var transaction = await connection.BeginTransactionAsync(cancellationToken);

        await using (var demotePrevious = connection.CreateCommand())
        {
            demotePrevious.Transaction = transaction;
            demotePrevious.CommandText = """
                UPDATE candidates
                SET promotion_status = 'pool',
                    updated_at = now()
                WHERE template_fingerprint = @templateFingerprint
                  AND promotion_status = 'promoted'
                  AND id <> @candidateId;
                """;
            demotePrevious.Parameters.AddWithValue("templateFingerprint", templateFingerprint);
            demotePrevious.Parameters.AddWithValue("candidateId", candidateId);
            await demotePrevious.ExecuteNonQueryAsync(cancellationToken);
        }

        await using (var promote = connection.CreateCommand())
        {
            promote.Transaction = transaction;
            promote.CommandText = """
                UPDATE candidates
                SET promotion_status = 'promoted',
                    updated_at = now()
                WHERE id = @candidateId
                  AND semantic_status = 'validated';
                """;
            promote.Parameters.AddWithValue("candidateId", candidateId);
            var rows = await promote.ExecuteNonQueryAsync(cancellationToken);
            if (rows != 1)
            {
                throw new InvalidOperationException($"Candidate {candidateId} was not in a validated state and could not be promoted.");
            }
        }

        await using (var updateTemplate = connection.CreateCommand())
        {
            updateTemplate.Transaction = transaction;
            updateTemplate.CommandText = """
                UPDATE query_templates
                SET current_candidate_id = @candidateId,
                    updated_at = now()
                WHERE template_fingerprint = @templateFingerprint;
                """;
            updateTemplate.Parameters.AddWithValue("templateFingerprint", templateFingerprint);
            updateTemplate.Parameters.AddWithValue("candidateId", candidateId);
            await updateTemplate.ExecuteNonQueryAsync(cancellationToken);
        }

        await using (var insertDecision = connection.CreateCommand())
        {
            insertDecision.Transaction = transaction;
            insertDecision.CommandText = """
                INSERT INTO decisions (
                    template_fingerprint,
                    decision_type,
                    candidate_id,
                    reason,
                    baseline_median_ms,
                    candidate_median_ms,
                    improvement_pct,
                    p_value,
                    confidence_interval,
                    benchmark_summary
                )
                VALUES (
                    @templateFingerprint,
                    'promote',
                    @candidateId,
                    @reason,
                    @baselineMedianMs,
                    @candidateMedianMs,
                    @improvementPct,
                    @pValue,
                    @confidenceInterval,
                    @benchmarkSummary
                );
                """;
            insertDecision.Parameters.AddWithValue("templateFingerprint", templateFingerprint);
            insertDecision.Parameters.AddWithValue("candidateId", candidateId);
            insertDecision.Parameters.AddWithValue("reason", evaluation.Reason);
            insertDecision.Parameters.AddWithValue("baselineMedianMs", evaluation.BaselineMedianMs);
            insertDecision.Parameters.AddWithValue("candidateMedianMs", evaluation.CandidateMedianMs);
            insertDecision.Parameters.AddWithValue("improvementPct", evaluation.ImprovementPct);
            insertDecision.Parameters.AddWithValue("pValue", evaluation.PValue ?? 1.0);
            insertDecision.Parameters.Add(new NpgsqlParameter("confidenceInterval", NpgsqlDbType.Jsonb)
            {
                Value = JsonSerializer.Serialize(
                    new
                    {
                        method = "not_computed_first_build",
                        reason = "Bootstrap confidence intervals are generated in controlled pilot analysis exports."
                    },
                    JsonOptions)
            });
            insertDecision.Parameters.Add(new NpgsqlParameter("benchmarkSummary", NpgsqlDbType.Jsonb)
            {
                Value = JsonSerializer.Serialize(
                    new
                    {
                        evaluation.PairCount,
                        evaluation.Alpha,
                        evaluation.MinimumImprovementPct,
                        evaluation.MaxCoefficientOfVariation,
                        evaluation.BaselineMedianMs,
                        evaluation.CandidateMedianMs,
                        evaluation.ImprovementPct,
                        evaluation.PValue,
                        evaluation.StabilityCoefficientOfVariation,
                        stability_metric = "paired_reward_cv",
                        benchmark_phase = "search",
                        statistical_test = "one_sided_wilcoxon_signed_rank"
                    },
                    JsonOptions)
            });
            await insertDecision.ExecuteNonQueryAsync(cancellationToken);
        }

        await transaction.CommitAsync(cancellationToken);
    }
}
