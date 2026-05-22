namespace QueryOptimizer.WorkloadRunner;

internal static class PromotionEvaluator
{
    public static PromotionEvaluation Evaluate(IReadOnlyList<BenchmarkPair> benchmarkPairs, RunnerOptions options)
    {
        if (benchmarkPairs.Count == 0)
        {
            return new PromotionEvaluation(
                Promoted: false,
                Reason: "no_benchmark_pairs",
                PairCount: 0,
                BaselineMedianMs: 0,
                CandidateMedianMs: 0,
                ImprovementPct: 0,
                PValue: null,
                StabilityCoefficientOfVariation: 0,
                Alpha: options.PromotionAlpha,
                MinimumImprovementPct: options.PromotionMinimumImprovementPct,
                MaxCoefficientOfVariation: options.PromotionMaxCoefficientOfVariation);
        }

        var baselineTimes = benchmarkPairs.Select(pair => pair.BaselineMs).ToArray();
        var candidateTimes = benchmarkPairs.Select(pair => pair.CandidateMs).ToArray();
        var baselineMedian = Median(baselineTimes);
        var candidateMedian = Median(candidateTimes);
        var improvementPct = baselineMedian <= 0
            ? 0
            : (baselineMedian - candidateMedian) / baselineMedian * 100.0;
        var stabilityCv = CoefficientOfVariation(benchmarkPairs.Select(ComputeReward).ToArray());
        var pValue = WilcoxonSignedRankOneSidedPValue(benchmarkPairs);

        string reason;
        if (benchmarkPairs.Count < options.MinimumPromotionPairs)
        {
            reason = "statistically_insufficient";
        }
        else if (improvementPct < options.PromotionMinimumImprovementPct)
        {
            reason = "improvement_below_threshold";
        }
        else if (stabilityCv > options.PromotionMaxCoefficientOfVariation)
        {
            reason = "candidate_relative_latency_unstable";
        }
        else if (pValue >= options.PromotionAlpha)
        {
            reason = "not_statistically_significant";
        }
        else
        {
            reason = "paired_statistical_evidence";
        }

        return new PromotionEvaluation(
            Promoted: reason == "paired_statistical_evidence",
            Reason: reason,
            PairCount: benchmarkPairs.Count,
            BaselineMedianMs: baselineMedian,
            CandidateMedianMs: candidateMedian,
            ImprovementPct: improvementPct,
            PValue: pValue,
            StabilityCoefficientOfVariation: stabilityCv,
            Alpha: options.PromotionAlpha,
            MinimumImprovementPct: options.PromotionMinimumImprovementPct,
            MaxCoefficientOfVariation: options.PromotionMaxCoefficientOfVariation);
    }

    private static double WilcoxonSignedRankOneSidedPValue(IReadOnlyList<BenchmarkPair> benchmarkPairs)
    {
        const double zeroTolerance = 1e-12;
        var differences = benchmarkPairs
            .Select(pair => pair.BaselineMs - pair.CandidateMs)
            .Where(difference => Math.Abs(difference) > zeroTolerance)
            .Select(difference => new SignedDifference(difference, Math.Abs(difference)))
            .OrderBy(item => item.AbsoluteDifference)
            .ToArray();

        if (differences.Length == 0)
        {
            return 1.0;
        }

        var ranked = new List<RankedDifference>(differences.Length);
        var index = 0;
        while (index < differences.Length)
        {
            var start = index;
            var absoluteDifference = differences[index].AbsoluteDifference;
            while (index < differences.Length &&
                   Math.Abs(differences[index].AbsoluteDifference - absoluteDifference) <= zeroTolerance)
            {
                index++;
            }

            var end = index - 1;
            var averageRank = (start + 1 + end + 1) / 2.0;
            for (var rankIndex = start; rankIndex <= end; rankIndex++)
            {
                ranked.Add(new RankedDifference(differences[rankIndex].Difference, averageRank));
            }
        }

        var observed = (int)Math.Round(
            ranked.Where(item => item.Difference > 0).Sum(item => item.Rank) * 2.0,
            MidpointRounding.AwayFromZero);
        var scaledRanks = ranked
            .Select(item => (int)Math.Round(item.Rank * 2.0, MidpointRounding.AwayFromZero))
            .ToArray();
        var maxSum = scaledRanks.Sum();
        var counts = new double[maxSum + 1];
        counts[0] = 1.0;

        foreach (var rank in scaledRanks)
        {
            for (var sum = maxSum - rank; sum >= 0; sum--)
            {
                if (counts[sum] > 0)
                {
                    counts[sum + rank] += counts[sum];
                }
            }
        }

        var favorable = 0.0;
        for (var sum = observed; sum < counts.Length; sum++)
        {
            favorable += counts[sum];
        }

        return Math.Clamp(favorable / Math.Pow(2.0, ranked.Count), 0.0, 1.0);
    }

    private static double CoefficientOfVariation(IReadOnlyList<double> values)
    {
        const double zeroTolerance = 1e-12;

        if (values.Count <= 1)
        {
            return 0.0;
        }

        var mean = values.Average();
        if (mean <= zeroTolerance)
        {
            return 0.0;
        }

        var variance = values.Sum(value => Math.Pow(value - mean, 2)) / (values.Count - 1);
        return Math.Sqrt(variance) / mean;
    }

    private static double ComputeReward(BenchmarkPair pair)
    {
        if (pair.BaselineMs <= 0 || pair.CandidateMs >= pair.BaselineMs)
        {
            return 0.0;
        }

        return (pair.BaselineMs - pair.CandidateMs) / pair.BaselineMs;
    }

    private static double Median(IReadOnlyList<double> values)
    {
        var ordered = values.Order().ToArray();
        var middle = ordered.Length / 2;
        return ordered.Length % 2 == 1
            ? ordered[middle]
            : (ordered[middle - 1] + ordered[middle]) / 2.0;
    }

    private sealed record SignedDifference(double Difference, double AbsoluteDifference);

    private sealed record RankedDifference(double Difference, double Rank);
}
