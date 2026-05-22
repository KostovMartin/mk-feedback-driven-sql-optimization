namespace QueryOptimizer.WorkloadRunner.Bandits;

internal enum BanditStrategy
{
    Thompson,
    Ucb1
}

internal enum BanditSelectionReason
{
    InitialPull,
    ThompsonSample,
    UcbScore
}

internal readonly record struct CandidateId(string Value)
{
    public override string ToString()
    {
        return Value;
    }
}

internal sealed record BanditOptions(
    BanditStrategy Strategy,
    double ObservationVariance,
    double UcbExplorationCoefficient)
{
    public static BanditOptions Default { get; } = new(
        BanditStrategy.Thompson,
        ObservationVariance: 0.1,
        UcbExplorationCoefficient: Math.Sqrt(2.0));
}

internal sealed record BanditArm(
    CandidateId CandidateId,
    string TemplateFingerprint,
    string SqlText,
    string SourceDetail,
    int TotalPulls,
    double TotalReward,
    double MeanReward,
    double RewardVariance,
    DateTimeOffset CreatedAt,
    DateTimeOffset? LastPulledAt);

internal sealed record BanditSelection(
    CandidateId CandidateId,
    BanditSelectionReason Reason,
    double? Score);

internal sealed record BanditObservation(
    string ParameterSetId,
    double BaselineMs,
    double CandidateMs);

internal sealed record BanditStateUpdate(
    int TotalPulls,
    double TotalReward,
    double MeanReward,
    double RewardVariance,
    double? UcbScore,
    DateTimeOffset LastPulledAt);
