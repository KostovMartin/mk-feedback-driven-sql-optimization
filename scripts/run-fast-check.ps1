$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Command,

        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]] $Arguments
    )

    $displayCommand = (@($Command) + $Arguments) -join " "
    Write-Host ">> $displayCommand"
    & $Command @Arguments

    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $displayCommand"
    }
}

function Test-PowerShellSyntax {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    if (Test-Path -LiteralPath $Path) {
        Write-Host ">> PowerShell syntax check $Path"
        $null = [scriptblock]::Create((Get-Content -Raw -LiteralPath $Path))
    } else {
        Write-Host "Skipping missing PowerShell syntax check $Path"
    }
}

Push-Location -LiteralPath $RepoRoot
try {
    $RuffTargets = @("app")
    if (Test-Path -LiteralPath "src\rewrite-service\tests") {
        $RuffTargets += "tests"
    }

    Invoke-Native uv --directory src\rewrite-service run ruff check @RuffTargets
    Invoke-Native uv --directory src\rewrite-service run mypy app
    if (Test-Path -LiteralPath "src\rewrite-service\tests") {
        Invoke-Native uv --directory src\rewrite-service run pytest
    } else {
        Write-Host "Skipping rewrite-service pytest; src\rewrite-service\tests is not present."
    }

    Invoke-Native dotnet restore src\QueryOptimizer.WorkloadRunner\QueryOptimizer.WorkloadRunner.csproj
    Invoke-Native dotnet build src\QueryOptimizer.WorkloadRunner\QueryOptimizer.WorkloadRunner.csproj --no-restore

    $RunnerTestProject = "tests\QueryOptimizer.WorkloadRunner.Tests\QueryOptimizer.WorkloadRunner.Tests.csproj"
    if (Test-Path -LiteralPath $RunnerTestProject) {
        Invoke-Native dotnet restore $RunnerTestProject
        Invoke-Native dotnet run --project $RunnerTestProject --no-restore
    } else {
        Write-Host "Skipping workload-runner tests; $RunnerTestProject is not present."
    }

    Invoke-Native python -m py_compile tpch-generator\generate-duckdb-tpch.py tpch-generator\tpch_parameterized_workload.py tpch-generator\real_world_workload.py tpch-generator\job_imdb_workload.py

    $PowerShellScripts = @(
        "scripts\run-fast-check.ps1",
        "scripts\run-main-run.ps1",
        "scripts\lib\ExperimentRun.psm1",
        "scripts\lib\analyze-results.ps1",
        "scripts\lib\export-monitoring-window.ps1",
        "scripts\lib\monitoring-functions.ps1",
        "scripts\lib\prepare-job-imdb-resources.ps1",
        "scripts\lib\prepare-job-imdb-workload.ps1",
        "scripts\lib\run-job-imdb-evaluation.ps1",
        "scripts\lib\run-real-world-evaluation.ps1",
        "scripts\lib\run-tpch-evaluation.ps1"
    )

    foreach ($ScriptPath in $PowerShellScripts) {
        Test-PowerShellSyntax $ScriptPath
    }

    Write-Host "Fast check completed."
} finally {
    Pop-Location
}
