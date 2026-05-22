param(
    [string] $QuerySourcePath = "queries\job-imdb",
    [string] $OutputRoot = "benchmark-data\job-imdb\workload",
    [int] $SearchPairs = 30,
    [int] $HeldOutPairs = 30,
    [string[]] $QueryIds = @(),
    [switch] $Clean
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

function Resolve-RepoPath {
    param([string] $Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }

    return (Join-Path $repoRoot $Path)
}

$querySource = Resolve-RepoPath -Path $QuerySourcePath
$outputRootPath = Resolve-RepoPath -Path $OutputRoot

if (-not (Test-Path -LiteralPath $querySource)) {
    throw "Missing JOB/IMDB query source path: $querySource. Provide official JOB SQL files such as 1a.sql through 33c.sql."
}

$queryFiles = Get-ChildItem -LiteralPath $querySource -Filter "*.sql" -File
if ($queryFiles.Count -eq 0) {
    throw "No JOB/IMDB SQL files found in $querySource. Expected files such as 1a.sql, 2a.sql, and 33c.sql."
}

$arguments = @(
    "tpch-generator\job_imdb_workload.py",
    "--query-source",
    $querySource,
    "--output-root",
    $outputRootPath,
    "--search-pairs",
    "$SearchPairs",
    "--held-out-pairs",
    "$HeldOutPairs"
)

foreach ($queryId in $QueryIds) {
    if (-not [string]::IsNullOrWhiteSpace($queryId)) {
        $arguments += @("--query-id", $queryId)
    }
}

if ($Clean) {
    $arguments += "--clean"
}

Write-Host ">> python $($arguments -join ' ')"
python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "JOB/IMDB workload preparation failed with exit code $LASTEXITCODE."
}
