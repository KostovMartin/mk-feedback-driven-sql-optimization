namespace QueryOptimizer.WorkloadRunner.Bandits;

internal sealed class BanditPolicy
{
    private readonly Random _random;

    public BanditPolicy(Random random)
    {
        _random = random;
    }

    public BanditSelection SelectCandidate(IReadOnlyCollection<BanditArm> arms, BanditOptions options)
    {
        ArgumentNullException.ThrowIfNull(arms);
        ArgumentNullException.ThrowIfNull(options);

        if (arms.Count == 0)
        {
            throw new ArgumentException("At least one candidate arm is required.", nameof(arms));
        }

        var initialPullArm = arms
            .Where(arm => arm.TotalPulls == 0)
            .OrderBy(arm => arm.CreatedAt)
            .ThenBy(arm => arm.CandidateId.ToString(), StringComparer.Ordinal)
            .FirstOrDefault();

        if (initialPullArm is not null)
        {
            return new BanditSelection(initialPullArm.CandidateId, BanditSelectionReason.InitialPull, null);
        }

        return options.Strategy switch
        {
            BanditStrategy.Thompson => SelectByThompsonSampling(arms),
            BanditStrategy.Ucb1 => SelectByUcb1(arms, options),
            _ => throw new ArgumentOutOfRangeException(nameof(options), options.Strategy, "Unsupported bandit strategy.")
        };
    }

    private BanditSelection SelectByThompsonSampling(IReadOnlyCollection<BanditArm> arms)
    {
        return arms
            .Select(arm => new BanditSelection(
                arm.CandidateId,
                BanditSelectionReason.ThompsonSample,
                SampleNormal(arm.MeanReward, Math.Max(arm.RewardVariance, 0.000001))))
            .OrderByDescending(selection => selection.Score)
            .ThenBy(selection => selection.CandidateId.ToString(), StringComparer.Ordinal)
            .First();
    }

    private static BanditSelection SelectByUcb1(IReadOnlyCollection<BanditArm> arms, BanditOptions options)
    {
        var totalPulls = arms.Sum(arm => arm.TotalPulls);
        if (totalPulls <= 0)
        {
            throw new InvalidOperationException("UCB1 scoring requires at least one observed pull.");
        }

        return arms
            .Select(arm =>
            {
                if (arm.TotalPulls <= 0)
                {
                    throw new InvalidOperationException("UCB1 scoring was asked to score a zero-pull arm.");
                }

                var score = arm.MeanReward
                    + options.UcbExplorationCoefficient * Math.Sqrt(Math.Log(totalPulls) / arm.TotalPulls);
                return new BanditSelection(arm.CandidateId, BanditSelectionReason.UcbScore, score);
            })
            .OrderByDescending(selection => selection.Score)
            .ThenBy(selection => selection.CandidateId.ToString(), StringComparer.Ordinal)
            .First();
    }

    private double SampleNormal(double mean, double variance)
    {
        var u1 = 1.0 - _random.NextDouble();
        var u2 = 1.0 - _random.NextDouble();
        var standardNormal = Math.Sqrt(-2.0 * Math.Log(u1)) * Math.Cos(2.0 * Math.PI * u2);
        return mean + Math.Sqrt(variance) * standardNormal;
    }
}
