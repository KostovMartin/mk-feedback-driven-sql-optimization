param(
    [ValidateSet("baseline", "rule", "llm", "mixed")]
    [string] $CandidateSource = "mixed",
    [string] $ScaleFactor = "1",
    [string] $Model = "",
    [string] $RunId = "",
    [string] $OutputRoot = "experiment-runs\real-world-evaluation",
    [int] $WorkloadLimit = 0,
    [int] $SearchParameterLimit = 0,
    [int] $HeldOutParameterLimit = 0,
    [switch] $Monitoring,
    [switch] $GpuMonitoring,
    [string] $ExternalPowerCsv = "",
    [switch] $SkipAnalysis
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "monitoring-functions.ps1")
. (Join-Path $PSScriptRoot "workload-reduction.ps1")

if ($GpuMonitoring -and -not $Monitoring) {
    throw "-GpuMonitoring requires -Monitoring."
}

if (-not [string]::IsNullOrWhiteSpace($ExternalPowerCsv) -and -not $Monitoring) {
    throw "-ExternalPowerCsv requires -Monitoring."
}

if ([string]::IsNullOrWhiteSpace($env:DOCKER_CONFIG)) {
    $localDockerConfig = Join-Path (Get-Location) ".docker"
    New-Item -ItemType Directory -Force -Path $localDockerConfig | Out-Null
    $env:DOCKER_CONFIG = $localDockerConfig
}

if ([string]::IsNullOrWhiteSpace($RunId)) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $RunId = "real-world-sf$ScaleFactor-$CandidateSource-$stamp"
}

if ($CandidateSource -in @("llm", "mixed") -and [string]::IsNullOrWhiteSpace($Model)) {
    throw "-Model must be supplied for LLM-backed runs."
}

New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$resolvedOutputRoot = (Resolve-Path -LiteralPath $OutputRoot).Path

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

$env:TPCH_SCALE_FACTOR = $ScaleFactor
$env:TPCH_RESULT_RUN_ID = $RunId
$env:TPCH_RESULTS_HOST_PATH = $resolvedOutputRoot
$env:TPCH_QUERIES_PATH = "/app/tpch/real-world/queries"
$env:TPCH_PARAMETERS_PATH = "/app/tpch/real-world/parameters"
$env:TPCH_OPTIMIZER_PROFILE = "real-world-tpch"
$env:TPCH_RUN_HELD_OUT_EVALUATION = if ($CandidateSource -eq "baseline") { "false" } else { "true" }
$env:TPCH_BENCHMARK_ITERATIONS = if ([string]::IsNullOrWhiteSpace($env:TPCH_BENCHMARK_ITERATIONS)) { "1" } else { $env:TPCH_BENCHMARK_ITERATIONS }
$env:TPCH_MIN_PROMOTION_PAIRS = if ([string]::IsNullOrWhiteSpace($env:TPCH_MIN_PROMOTION_PAIRS)) { "30" } else { $env:TPCH_MIN_PROMOTION_PAIRS }
$env:TPCH_VALIDATION_PARAMETER_SET_LIMIT = if ([string]::IsNullOrWhiteSpace($env:TPCH_VALIDATION_PARAMETER_SET_LIMIT)) { "3" } else { $env:TPCH_VALIDATION_PARAMETER_SET_LIMIT }
$env:TPCH_CAPTURE_EXPLAIN_PLANS = if ([string]::IsNullOrWhiteSpace($env:TPCH_CAPTURE_EXPLAIN_PLANS)) { "true" } else { $env:TPCH_CAPTURE_EXPLAIN_PLANS }
$env:TPCH_MODEL_TIMEOUT_SECONDS = if ([string]::IsNullOrWhiteSpace($env:TPCH_MODEL_TIMEOUT_SECONDS)) { "600" } else { $env:TPCH_MODEL_TIMEOUT_SECONDS }
$env:TPCH_REWRITE_SERVICE_HTTP_TIMEOUT_SECONDS = if ([string]::IsNullOrWhiteSpace($env:TPCH_REWRITE_SERVICE_HTTP_TIMEOUT_SECONDS)) { "1800" } else { $env:TPCH_REWRITE_SERVICE_HTTP_TIMEOUT_SECONDS }
$env:TPCH_EQUIVALENCE_TIMEOUT_MS = if ([string]::IsNullOrWhiteSpace($env:TPCH_EQUIVALENCE_TIMEOUT_MS)) { "900000" } else { $env:TPCH_EQUIVALENCE_TIMEOUT_MS }
$env:TPCH_EQUIVALENCE_MAX_ROWS_FULL_COMPARE = if ([string]::IsNullOrWhiteSpace($env:TPCH_EQUIVALENCE_MAX_ROWS_FULL_COMPARE)) { "1000000" } else { $env:TPCH_EQUIVALENCE_MAX_ROWS_FULL_COMPARE }
$env:TPCH_PROMOTION_ALPHA = if ([string]::IsNullOrWhiteSpace($env:TPCH_PROMOTION_ALPHA)) { "0.05" } else { $env:TPCH_PROMOTION_ALPHA }
$env:TPCH_PROMOTION_MIN_IMPROVEMENT_PCT = if ([string]::IsNullOrWhiteSpace($env:TPCH_PROMOTION_MIN_IMPROVEMENT_PCT)) { "2.0" } else { $env:TPCH_PROMOTION_MIN_IMPROVEMENT_PCT }
$env:TPCH_PROMOTION_MAX_CANDIDATE_CV = if ([string]::IsNullOrWhiteSpace($env:TPCH_PROMOTION_MAX_CANDIDATE_CV)) { "0.3" } else { $env:TPCH_PROMOTION_MAX_CANDIDATE_CV }
$env:TPCH_BANDIT_STRATEGY = if ([string]::IsNullOrWhiteSpace($env:TPCH_BANDIT_STRATEGY)) { "thompson" } else { $env:TPCH_BANDIT_STRATEGY }
$env:TPCH_BANDIT_RANDOM_SEED = if ([string]::IsNullOrWhiteSpace($env:TPCH_BANDIT_RANDOM_SEED)) { "12345" } else { $env:TPCH_BANDIT_RANDOM_SEED }
$env:TPCH_BANDIT_OBSERVATION_VARIANCE = if ([string]::IsNullOrWhiteSpace($env:TPCH_BANDIT_OBSERVATION_VARIANCE)) { "0.1" } else { $env:TPCH_BANDIT_OBSERVATION_VARIANCE }
$env:TPCH_UCB1_EXPLORATION_COEFFICIENT = if ([string]::IsNullOrWhiteSpace($env:TPCH_UCB1_EXPLORATION_COEFFICIENT)) { "1.4142135623730951" } else { $env:TPCH_UCB1_EXPLORATION_COEFFICIENT }
$env:TPCH_BACKGROUND_OPTIMIZER_ROUNDS = if ([string]::IsNullOrWhiteSpace($env:TPCH_BACKGROUND_OPTIMIZER_ROUNDS)) { "1" } else { $env:TPCH_BACKGROUND_OPTIMIZER_ROUNDS }
$env:TPCH_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT = if ([string]::IsNullOrWhiteSpace($env:TPCH_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT)) { "30" } else { $env:TPCH_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT }

switch ($CandidateSource) {
    "baseline" {
        $env:TPCH_WORKLOAD_MANIFEST_FILE = "/app/tpch/real-world/real-world-baseline-corpus.json"
        $env:TPCH_ENABLE_RULES = "false"
        $env:TPCH_ENABLE_LLM = "false"
        $env:TPCH_MAX_LLM_CANDIDATES = "0"
    }
    "rule" {
        $env:TPCH_WORKLOAD_MANIFEST_FILE = "/app/tpch/real-world/real-world-rule-corpus.json"
        $env:TPCH_ENABLE_RULES = "true"
        $env:TPCH_ENABLE_LLM = "false"
        $env:TPCH_MAX_LLM_CANDIDATES = "0"
    }
    "llm" {
        $env:TPCH_WORKLOAD_MANIFEST_FILE = "/app/tpch/real-world/real-world-local-llm-corpus.json"
        $env:TPCH_ENABLE_RULES = "false"
        $env:TPCH_ENABLE_LLM = "true"
        $env:TPCH_MAX_LLM_CANDIDATES = "1"
        $env:DEFAULT_MODEL = $Model
    }
    "mixed" {
        $env:TPCH_WORKLOAD_MANIFEST_FILE = "/app/tpch/real-world/real-world-mixed-corpus.json"
        $env:TPCH_ENABLE_RULES = "true"
        $env:TPCH_ENABLE_LLM = "true"
        $env:TPCH_MAX_LLM_CANDIDATES = "1"
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
$compose += @("-f", "docker-compose.yml", "-f", "docker-compose.tpch.yml")
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

function Reset-ComposeDataVolumes {
    $projectName = Get-ComposeProjectName
    Remove-DockerVolumeIfExists -Name "${projectName}_target-db-data"
    Remove-DockerVolumeIfExists -Name "${projectName}_metadata-db-data"
}

Set-ExperimentHostMetadata

$monitoringStartUtc = $null
$monitoringEndUtc = $null
$monitoringMetricsPath = $null
$prometheusUrl = if ($Monitoring) { Get-MonitoringPrometheusBaseUrl } else { $null }
$grafanaPort = if ([string]::IsNullOrWhiteSpace($env:GRAFANA_PORT)) { "3000" } else { $env:GRAFANA_PORT }
$grafanaUrl = if ($Monitoring) { "http://localhost:$grafanaPort" } else { $null }
$externalPowerArtifactPath = $null
$runWorkloadEntries = if ($WorkloadLimit -gt 0) { $WorkloadLimit } else { 8 }
$runSearchParameterSetsPerQuery = if ($SearchParameterLimit -gt 0) { $SearchParameterLimit } else { 70 }
$runHeldOutParameterSetsPerQuery = if ($HeldOutParameterLimit -gt 0) { $HeldOutParameterLimit } else { 30 }

Invoke-DockerCompose build
Invoke-DockerCompose down --remove-orphans
Reset-ComposeDataVolumes

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

    Invoke-DockerCompose run --rm data-loader
    if ($WorkloadLimit -gt 0 -or $SearchParameterLimit -gt 0 -or $HeldOutParameterLimit -gt 0) {
        $realWorldRoot = (Resolve-Path -LiteralPath ".\benchmark-data\tpch\sf$ScaleFactor\real-world").Path
        $manifestFileName = ($env:TPCH_WORKLOAD_MANIFEST_FILE -split "/")[-1]
        $limitedManifestFileName = New-LimitedJsonFileName -FileName $manifestFileName -Suffix $RunId
        New-ReducedWorkloadFiles `
            -ManifestPath (Join-Path $realWorldRoot $manifestFileName) `
            -OutputManifestPath (Join-Path $realWorldRoot $limitedManifestFileName) `
            -ParameterDirectory (Join-Path $realWorldRoot "parameters") `
            -Suffix $RunId `
            -WorkloadLimit $WorkloadLimit `
            -SearchParameterLimit $SearchParameterLimit `
            -HeldOutParameterLimit $HeldOutParameterLimit
        $env:TPCH_WORKLOAD_MANIFEST_FILE = "/app/tpch/real-world/$limitedManifestFileName"
    }

    Invoke-DockerCompose up -d --wait rewrite-service
    Invoke-DockerCompose run --rm --no-deps workload-runner
    Invoke-DockerCompose run --rm tpch-results-exporter

    $exportDirectory = Join-Path $resolvedOutputRoot $RunId
    New-Item -ItemType Directory -Force -Path $exportDirectory | Out-Null

    if ($Monitoring) {
        $monitoringEndUtc = (Get-Date).ToUniversalTime()
        & (Join-Path $PSScriptRoot "export-monitoring-window.ps1") `
            -OutputDirectory $exportDirectory `
            -StartUtc $monitoringStartUtc `
            -EndUtc $monitoringEndUtc `
            -PrometheusBaseUrl $prometheusUrl `
            -ExternalPowerCsv $ExternalPowerCsv
        if ($LASTEXITCODE -ne 0) {
            throw "Real-world monitoring export failed with exit code $LASTEXITCODE."
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

    $runManifest = [ordered]@{
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        run_id = $RunId
        corpus = "custom-real-world-suboptimal-tpch"
        candidate_source = $CandidateSource
        scale_factor = $ScaleFactor
        model = if ($CandidateSource -in @("llm", "mixed")) { $Model } else { $null }
        workload_manifest_file = $env:TPCH_WORKLOAD_MANIFEST_FILE
        query_path = $env:TPCH_QUERIES_PATH
        parameter_path = $env:TPCH_PARAMETERS_PATH
        workload_entries = $runWorkloadEntries
        search_parameter_sets_per_query = $runSearchParameterSetsPerQuery
        held_out_parameter_sets_per_query = $runHeldOutParameterSetsPerQuery
        benchmark_iterations = $env:TPCH_BENCHMARK_ITERATIONS
        minimum_promotion_pairs = $env:TPCH_MIN_PROMOTION_PAIRS
        promotion_alpha = $env:TPCH_PROMOTION_ALPHA
        promotion_min_improvement_pct = $env:TPCH_PROMOTION_MIN_IMPROVEMENT_PCT
        promotion_max_candidate_cv = $env:TPCH_PROMOTION_MAX_CANDIDATE_CV
        bandit_strategy = $env:TPCH_BANDIT_STRATEGY
        bandit_random_seed = $env:TPCH_BANDIT_RANDOM_SEED
        bandit_observation_variance = $env:TPCH_BANDIT_OBSERVATION_VARIANCE
        ucb1_exploration_coefficient = $env:TPCH_UCB1_EXPLORATION_COEFFICIENT
        background_optimizer_rounds = $env:TPCH_BACKGROUND_OPTIMIZER_ROUNDS
        background_optimizer_parameter_limit = $env:TPCH_BACKGROUND_OPTIMIZER_PARAMETER_LIMIT
        validation_parameter_set_limit = $env:TPCH_VALIDATION_PARAMETER_SET_LIMIT
        equivalence_max_rows_full_compare = $env:TPCH_EQUIVALENCE_MAX_ROWS_FULL_COMPARE
        capture_explain_plans = $env:TPCH_CAPTURE_EXPLAIN_PLANS
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
    }
    $runManifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $exportDirectory "manifest.json") -Encoding UTF8

    if (-not $SkipAnalysis) {
        & (Join-Path $PSScriptRoot "analyze-results.ps1") `
            -InputDirectory $exportDirectory `
            -Alpha ([double]$env:TPCH_PROMOTION_ALPHA) `
            -ImprovementThresholdPct ([double]$env:TPCH_PROMOTION_MIN_IMPROVEMENT_PCT)
        if ($LASTEXITCODE -ne 0) {
            throw "Real-world post-hoc analysis failed with exit code $LASTEXITCODE."
        }
    }

    Write-Host "Real-world evaluation exports written to $exportDirectory"
}
finally {
    docker @compose down --remove-orphans
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "docker compose down failed with exit code $LASTEXITCODE."
    }
    Reset-ComposeDataVolumes
}
