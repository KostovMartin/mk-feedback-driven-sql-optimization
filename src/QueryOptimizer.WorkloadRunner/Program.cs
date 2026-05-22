using System;
using System.Threading;
using System.Threading.Tasks;

namespace QueryOptimizer.WorkloadRunner;

internal static class Program
{
    internal static async Task<int> Main()
    {
        try
        {
            var runner = new ControlledWorkloadRunner(RunnerOptions.FromEnvironment());
            await runner.RunAsync(CancellationToken.None);
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            return 1;
        }
    }
}
