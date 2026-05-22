using System.Reflection;

namespace QueryOptimizer.WorkloadRunner.Tests;

internal static class CandidateGenerationMetadataTests
{
    public static void Run()
    {
        RuleCertificationRequiresRuleSourceAndAppliedRules();
    }

    private static void RuleCertificationRequiresRuleSourceAndAppliedRules()
    {
        var method = LoadRuleCertifiedMethod();

        TestAssert.Equal(true, Invoke(method, "rule", ["count_gt_zero_to_exists"]));
        TestAssert.Equal(false, Invoke(method, "rule", []));
        TestAssert.Equal(false, Invoke(method, "llm", []));
        TestAssert.Equal(false, Invoke(method, "llm", ["count_gt_zero_to_exists"]));
    }

    private static MethodInfo LoadRuleCertifiedMethod()
    {
        var type = Type.GetType(
            "QueryOptimizer.WorkloadRunner.CandidateGenerationMetadata, QueryOptimizer.WorkloadRunner");
        TestAssert.NotNull(type);

        var method = type!.GetMethod(
            "IsRuleCertified",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
        TestAssert.NotNull(method);

        return method!;
    }

    private static bool Invoke(MethodInfo method, string sourceType, string[] appliedRules)
    {
        return (bool)method.Invoke(null, [sourceType, appliedRules])!;
    }
}
