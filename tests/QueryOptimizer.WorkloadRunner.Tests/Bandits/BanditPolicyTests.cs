using QueryOptimizer.WorkloadRunner.Bandits;

namespace QueryOptimizer.WorkloadRunner.Tests.Bandits;

internal static class BanditPolicyTests
{
    public static void Run()
    {
        SelectCandidateChoosesOldestZeroPullArmBeforeScoring();
        SelectCandidateDoesNotEvaluateUcbForZeroPullArm();
        ThompsonSelectionIsDeterministicForFixedSeed();
        UcbSelectionUsesMeanRewardAndExplorationTerm();
    }

    private static void SelectCandidateChoosesOldestZeroPullArmBeforeScoring()
    {
        var arms = new[]
        {
            Arm("existing", totalPulls: 4, meanReward: 0.25, createdAtOffsetMinutes: 0),
            Arm("newer", totalPulls: 0, meanReward: 0.0, createdAtOffsetMinutes: 2),
            Arm("older", totalPulls: 0, meanReward: 0.0, createdAtOffsetMinutes: 1)
        };
        var policy = new BanditPolicy(new Random(123));

        var selection = policy.SelectCandidate(arms, BanditOptions.Default with { Strategy = BanditStrategy.Thompson });

        TestAssert.Equal("older", selection.CandidateId.ToString());
        TestAssert.Equal(BanditSelectionReason.InitialPull, selection.Reason);
    }

    private static void SelectCandidateDoesNotEvaluateUcbForZeroPullArm()
    {
        var arms = new[]
        {
            Arm("zero", totalPulls: 0, meanReward: 0.0, createdAtOffsetMinutes: 0),
            Arm("scored", totalPulls: 2, meanReward: 0.2, createdAtOffsetMinutes: 1)
        };
        var policy = new BanditPolicy(new Random(123));

        var selection = policy.SelectCandidate(arms, BanditOptions.Default with { Strategy = BanditStrategy.Ucb1 });

        TestAssert.Equal("zero", selection.CandidateId.ToString());
        TestAssert.Equal(BanditSelectionReason.InitialPull, selection.Reason);
        TestAssert.Null(selection.Score);
    }

    private static void ThompsonSelectionIsDeterministicForFixedSeed()
    {
        var arms = new[]
        {
            Arm("a", totalPulls: 3, meanReward: 0.10, rewardVariance: 0.05, createdAtOffsetMinutes: 0),
            Arm("b", totalPulls: 3, meanReward: 0.20, rewardVariance: 0.05, createdAtOffsetMinutes: 1),
            Arm("c", totalPulls: 3, meanReward: 0.15, rewardVariance: 0.05, createdAtOffsetMinutes: 2)
        };
        var options = BanditOptions.Default with { Strategy = BanditStrategy.Thompson };

        var first = new BanditPolicy(new Random(777)).SelectCandidate(arms, options);
        var second = new BanditPolicy(new Random(777)).SelectCandidate(arms, options);

        TestAssert.Equal(first.CandidateId, second.CandidateId);
        TestAssert.Equal(first.Score, second.Score);
        TestAssert.Equal(BanditSelectionReason.ThompsonSample, first.Reason);
    }

    private static void UcbSelectionUsesMeanRewardAndExplorationTerm()
    {
        var arms = new[]
        {
            Arm("explore", totalPulls: 1, meanReward: 0.10, createdAtOffsetMinutes: 0),
            Arm("exploit", totalPulls: 10, meanReward: 0.30, createdAtOffsetMinutes: 1)
        };
        var policy = new BanditPolicy(new Random(123));

        var selection = policy.SelectCandidate(arms, BanditOptions.Default with { Strategy = BanditStrategy.Ucb1 });

        TestAssert.Equal("explore", selection.CandidateId.ToString());
        TestAssert.Equal(BanditSelectionReason.UcbScore, selection.Reason);
        TestAssert.NotNull(selection.Score);
    }

    private static BanditArm Arm(
        string id,
        int totalPulls,
        double meanReward,
        double rewardVariance = 1.0,
        int createdAtOffsetMinutes = 0)
    {
        return new BanditArm(
            CandidateId: new CandidateId(id),
            TemplateFingerprint: "template",
            SqlText: $"select '{id}'",
            SourceDetail: $"source:{id}",
            TotalPulls: totalPulls,
            TotalReward: meanReward * totalPulls,
            MeanReward: meanReward,
            RewardVariance: rewardVariance,
            CreatedAt: DateTimeOffset.UnixEpoch.AddMinutes(createdAtOffsetMinutes),
            LastPulledAt: totalPulls == 0 ? null : DateTimeOffset.UnixEpoch.AddHours(1));
    }
}
