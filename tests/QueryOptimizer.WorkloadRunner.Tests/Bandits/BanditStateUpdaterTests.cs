using QueryOptimizer.WorkloadRunner.Bandits;

namespace QueryOptimizer.WorkloadRunner.Tests.Bandits;

internal static class BanditStateUpdaterTests
{
    public static void Run()
    {
        ComputeRewardClipsRegressionsToZero();
        ComputeRewardReturnsRelativeImprovement();
        UpdateMovesMeanTowardObservedRewardAndReducesVariance();
        UpdateCarriesPriorObservedStateForward();
    }

    private static void ComputeRewardClipsRegressionsToZero()
    {
        var reward = BanditStateUpdater.ComputeReward(new BanditObservation("p1", BaselineMs: 100, CandidateMs: 125));

        TestAssert.Equal(0.0, reward);
    }

    private static void ComputeRewardReturnsRelativeImprovement()
    {
        var reward = BanditStateUpdater.ComputeReward(new BanditObservation("p1", BaselineMs: 100, CandidateMs: 75));

        TestAssert.Equal(0.25, reward);
    }

    private static void UpdateMovesMeanTowardObservedRewardAndReducesVariance()
    {
        var arm = new BanditArm(
            CandidateId: new CandidateId("candidate"),
            TemplateFingerprint: "template",
            SqlText: "select 1",
            SourceDetail: "rule:test",
            TotalPulls: 0,
            TotalReward: 0,
            MeanReward: 0.5,
            RewardVariance: 1.0,
            CreatedAt: DateTimeOffset.UnixEpoch,
            LastPulledAt: null);
        var observations = new[]
        {
            new BanditObservation("p1", BaselineMs: 100, CandidateMs: 80),
            new BanditObservation("p2", BaselineMs: 100, CandidateMs: 90)
        };

        var state = BanditStateUpdater.Update(arm, observations, BanditOptions.Default, DateTimeOffset.UnixEpoch.AddHours(1));

        TestAssert.Equal(2, state.TotalPulls);
        TestAssert.Equal(0.30, state.TotalReward, precision: 10);
        TestAssert.True(state.MeanReward > 0.15, "Posterior mean should be greater than the observed mean.");
        TestAssert.True(state.MeanReward < 0.5, "Posterior mean should move down from the prior.");
        TestAssert.True(state.RewardVariance < 1.0, "Posterior variance should shrink.");
        TestAssert.Equal(DateTimeOffset.UnixEpoch.AddHours(1), state.LastPulledAt);
    }

    private static void UpdateCarriesPriorObservedStateForward()
    {
        var arm = new BanditArm(
            CandidateId: new CandidateId("candidate"),
            TemplateFingerprint: "template",
            SqlText: "select 1",
            SourceDetail: "rule:test",
            TotalPulls: 2,
            TotalReward: 0.30,
            MeanReward: 0.20,
            RewardVariance: 0.10,
            CreatedAt: DateTimeOffset.UnixEpoch,
            LastPulledAt: DateTimeOffset.UnixEpoch.AddMinutes(1));

        var state = BanditStateUpdater.Update(
            arm,
            new[] { new BanditObservation("p3", BaselineMs: 100, CandidateMs: 50) },
            BanditOptions.Default,
            DateTimeOffset.UnixEpoch.AddHours(2));

        TestAssert.Equal(3, state.TotalPulls);
        TestAssert.Equal(0.80, state.TotalReward, precision: 10);
        TestAssert.True(state.MeanReward > 0.20, "Posterior mean should increase after a strong observation.");
    }
}
