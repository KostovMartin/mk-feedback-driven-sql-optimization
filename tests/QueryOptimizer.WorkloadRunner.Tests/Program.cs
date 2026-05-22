using QueryOptimizer.WorkloadRunner.Tests.Bandits;
using QueryOptimizer.WorkloadRunner.Tests;

try
{
    BanditPolicyTests.Run();
    BanditStateUpdaterTests.Run();
    CandidateGenerationMetadataTests.Run();
    Console.WriteLine("QueryOptimizer.WorkloadRunner.Tests: all tests passed.");
    return 0;
}
catch (Exception ex)
{
    Console.Error.WriteLine(ex);
    return 1;
}
