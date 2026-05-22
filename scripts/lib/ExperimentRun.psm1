$ErrorActionPreference = "Stop"

function Get-RepositoryRoot {
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
    }

    return (Get-Location).Path
}

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

function Initialize-LocalDockerConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string] $RepositoryRoot
    )

    if ([string]::IsNullOrWhiteSpace($env:DOCKER_CONFIG)) {
        $localDockerConfig = Join-Path $RepositoryRoot ".docker"
        New-Item -ItemType Directory -Force -Path $localDockerConfig | Out-Null
        $env:DOCKER_CONFIG = $localDockerConfig
    }
}

function Get-OptionalCommandOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FilePath,

        [Parameter(Mandatory = $true)]
        [string[]] $Arguments
    )

    try {
        $output = & $FilePath @Arguments 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$output)) {
            return (($output | Out-String).Trim())
        }
    }
    catch {
        return "unknown"
    }

    return "unknown"
}

function Set-ExperimentHostMetadata {
    try {
        $processor = Get-CimInstance Win32_Processor | Select-Object -First 1
        $computer = Get-CimInstance Win32_ComputerSystem
        $os = Get-CimInstance Win32_OperatingSystem

        $env:HOST_CPU_MODEL = $processor.Name
        $env:HOST_LOGICAL_PROCESSOR_COUNT = [string]($processor.NumberOfLogicalProcessors)
        $env:HOST_TOTAL_MEMORY_BYTES = [string]([int64]$computer.TotalPhysicalMemory)
        $env:HOST_OS_DESCRIPTION = "$($os.Caption) $($os.Version) build $($os.BuildNumber)"
    }
    catch {
        $env:HOST_CPU_MODEL = "unknown"
        $env:HOST_LOGICAL_PROCESSOR_COUNT = "unknown"
        $env:HOST_TOTAL_MEMORY_BYTES = "unknown"
        $env:HOST_OS_DESCRIPTION = "unknown"
    }

    $env:DOCKER_VERSION = Get-OptionalCommandOutput -FilePath "docker" -Arguments @("version", "--format", "{{.Server.Version}}")
    $env:DOCKER_COMPOSE_VERSION = Get-OptionalCommandOutput -FilePath "docker" -Arguments @("compose", "version", "--short")
}

function Assert-SafeRunId {
    param(
        [Parameter(Mandatory = $true)]
        [string] $RunId
    )

    if ([string]::IsNullOrWhiteSpace($RunId)) {
        throw "RunId must be a non-empty string."
    }

    if ($RunId.Contains("..") -or $RunId.Contains("\") -or $RunId.Contains("/")) {
        throw "RunId must not contain path separators or traversal segments."
    }

    if ($RunId -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]*$") {
        throw "RunId contains unsupported characters. Use letters, digits, dot, underscore, and hyphen only."
    }
}

function Copy-RawRunDataItem {
    param(
        [Parameter(Mandatory = $true)]
        [string] $SourceRoot,

        [Parameter(Mandatory = $true)]
        [string] $StagingRoot,

        [Parameter(Mandatory = $true)]
        [string] $RelativePath
    )

    $sourcePath = Join-Path $SourceRoot $RelativePath
    $destinationPath = Join-Path $StagingRoot $RelativePath
    $destinationDirectory = Split-Path -Parent $destinationPath
    New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
}

function New-RawRunDataBundle {
    param(
        [Parameter(Mandatory = $true)]
        [string] $RunId,

        [Parameter(Mandatory = $true)]
        [string] $ExportDirectory,

        [Parameter(Mandatory = $true)]
        [string] $Corpus,

        [Parameter(Mandatory = $true)]
        [string] $Model
    )

    foreach ($value in @($ExportDirectory, $Corpus, $Model)) {
        if ([string]::IsNullOrWhiteSpace($value)) {
            throw "New-RawRunDataBundle parameters must be non-empty strings."
        }
    }
    Assert-SafeRunId -RunId $RunId

    $repositoryRoot = Get-RepositoryRoot
    $resolvedExportDirectory = Resolve-Path -LiteralPath $ExportDirectory -ErrorAction Stop
    $artifactDirectory = Join-Path $repositoryRoot "experiment-artifacts"
    New-Item -ItemType Directory -Force -Path $artifactDirectory | Out-Null

    $zipPath = Join-Path $artifactDirectory "$RunId-raw-run-data.zip"
    $checksumPath = Join-Path $artifactDirectory "$RunId-SHA256SUMS.txt"

    foreach ($path in @($zipPath, $checksumPath)) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Force
        }
    }

    $requiredFiles = @(
        "manifest.json",
        "workload_case_results.csv",
        "benchmark_runs.csv",
        "candidates.csv",
        "decisions.csv",
        "equivalence_checks.csv",
        "invocations.csv",
        "query_templates.csv"
    )
    $optionalFiles = @(
        "pool_summary.csv",
        "bandit_state.csv",
        "controlled_analysis_summary.json",
        "controlled_candidate_source_summary.csv",
        "controlled_pair_summary.csv",
        "controlled_template_summary.csv",
        "controlled_hypothesis_summary.md",
        "job-imdb-resource-manifest.json"
    )

    $missingRequiredFiles = @()
    foreach ($fileName in $requiredFiles) {
        $path = Join-Path $resolvedExportDirectory.Path $fileName
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            $missingRequiredFiles += $fileName
        }
    }

    if ($missingRequiredFiles.Count -gt 0) {
        throw "Missing required raw run-data files under export directory $($resolvedExportDirectory.Path): $($missingRequiredFiles -join ', ')"
    }

    $stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) "experiment-raw-run-data-$RunId-$([guid]::NewGuid().ToString('N'))"
    try {
        New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

        foreach ($fileName in $requiredFiles) {
            Copy-RawRunDataItem -SourceRoot $resolvedExportDirectory.Path -StagingRoot $stagingRoot -RelativePath $fileName
        }

        foreach ($fileName in $optionalFiles) {
            $path = Join-Path $resolvedExportDirectory.Path $fileName
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                Copy-RawRunDataItem -SourceRoot $resolvedExportDirectory.Path -StagingRoot $stagingRoot -RelativePath $fileName
            }
        }

        $metricsDirectory = Join-Path $resolvedExportDirectory.Path "metrics"
        if (Test-Path -LiteralPath $metricsDirectory -PathType Container) {
            Copy-Item -LiteralPath $metricsDirectory -Destination (Join-Path $stagingRoot "metrics") -Recurse -Force
        }

        $workloadSnapshotDirectory = Join-Path $resolvedExportDirectory.Path "workload-snapshot"
        if (Test-Path -LiteralPath $workloadSnapshotDirectory -PathType Container) {
            $safeWorkloadFiles = Get-ChildItem -LiteralPath $workloadSnapshotDirectory -Recurse -File |
                Where-Object { $_.Extension -in @(".json", ".csv", ".md", ".txt") }
            foreach ($file in $safeWorkloadFiles) {
                $relativePath = $file.FullName.Substring($resolvedExportDirectory.Path.Length + 1)
                Copy-RawRunDataItem -SourceRoot $resolvedExportDirectory.Path -StagingRoot $stagingRoot -RelativePath $relativePath
            }
        }

        $stagedRawRunDataPaths = @(Get-ChildItem -LiteralPath $stagingRoot -Force)
        if ($stagedRawRunDataPaths.Count -eq 0) {
            throw "No raw run-data files staged from export directory: $($resolvedExportDirectory.Path)"
        }

        Compress-Archive -LiteralPath $stagedRawRunDataPaths.FullName -DestinationPath $zipPath -CompressionLevel Optimal

        $hashLines = @()
        foreach ($path in @($zipPath)) {
            $hash = Get-FileHash -LiteralPath $path -Algorithm SHA256
            $hashLines += "$($hash.Hash)  $(Split-Path -Leaf $path)"
        }
        $hashLines | Set-Content -LiteralPath $checksumPath -Encoding ASCII
    }
    finally {
        if (Test-Path -LiteralPath $stagingRoot) {
            Remove-Item -LiteralPath $stagingRoot -Recurse -Force
        }
    }

    Write-Host "Raw run-data bundle written to $zipPath"
}

function Invoke-CorpusMainRun {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Model,

        [Parameter(Mandatory = $true)]
        [string] $RunId,

        [Parameter(Mandatory = $true)]
        [string] $ScriptName,

        [Parameter(Mandatory = $true)]
        [string] $ExportDirectory,

        [Parameter(Mandatory = $true)]
        [string] $Corpus,

        [hashtable] $AdditionalParameters = @{}
    )

    if ([string]::IsNullOrWhiteSpace($Model)) {
        throw "-Model must be a non-empty string."
    }

    $repositoryRoot = Get-RepositoryRoot
    $scriptPath = Join-Path $repositoryRoot "scripts\lib\$ScriptName"
    $previousDefaultModelExists = Test-Path Env:DEFAULT_MODEL
    $previousDefaultModel = $env:DEFAULT_MODEL
    $env:DEFAULT_MODEL = $Model

    Push-Location -LiteralPath $repositoryRoot
    try {
        Initialize-LocalDockerConfig -RepositoryRoot $repositoryRoot
        $scriptParameters = @{
            CandidateSource = "mixed"
            Model = $Model
            RunId = $RunId
            Monitoring = $true
        }
        foreach ($key in $AdditionalParameters.Keys) {
            $scriptParameters[$key] = $AdditionalParameters[$key]
        }
        & $scriptPath @scriptParameters
        New-RawRunDataBundle `
            -RunId $RunId `
            -ExportDirectory $ExportDirectory `
            -Corpus $Corpus `
            -Model $Model
    }
    finally {
        try {
            if ($previousDefaultModelExists) {
                $env:DEFAULT_MODEL = $previousDefaultModel
            }
            else {
                Remove-Item Env:DEFAULT_MODEL -ErrorAction SilentlyContinue
            }
        }
        finally {
            Pop-Location
        }
    }
}

function Invoke-TpchMainRun {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Model
    )

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $runId = "tpch-sf1-mixed-$stamp"
    $repositoryRoot = Get-RepositoryRoot
    Invoke-CorpusMainRun `
        -Model $Model `
        -RunId $runId `
        -ScriptName "run-tpch-evaluation.ps1" `
        -ExportDirectory (Join-Path $repositoryRoot "experiment-runs\tpch-evaluation\$runId") `
        -Corpus "tpch" `
        -AdditionalParameters @{ ScaleFactor = "1" }
}

function Invoke-RealWorldMainRun {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Model
    )

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $runId = "real-world-sf1-mixed-$stamp"
    $repositoryRoot = Get-RepositoryRoot
    Invoke-CorpusMainRun `
        -Model $Model `
        -RunId $runId `
        -ScriptName "run-real-world-evaluation.ps1" `
        -ExportDirectory (Join-Path $repositoryRoot "experiment-runs\real-world-evaluation\$runId") `
        -Corpus "real-world" `
        -AdditionalParameters @{ ScaleFactor = "1" }
}

function Invoke-JobImdbMainRun {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Model
    )

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $runId = "job-imdb-mixed-$stamp"
    $repositoryRoot = Get-RepositoryRoot
    Invoke-CorpusMainRun `
        -Model $Model `
        -RunId $runId `
        -ScriptName "run-job-imdb-evaluation.ps1" `
        -ExportDirectory (Join-Path $repositoryRoot "experiment-runs\job-imdb-evaluation\$runId") `
        -Corpus "job-imdb"
}

Export-ModuleMember -Function Invoke-TpchMainRun, Invoke-RealWorldMainRun, Invoke-JobImdbMainRun, New-RawRunDataBundle
