namespace QueryOptimizer.WorkloadRunner.Bandits;

internal static class BanditStateUpdater
{
    private const double MinimumVariance = 0.000001;

    public static double ComputeReward(BanditObservation observation)
    {
        if (observation.BaselineMs <= 0 || observation.CandidateMs >= observation.BaselineMs)
        {
            return 0.0;
        }

        return (observation.BaselineMs - observation.CandidateMs) / observation.BaselineMs;
    }

    public static BanditStateUpdate Update(
        BanditArm prior,
        IReadOnlyCollection<BanditObservation> observations,
        BanditOptions options,
        DateTimeOffset observedAt)
    {
        ArgumentNullException.ThrowIfNull(observations);
        ArgumentNullException.ThrowIfNull(options);

        var rewards = observations.Select(ComputeReward).ToArray();
        var observedPulls = rewards.Length;
        var observedReward = rewards.Sum();
        var observedMean = observedPulls == 0 ? 0.0 : rewards.Average();

        var totalPulls = prior.TotalPulls + observedPulls;
        var totalReward = prior.TotalReward + observedReward;
        var posterior = UpdateNormalPosterior(
            prior.TotalPulls == 0 ? 0.5 : prior.MeanReward,
            Math.Max(prior.RewardVariance, MinimumVariance),
            observedMean,
            Math.Max(options.ObservationVariance, MinimumVariance));

        double? ucbScore = null;
        if (options.Strategy == BanditStrategy.Ucb1 && totalPulls > 0)
        {
            ucbScore = totalReward / totalPulls;
        }

        return new BanditStateUpdate(
            TotalPulls: totalPulls,
            TotalReward: totalReward,
            MeanReward: posterior.Mean,
            RewardVariance: posterior.Variance,
            UcbScore: ucbScore,
            LastPulledAt: observedAt);
    }

    private static (double Mean, double Variance) UpdateNormalPosterior(
        double priorMean,
        double priorVariance,
        double observedMean,
        double observationVariance)
    {
        var posteriorVariance = 1.0 / ((1.0 / priorVariance) + (1.0 / observationVariance));
        var posteriorMean = posteriorVariance * ((priorMean / priorVariance) + (observedMean / observationVariance));
        return (posteriorMean, Math.Max(posteriorVariance, MinimumVariance));
    }
}
