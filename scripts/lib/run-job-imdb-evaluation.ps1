param(
    [ValidateSet("baseline", "rule", "llm", "mixed")]
    [string] $CandidateSource = "mixed",
    [string] $Model = "",
    [string] $RunId = "",
    [string] $QuerySourcePath = "queries\job-imdb",
    [string] $DataPath = "data\job-imdb",
    [string] $WorkloadRoot = "benchmark-data\job-imdb\workload",
    [string] $OutputRoot = "experiment-runs\job-imdb-evaluation",
    [int] $SearchPairs = 30,
    [int] $HeldOutPairs = 30,
    [string[]] $QueryIds = @(),
    [switch] $Monitoring,
    [switch] $GpuMonitoring,
    [string] $ExternalPowerCsv = "",
    [switch] $ReuseTargetDatabase,
    [switch] $CleanTargetDatabase,
    [switch] $SkipAnalysis
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "monitoring-functions.ps1")

if ($ReuseTargetDatabase -and $CleanTargetDatabase) {
    throw "Use either -ReuseTargetDatabase or -CleanTargetDatabase, not both."
}

if ($GpuMonitoring -and -not $Monitoring) {
    throw "-GpuMonitoring requires -Monitoring."
}

if (-not [string]::IsNullOrWhiteSpace($ExternalPowerCsv) -and -not $Monitoring) {
    throw "-ExternalPowerCsv requires -Monitoring."
}

$reuseTargetDatabaseEffective = -not $CleanTargetDatabase

if ([string]::IsNullOrWhiteSpace($env:DOCKER_CONFIG)) {
    $localDockerConfig = Join-Path (Get-Location) ".docker"
    New-Item -ItemType Directory -Force -Path $localDockerConfig | Out-Null
    $env:DOCKER_CONFIG = $localDockerConfig
}

if ([string]::IsNullOrWhiteSpace($RunId)) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $RunId = "job-imdb-$CandidateSource-$stamp"
}

if ($CandidateSource -in @("llm", "mixed") -and [string]::IsNullOrWhiteSpace($Model)) {
    throw "-Model must be supplied for LLM-backed runs."
}

$JOB_IMDB_REQUIRED_TABLES = @(
    "aka_name",
    "aka_title",
    "cast_info",
    "char_name",
    "comp_cast_type",
    "company_name",
    "company_type",
    "complete_cast",
    "info_type",
    "keyword",
    "kind_type",
    "link_type",
    "movie_companies",
    "movie_info",
    "movie_info_idx",
    "movie_keyword",
    "movie_link",
    "name",
    "person_info",
    "role_type",
    "title"
)

$resolvedQuerySource = Resolve-Path -LiteralPath $QuerySourcePath -ErrorAction SilentlyContinue
if ($null -eq $resolvedQuerySource) {
    throw "Missing JOB/IMDB query source path: $QuerySourcePath. Run scripts\lib\prepare-job-imdb-resources.ps1 to download the official JOB SQL files and IMDB CSV archive before running the pilot."
}

$queryFiles = @(Get-ChildItem -LiteralPath $resolvedQuerySource.Path -Filter "*.sql" -File -ErrorAction SilentlyContinue)
if ($queryFiles.Count -eq 0) {
    throw "No JOB/IMDB SQL files found in $QuerySourcePath. Run scripts\lib\prepare-job-imdb-resources.ps1 to download the official JOB SQL files and IMDB CSV archive before running the pilot."
}

$resolvedDataPath = Resolve-Path -LiteralPath $DataPath -ErrorAction SilentlyContinue
if ($null -eq $resolvedDataPath) {
    throw "Missing JOB/IMDB data path: $DataPath. Run scripts\lib\prepare-job-imdb-resources.ps1 to download schema.sql and the official IMDB CSV files before running the pilot."
}

if (-not (Test-Path -LiteralPath (Join-Path $resolvedDataPath.Path "schema.sql"))) {
    throw "Missing JOB/IMDB schema file: $(Join-Path $DataPath "schema.sql"). Run scripts\lib\prepare-job-imdb-resources.ps1 to download the JOB schema."
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$resolvedOutputRoot = (Resolve-Path -LiteralPath $OutputRoot).Path

$prepareArguments = @{
    QuerySourcePath = $resolvedQuerySource.Path
    OutputRoot = $WorkloadRoot
    SearchPairs = $SearchPairs
    HeldOutPairs = $HeldOutPairs
    QueryIds = $QueryIds
    Clean = $true
}

& (Join-Path $PSScriptRoot "prepare-job-imdb-workload.ps1") @prepareArguments
if ($LASTEXITCODE -ne 0) {
    throw "JOB/IMDB workload preparation failed with exit code $LASTEXITCODE."
}

$resolvedWorkloadRoot = (Resolve-Path -LiteralPath $WorkloadRoot).Path

function Get-OptionalCommandOutput {
    param(
        [string] $FilePath,
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

function Get-GitMetadata {
    $commit = Get-OptionalCommandOutput "git" @("rev-parse", "HEAD")
    $branch = Get-OptionalCommandOutput "git" @("branch", "--show-current")
    $statusShort = "unknown"
    $isDirty = $false

    try {
        $statusOutput = & git status --short 2>$null
        if ($LASTEXITCODE -eq 0) {
            $statusShort = (($statusOutput | Out-String).Trim())
            $isDirty = -not [string]::IsNullOrWhiteSpace($statusShort)
        }
    }
    catch {
        $statusShort = "unknown"
        $isDirty = $false
    }

    return [pscustomobject]@{
        Commit = $commit
        Branch = $branch
        IsDirty = $isDirty
        StatusShort = $statusShort
    }
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

$env:JOB_IMDB_RESULT_RUN_ID = $RunId
$env:JOB_IMDB_RESULTS_HOST_PATH = $resolvedOutputRoot
$env:JOB_IMDB_WORKLOAD_HOST_PATH = $resolvedWorkloadRoot
$env:JOB_IMDB_OPTIMIZER_PROFILE = "job-imdb"
$env:JOB_IMDB_RUN_HELD_OUT_EVALUATION = if ($CandidateSource -eq "baseline") { "false" } else { "true" }
$env:JOB_IMDB_BENCHMARK_ITERATIONS = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_BENCHMARK_ITERATIONS)) { "1" } else { $env:JOB_IMDB_BENCHMARK_ITERATIONS }
$env:JOB_IMDB_MIN_PROMOTION_PAIRS = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_MIN_PROMOTION_PAIRS)) { "30" } else { $env:JOB_IMDB_MIN_PROMOTION_PAIRS }
$env:JOB_IMDB_VALIDATION_PARAMETER_SET_LIMIT = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_VALIDATION_PARAMETER_SET_LIMIT)) { "1" } else { $env:JOB_IMDB_VALIDATION_PARAMETER_SET_LIMIT }
$env:JOB_IMDB_CAPTURE_EXPLAIN_PLANS = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_CAPTURE_EXPLAIN_PLANS)) { "true" } else { $env:JOB_IMDB_CAPTURE_EXPLAIN_PLANS }
$env:JOB_IMDB_MODEL_TIMEOUT_SECONDS = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_MODEL_TIMEOUT_SECONDS)) { "600" } else { $env:JOB_IMDB_MODEL_TIMEOUT_SECONDS }
$env:JOB_IMDB_REWRITE_SERVICE_HTTP_TIMEOUT_SECONDS = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_REWRITE_SERVICE_HTTP_TIMEOUT_SECONDS)) { "1800" } else { $env:JOB_IMDB_REWRITE_SERVICE_HTTP_TIMEOUT_SECONDS }
$env:JOB_IMDB_EQUIVALENCE_TIMEOUT_MS = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_EQUIVALENCE_TIMEOUT_MS)) { "900000" } else { $env:JOB_IMDB_EQUIVALENCE_TIMEOUT_MS }
$env:JOB_IMDB_EQUIVALENCE_MAX_ROWS_FULL_COMPARE = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_EQUIVALENCE_MAX_ROWS_FULL_COMPARE)) { "1000000" } else { $env:JOB_IMDB_EQUIVALENCE_MAX_ROWS_FULL_COMPARE }
$env:JOB_IMDB_PROMOTION_ALPHA = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_PROMOTION_ALPHA)) { "0.05" } else { $env:JOB_IMDB_PROMOTION_ALPHA }
$env:JOB_IMDB_PROMOTION_MIN_IMPROVEMENT_PCT = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_PROMOTION_MIN_IMPROVEMENT_PCT)) { "2.0" } else { $env:JOB_IMDB_PROMOTION_MIN_IMPROVEMENT_PCT }
$env:JOB_IMDB_PROMOTION_MAX_CANDIDATE_CV = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_PROMOTION_MAX_CANDIDATE_CV)) { "0.3" } else { $env:JOB_IMDB_PROMOTION_MAX_CANDIDATE_CV }
$env:JOB_IMDB_BANDIT_STRATEGY = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_BANDIT_STRATEGY)) { "thompson" } else { $env:JOB_IMDB_BANDIT_STRATEGY }
$env:JOB_IMDB_BANDIT_RANDOM_SEED = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_BANDIT_RANDOM_SEED)) { "12345" } else { $env:JOB_IMDB_BANDIT_RANDOM_SEED }
$env:JOB_IMDB_BANDIT_OBSERVATION_VARIANCE = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_BANDIT_OBSERVATION_VARIANCE)) { "0.1" } else { $env:JOB_IMDB_BANDIT_OBSERVATION_VARIANCE }
$env:JOB_IMDB_UCB1_EXPLORATION_COEFFICIENT = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_UCB1_EXPLORATION_COEFFICIENT)) { "1.4142135623730951" } else { $env:JOB_IMDB_UCB1_EXPLORATION_COEFFICIENT }
$env:JOB_IMDB_BACKGROUND_OPTIMIZER_ROUNDS = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_BACKGROUND_OPTIMIZER_ROUNDS)) { "1" } else { $env:JOB_IMDB_BACKGROUND_OPTIMIZER_ROUNDS }
$env:JOB_IMDB_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT = if ([string]::IsNullOrWhiteSpace($env:JOB_IMDB_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT)) { "30" } else { $env:JOB_IMDB_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT }

switch ($CandidateSource) {
    "baseline" {
        $env:JOB_IMDB_WORKLOAD_MANIFEST_FILE = "/app/job-imdb/job-imdb-baseline-corpus.json"
        $env:JOB_IMDB_ENABLE_RULES = "false"
        $env:JOB_IMDB_ENABLE_LLM = "false"
        $env:JOB_IMDB_MAX_LLM_CANDIDATES = "0"
    }
    "rule" {
        $env:JOB_IMDB_WORKLOAD_MANIFEST_FILE = "/app/job-imdb/job-imdb-rule-corpus.json"
        $env:JOB_IMDB_ENABLE_RULES = "true"
        $env:JOB_IMDB_ENABLE_LLM = "false"
        $env:JOB_IMDB_MAX_LLM_CANDIDATES = "0"
    }
    "llm" {
        $env:JOB_IMDB_WORKLOAD_MANIFEST_FILE = "/app/job-imdb/job-imdb-local-llm-corpus.json"
        $env:JOB_IMDB_ENABLE_RULES = "false"
        $env:JOB_IMDB_ENABLE_LLM = "true"
        $env:JOB_IMDB_MAX_LLM_CANDIDATES = "1"
        $env:DEFAULT_MODEL = $Model
    }
    "mixed" {
        $env:JOB_IMDB_WORKLOAD_MANIFEST_FILE = "/app/job-imdb/job-imdb-mixed-corpus.json"
        $env:JOB_IMDB_ENABLE_RULES = "true"
        $env:JOB_IMDB_ENABLE_LLM = "true"
        $env:JOB_IMDB_MAX_LLM_CANDIDATES = "1"
        $env:DEFAULT_MODEL = $Model
    }
}

$composeProfiles = @()
if ($CandidateSource -in @("llm", "mixed")) {
    $composeProfiles += "llm-local"
}
if ($Monitoring -and $GpuMonitoring) {
    $composeProfiles += "gpu-monitoring"
}

$compose = @("compose")
foreach ($profile in $composeProfiles) {
    $compose += @("--profile", $profile)
}
$compose += @("-f", "docker-compose.yml", "-f", "docker-compose.job-imdb.yml")
if ($Monitoring) {
    $compose += @("-f", "docker-compose.monitoring.yml")
}

function Invoke-DockerCompose {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]] $Arguments
    )

    $dockerArgs = @($compose + $Arguments)
    & docker @dockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Get-ComposeProjectName {
    if (-not [string]::IsNullOrWhiteSpace($env:COMPOSE_PROJECT_NAME)) {
        return $env:COMPOSE_PROJECT_NAME
    }

    return ((Split-Path -Leaf (Get-Location)).ToLowerInvariant() -replace '[^a-z0-9_-]', '')
}

function Remove-DockerVolumeIfExists {
    param([string] $Name)

    $existing = docker volume ls --quiet --filter "name=^$Name$"
    if (-not [string]::IsNullOrWhiteSpace($existing)) {
        docker volume rm $Name | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "docker volume rm $Name failed with exit code $LASTEXITCODE."
        }
    }
}

function Reset-ComposeTargetDataVolume {
    $projectName = Get-ComposeProjectName
    Remove-DockerVolumeIfExists -Name "${projectName}_target-db-data"
}

function Reset-ComposeMetadataDataVolume {
    $projectName = Get-ComposeProjectName
    Remove-DockerVolumeIfExists -Name "${projectName}_metadata-db-data"
}

function Reset-ComposeDataVolumes {
    Reset-ComposeTargetDataVolume
    Reset-ComposeMetadataDataVolume
}

function Get-JobImdbSeededTableCount {
    $regclassLiterals = $JOB_IMDB_REQUIRED_TABLES | ForEach-Object { "'public.$_'" }
    $sql = "SELECT count(*) FROM unnest(ARRAY[$($regclassLiterals -join ',')]::text[]) AS table_name WHERE to_regclass(table_name) IS NOT NULL;"
    $dockerArgs = @($compose + @("exec", "-T", "target-db", "psql", "-U", "postgres", "-d", "tpch", "-Atc", $sql))

    $output = & docker @dockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose exec target-db psql failed while checking JOB/IMDB seeded tables with exit code $LASTEXITCODE."
    }

    $lastLine = @($output | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }) | Select-Object -Last 1
    if ($null -eq $lastLine) {
        throw "Could not determine whether the JOB/IMDB target database is already seeded."
    }

    return [int]$lastLine
}

Set-ExperimentHostMetadata

$monitoringStartUtc = $null
$monitoringEndUtc = $null
$monitoringMetricsPath = $null
$prometheusUrl = if ($Monitoring) { Get-MonitoringPrometheusBaseUrl } else { $null }
$grafanaPort = if ([string]::IsNullOrWhiteSpace($env:GRAFANA_PORT)) { "3000" } else { $env:GRAFANA_PORT }
$grafanaUrl = if ($Monitoring) { "http://localhost:$grafanaPort" } else { $null }
$externalPowerArtifactPath = $null

Invoke-DockerCompose build
Invoke-DockerCompose down --remove-orphans
Reset-ComposeMetadataDataVolume
if (-not $reuseTargetDatabaseEffective) {
    Reset-ComposeTargetDataVolume
}

$previousJobImdbSkipTargetLoad = $env:JOB_IMDB_SKIP_TARGET_LOAD
$env:JOB_IMDB_SKIP_TARGET_LOAD = "false"

try {
    if ($Monitoring) {
        $monitoringServices = @("cadvisor", "node-exporter", "prometheus", "grafana")
        if ($GpuMonitoring) {
            $monitoringServices += "dcgm-exporter"
        }
        Invoke-DockerCompose up -d --wait @monitoringServices
        $monitoringStartUtc = (Get-Date).ToUniversalTime()
    }

    if ($CandidateSource -in @("llm", "mixed")) {
        Invoke-DockerCompose up -d --wait target-db metadata-db ollama
    }
    else {
        Invoke-DockerCompose up -d --wait target-db metadata-db
    }

    if ($reuseTargetDatabaseEffective) {
        $seededTableCount = Get-JobImdbSeededTableCount
        if ($seededTableCount -eq $JOB_IMDB_REQUIRED_TABLES.Count) {
            Write-Host "Reusing seeded JOB/IMDB target database; skipping CSV load."
            $env:JOB_IMDB_SKIP_TARGET_LOAD = "true"
        }
        elseif ($seededTableCount -eq 0) {
            Write-Host "No seeded JOB/IMDB target database found; loading CSV files once."
        }
        else {
            throw "The JOB/IMDB target database contains $seededTableCount of $($JOB_IMDB_REQUIRED_TABLES.Count) required tables. Run with -CleanTargetDatabase to recreate the target database."
        }
    }

    Invoke-DockerCompose run --rm data-loader
    Invoke-DockerCompose up -d --wait rewrite-service
    Invoke-DockerCompose run --rm --no-deps workload-runner
    Invoke-DockerCompose run --rm job-imdb-results-exporter

    $exportDirectory = Join-Path $resolvedOutputRoot $RunId
    New-Item -ItemType Directory -Force -Path $exportDirectory | Out-Null
    $workloadSnapshotDirectory = Join-Path $exportDirectory "workload-snapshot"
    if (Test-Path -LiteralPath $workloadSnapshotDirectory) {
        throw "The export directory already contains a workload snapshot: $workloadSnapshotDirectory"
    }
    Copy-Item -LiteralPath $resolvedWorkloadRoot -Destination $workloadSnapshotDirectory -Recurse -Force

    $resourceManifestHostPath = Join-Path $resolvedDataPath.Path "job-imdb-resource-manifest.json"
    $resourceManifestExportPath = $null
    if (Test-Path -LiteralPath $resourceManifestHostPath) {
        $resourceManifestExportPath = "job-imdb-resource-manifest.json"
        Copy-Item `
            -LiteralPath $resourceManifestHostPath `
            -Destination (Join-Path $exportDirectory $resourceManifestExportPath) `
            -Force
    }

    if ($Monitoring) {
        $monitoringEndUtc = (Get-Date).ToUniversalTime()
        & (Join-Path $PSScriptRoot "export-monitoring-window.ps1") `
            -OutputDirectory $exportDirectory `
            -StartUtc $monitoringStartUtc `
            -EndUtc $monitoringEndUtc `
            -PrometheusBaseUrl $prometheusUrl `
            -ExternalPowerCsv $ExternalPowerCsv
        if ($LASTEXITCODE -ne 0) {
            throw "JOB/IMDB monitoring export failed with exit code $LASTEXITCODE."
        }
        $monitoringMetricsPath = "metrics"
        if (-not [string]::IsNullOrWhiteSpace($ExternalPowerCsv)) {
            $externalPowerArtifactPath = "metrics/external-power.csv"
        }
    }

    $monitoringStartUtcString = $null
    if ($null -ne $monitoringStartUtc) {
        $monitoringStartUtcString = $monitoringStartUtc.ToString("o")
    }
    $monitoringEndUtcString = $null
    if ($null -ne $monitoringEndUtc) {
        $monitoringEndUtcString = $monitoringEndUtc.ToString("o")
    }

    $gitMetadata = Get-GitMetadata
    $runManifest = [ordered]@{
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        run_id = $RunId
        corpus = "job-imdb"
        candidate_source = $CandidateSource
        model = if ($CandidateSource -in @("llm", "mixed")) { $Model } else { $null }
        workload_manifest_file = $env:JOB_IMDB_WORKLOAD_MANIFEST_FILE
        query_path = "/app/job-imdb/queries"
        parameter_path = "/app/job-imdb/parameters"
        measurement_policy = "fixed_literal_repeated_measurement"
        search_parameter_sets_per_query = $SearchPairs
        held_out_parameter_sets_per_query = $HeldOutPairs
        query_filter = @($QueryIds)
        target_database_reuse = $reuseTargetDatabaseEffective
        target_database_clean_requested = [bool]$CleanTargetDatabase
        job_imdb_skip_target_load = $env:JOB_IMDB_SKIP_TARGET_LOAD
        workload_snapshot_path = "workload-snapshot"
        resource_manifest_path = $resourceManifestExportPath
        benchmark_iterations = $env:JOB_IMDB_BENCHMARK_ITERATIONS
        minimum_promotion_pairs = $env:JOB_IMDB_MIN_PROMOTION_PAIRS
        promotion_alpha = $env:JOB_IMDB_PROMOTION_ALPHA
        promotion_min_improvement_pct = $env:JOB_IMDB_PROMOTION_MIN_IMPROVEMENT_PCT
        promotion_max_candidate_cv = $env:JOB_IMDB_PROMOTION_MAX_CANDIDATE_CV
        bandit_strategy = $env:JOB_IMDB_BANDIT_STRATEGY
        bandit_random_seed = $env:JOB_IMDB_BANDIT_RANDOM_SEED
        bandit_observation_variance = $env:JOB_IMDB_BANDIT_OBSERVATION_VARIANCE
        ucb1_exploration_coefficient = $env:JOB_IMDB_UCB1_EXPLORATION_COEFFICIENT
        background_optimizer_rounds = $env:JOB_IMDB_BACKGROUND_OPTIMIZER_ROUNDS
        background_optimizer_parameter_limit = $env:JOB_IMDB_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT
        validation_parameter_set_limit = $env:JOB_IMDB_VALIDATION_PARAMETER_SET_LIMIT
        equivalence_max_rows_full_compare = $env:JOB_IMDB_EQUIVALENCE_MAX_ROWS_FULL_COMPARE
        capture_explain_plans = $env:JOB_IMDB_CAPTURE_EXPLAIN_PLANS
        query_source_path = $resolvedQuerySource.Path
        data_path = $resolvedDataPath.Path
        workload_host_path = $resolvedWorkloadRoot
        docker_version = $env:DOCKER_VERSION
        docker_compose_version = $env:DOCKER_COMPOSE_VERSION
        host_cpu_model = $env:HOST_CPU_MODEL
        host_logical_processor_count = $env:HOST_LOGICAL_PROCESSOR_COUNT
        host_total_memory_bytes = $env:HOST_TOTAL_MEMORY_BYTES
        host_os_description = $env:HOST_OS_DESCRIPTION
        monitoring_enabled = [bool]$Monitoring
        gpu_monitoring_requested = [bool]$GpuMonitoring
        prometheus_url = $prometheusUrl
        grafana_url = $grafanaUrl
        monitoring_start_utc = $monitoringStartUtcString
        monitoring_end_utc = $monitoringEndUtcString
        monitoring_metrics_path = $monitoringMetricsPath
        external_power_artifact_path = $externalPowerArtifactPath
        git_commit = $gitMetadata.Commit
        git_branch = $gitMetadata.Branch
        git_worktree_dirty = $gitMetadata.IsDirty
        git_status_short = $gitMetadata.StatusShort
    }
    $runManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $exportDirectory "manifest.json") -Encoding UTF8

    if (-not $SkipAnalysis) {
        & (Join-Path $PSScriptRoot "analyze-results.ps1") `
            -InputDirectory $exportDirectory `
            -Alpha ([double]$env:JOB_IMDB_PROMOTION_ALPHA) `
            -ImprovementThresholdPct ([double]$env:JOB_IMDB_PROMOTION_MIN_IMPROVEMENT_PCT)
        if ($LASTEXITCODE -ne 0) {
            throw "JOB/IMDB post-hoc analysis failed with exit code $LASTEXITCODE."
        }
    }

    Write-Host "JOB/IMDB evaluation exports written to $exportDirectory"
}
finally {
    docker @compose down --remove-orphans
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "docker compose down failed with exit code $LASTEXITCODE."
    }
    Reset-ComposeMetadataDataVolume
    if (-not $reuseTargetDatabaseEffective) {
        Reset-ComposeTargetDataVolume
    }

    if ($null -eq $previousJobImdbSkipTargetLoad) {
        Remove-Item Env:JOB_IMDB_SKIP_TARGET_LOAD -ErrorAction SilentlyContinue
    }
    else {
        $env:JOB_IMDB_SKIP_TARGET_LOAD = $previousJobImdbSkipTargetLoad
    }
}
