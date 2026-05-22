param(
    [Parameter(Mandatory = $true)]
    [string] $OutputDirectory,
    [Parameter(Mandatory = $true)]
    [datetime] $StartUtc,
    [Parameter(Mandatory = $true)]
    [datetime] $EndUtc,
    [string] $PrometheusBaseUrl = "",
    [int] $StepSeconds = 15,
    [string] $ExternalPowerCsv = ""
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "monitoring-functions.ps1")

if ($EndUtc.ToUniversalTime() -le $StartUtc.ToUniversalTime()) {
    throw "Monitoring export end time must be after start time."
}

if ($StepSeconds -lt 1) {
    throw "Monitoring export step must be at least one second."
}

$resolvedOutputDirectory = $OutputDirectory
if (-not (Test-Path -LiteralPath $resolvedOutputDirectory)) {
    New-Item -ItemType Directory -Force -Path $resolvedOutputDirectory | Out-Null
}
$resolvedOutputDirectory = (Resolve-Path -LiteralPath $resolvedOutputDirectory).Path

$metricsDirectory = Join-Path $resolvedOutputDirectory "metrics"
New-Item -ItemType Directory -Force -Path $metricsDirectory | Out-Null

$baseUrl = Get-MonitoringPrometheusBaseUrl -PrometheusBaseUrl $PrometheusBaseUrl
$start = $StartUtc.ToUniversalTime()
$end = $EndUtc.ToUniversalTime()

$querySpecs = @(
    [ordered]@{
        file = "docker-containers.csv"
        category = "container"
        metric = "container_cpu_cores"
        query = 'sum by (container_label_com_docker_compose_service, name) (rate(container_cpu_usage_seconds_total{image!=""}[1m]))'
    },
    [ordered]@{
        file = "docker-containers.csv"
        category = "container"
        metric = "container_memory_working_set_bytes"
        query = 'sum by (container_label_com_docker_compose_service, name) (container_memory_working_set_bytes{image!=""})'
    },
    [ordered]@{
        file = "docker-containers.csv"
        category = "container"
        metric = "container_network_receive_bytes_per_second"
        query = 'sum by (container_label_com_docker_compose_service, name) (rate(container_network_receive_bytes_total{image!=""}[1m]))'
    },
    [ordered]@{
        file = "docker-containers.csv"
        category = "container"
        metric = "container_network_transmit_bytes_per_second"
        query = 'sum by (container_label_com_docker_compose_service, name) (rate(container_network_transmit_bytes_total{image!=""}[1m]))'
    },
    [ordered]@{
        file = "docker-containers.csv"
        category = "container"
        metric = "container_fs_reads_bytes_per_second"
        query = 'sum by (container_label_com_docker_compose_service, name) (rate(container_fs_reads_bytes_total{image!=""}[1m]))'
    },
    [ordered]@{
        file = "docker-containers.csv"
        category = "container"
        metric = "container_fs_writes_bytes_per_second"
        query = 'sum by (container_label_com_docker_compose_service, name) (rate(container_fs_writes_bytes_total{image!=""}[1m]))'
    },
    [ordered]@{
        file = "host.csv"
        category = "host"
        metric = "host_cpu_utilization_ratio"
        query = '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))'
    },
    [ordered]@{
        file = "host.csv"
        category = "host"
        metric = "host_memory_utilization_ratio"
        query = '1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)'
    },
    [ordered]@{
        file = "host.csv"
        category = "host"
        metric = "host_disk_read_bytes_per_second"
        query = 'sum by (instance, device) (rate(node_disk_read_bytes_total[1m]))'
    },
    [ordered]@{
        file = "host.csv"
        category = "host"
        metric = "host_disk_written_bytes_per_second"
        query = 'sum by (instance, device) (rate(node_disk_written_bytes_total[1m]))'
    },
    [ordered]@{
        file = "host.csv"
        category = "host"
        metric = "host_network_receive_bytes_per_second"
        query = 'sum by (instance, device) (rate(node_network_receive_bytes_total{device!="lo"}[1m]))'
    },
    [ordered]@{
        file = "host.csv"
        category = "host"
        metric = "host_network_transmit_bytes_per_second"
        query = 'sum by (instance, device) (rate(node_network_transmit_bytes_total{device!="lo"}[1m]))'
    },
    [ordered]@{
        file = "gpu.csv"
        category = "gpu"
        metric = "gpu_utilization_percent"
        query = 'DCGM_FI_DEV_GPU_UTIL'
    },
    [ordered]@{
        file = "gpu.csv"
        category = "gpu"
        metric = "gpu_framebuffer_used_mebibytes"
        query = 'DCGM_FI_DEV_FB_USED'
    },
    [ordered]@{
        file = "gpu.csv"
        category = "gpu"
        metric = "gpu_power_usage_watts"
        query = 'DCGM_FI_DEV_POWER_USAGE'
    },
    [ordered]@{
        file = "gpu.csv"
        category = "gpu"
        metric = "gpu_total_energy_millijoules"
        query = 'DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION'
    },
    [ordered]@{
        file = "rewrite-service.csv"
        category = "rewrite-service"
        metric = "rewrite_service_requests_per_second"
        query = 'sum by (endpoint) (rate(rewrite_service_requests_total[1m]))'
    },
    [ordered]@{
        file = "rewrite-service.csv"
        category = "rewrite-service"
        metric = "rewrite_service_mean_latency_seconds"
        query = 'rate(rewrite_service_request_seconds_sum[1m]) / rate(rewrite_service_request_seconds_count[1m])'
    }
)

$rowsByFile = @{}
$rawResults = New-Object System.Collections.Generic.List[object]

foreach ($spec in $querySpecs) {
    $file = Get-MonitoringPropertyValue -Object $spec -Name "file"
    $category = Get-MonitoringPropertyValue -Object $spec -Name "category"
    $metric = Get-MonitoringPropertyValue -Object $spec -Name "metric"
    $query = Get-MonitoringPropertyValue -Object $spec -Name "query"

    try {
        $response = Invoke-PrometheusRangeQuery `
            -PrometheusBaseUrl $baseUrl `
            -Query $query `
            -StartUtc $start `
            -EndUtc $end `
            -StepSeconds $StepSeconds

        $rows = @(Convert-PrometheusMatrixToRows `
            -Response $response `
            -Category $category `
            -MetricName $metric)
    }
    catch {
        throw "Failed to export monitoring metric '$metric' from query '$query': $($_.Exception.Message)"
    }

    if (-not $rowsByFile.ContainsKey($file)) {
        $rowsByFile[$file] = New-Object System.Collections.Generic.List[object]
    }
    foreach ($row in $rows) {
        $rowsByFile[$file].Add($row)
    }

    $rawResults.Add((New-MonitoringQuerySummary `
        -Spec $spec `
        -Response $response `
        -RowCount $rows.Count))
}

$expectedFiles = @(
    "docker-containers.csv",
    "host.csv",
    "gpu.csv",
    "rewrite-service.csv"
)

$writtenFiles = New-Object System.Collections.Generic.List[object]
foreach ($file in $expectedFiles) {
    $path = Join-Path $metricsDirectory $file
    $rows = @()
    if ($rowsByFile.ContainsKey($file)) {
        $rows = $rowsByFile[$file].ToArray()
    }

    Write-MonitoringCsv -Rows $rows -Path $path
    $writtenFiles.Add([ordered]@{
        file = $file
        path = "metrics/$file"
        row_count = $rows.Count
    })
}

$externalPowerPath = Copy-ExternalPowerCsv `
    -ExternalPowerCsv $ExternalPowerCsv `
    -MetricsDirectory $metricsDirectory

if (-not [string]::IsNullOrWhiteSpace($externalPowerPath)) {
    $writtenFiles.Add([ordered]@{
        file = "external-power.csv"
        path = "metrics/external-power.csv"
        row_count = $null
    })
}

$rawResults | ConvertTo-Json -Depth 100 | Set-Content `
    -LiteralPath (Join-Path $metricsDirectory "prometheus-window.json") `
    -Encoding UTF8

$externalPowerCsvSource = $null
if (-not [string]::IsNullOrWhiteSpace($ExternalPowerCsv)) {
    $externalPowerCsvSource = $ExternalPowerCsv
}

$querySummaries = @($querySpecs | ForEach-Object {
    [ordered]@{
        file = Get-MonitoringPropertyValue -Object $_ -Name "file"
        category = Get-MonitoringPropertyValue -Object $_ -Name "category"
        metric = Get-MonitoringPropertyValue -Object $_ -Name "metric"
        query = Get-MonitoringPropertyValue -Object $_ -Name "query"
    }
})

$manifest = [ordered]@{
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    start_utc = $start.ToString("o")
    end_utc = $end.ToString("o")
    step_seconds = $StepSeconds
    prometheus_base_url = $baseUrl
    external_power_csv_source = $externalPowerCsvSource
    files = $writtenFiles.ToArray()
    queries = $querySummaries
}

$manifest | ConvertTo-Json -Depth 20 | Set-Content `
    -LiteralPath (Join-Path $metricsDirectory "monitoring-manifest.json") `
    -Encoding UTF8

Write-Host "Monitoring metrics exported to $metricsDirectory"
