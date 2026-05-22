namespace QueryOptimizer.WorkloadRunner;

internal static class CandidateGenerationMetadata
{
    public static bool IsRuleCertified(string sourceType, IReadOnlyCollection<string> appliedRules)
    {
        return string.Equals(sourceType, "rule", StringComparison.OrdinalIgnoreCase)
            && appliedRules.Count > 0;
    }
}
