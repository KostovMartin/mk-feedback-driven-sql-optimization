function Get-MonitoringPrometheusBaseUrl {
    param([string] $PrometheusBaseUrl = "")

    if (-not [string]::IsNullOrWhiteSpace($PrometheusBaseUrl)) {
        return $PrometheusBaseUrl.TrimEnd("/")
    }

    $port = $env:PROMETHEUS_PORT
    if ([string]::IsNullOrWhiteSpace($port)) {
        $port = "9090"
    }

    return "http://localhost:$port"
}

function Invoke-PrometheusRangeQuery {
    param(
        [string] $PrometheusBaseUrl = "",
        [Parameter(Mandatory = $true)]
        [string] $Query,
        [Parameter(Mandatory = $true)]
        [datetime] $StartUtc,
        [Parameter(Mandatory = $true)]
        [datetime] $EndUtc,
        [int] $StepSeconds = 15
    )

    $baseUrl = Get-MonitoringPrometheusBaseUrl -PrometheusBaseUrl $PrometheusBaseUrl
    $start = [uri]::EscapeDataString($StartUtc.ToUniversalTime().ToString("o"))
    $end = [uri]::EscapeDataString($EndUtc.ToUniversalTime().ToString("o"))
    $queryEncoded = [uri]::EscapeDataString($Query)
    $uri = "$baseUrl/api/v1/query_range?query=$queryEncoded&start=$start&end=$end&step=${StepSeconds}s"

    return Invoke-RestMethod -Uri $uri -Method Get
}

function Get-MonitoringPropertyValue {
    param(
        [object] $Object,
        [Parameter(Mandatory = $true)]
        [string] $Name
    )

    if ($null -eq $Object) {
        return $null
    }

    if ($Object -is [System.Collections.IDictionary] -and $Object.Contains($Name)) {
        return $Object[$Name]
    }

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }

    return $property.Value
}

function Convert-PrometheusMatrixToRows {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Response,
        [Parameter(Mandatory = $true)]
        [string] $Category,
        [Parameter(Mandatory = $true)]
        [string] $MetricName
    )

    $rows = New-Object System.Collections.Generic.List[object]
    $data = Get-MonitoringPropertyValue -Object $Response -Name "data"
    $result = Get-MonitoringPropertyValue -Object $data -Name "result"
    if ($null -eq $result) {
        return @()
    }

    foreach ($series in @($result)) {
        $labelsJson = "{}"
        $metric = Get-MonitoringPropertyValue -Object $series -Name "metric"
        if ($null -ne $metric) {
            $labelsJson = $metric | ConvertTo-Json -Compress -Depth 20
        }

        $values = Get-MonitoringPropertyValue -Object $series -Name "values"
        foreach ($sample in @($values)) {
            if ($null -eq $sample) {
                continue
            }

            $sampleValues = @($sample)
            if ($sampleValues.Count -eq 1 -and $sampleValues[0] -is [System.Collections.IEnumerable] -and $sampleValues[0] -isnot [string]) {
                $sampleValues = @($sampleValues[0])
            }

            if ($sampleValues.Count -lt 2) {
                continue
            }

            $unixSeconds = [double]$sampleValues[0]
            $timestampUtc = [DateTimeOffset]::FromUnixTimeMilliseconds(
                [int64][Math]::Round($unixSeconds * 1000)
            ).UtcDateTime.ToString("o")

            $rows.Add([pscustomobject]@{
                timestamp_utc = $timestampUtc
                category = $Category
                metric = $MetricName
                labels_json = $labelsJson
                value = [double]$sampleValues[1]
            })
        }
    }

    return $rows.ToArray()
}

function New-MonitoringQuerySummary {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Spec,
        [Parameter(Mandatory = $true)]
        [object] $Response,
        [int] $RowCount = 0
    )

    $data = Get-MonitoringPropertyValue -Object $Response -Name "data"
    $result = Get-MonitoringPropertyValue -Object $data -Name "result"
    $resultType = Get-MonitoringPropertyValue -Object $data -Name "resultType"
    $warnings = Get-MonitoringPropertyValue -Object $Response -Name "warnings"

    return [ordered]@{
        file = Get-MonitoringPropertyValue -Object $Spec -Name "file"
        category = Get-MonitoringPropertyValue -Object $Spec -Name "category"
        metric = Get-MonitoringPropertyValue -Object $Spec -Name "metric"
        query = Get-MonitoringPropertyValue -Object $Spec -Name "query"
        status = Get-MonitoringPropertyValue -Object $Response -Name "status"
        result_type = $resultType
        series_count = @($result).Count
        row_count = $RowCount
        warnings = if ($null -eq $warnings) { @() } else { @($warnings) }
    }
}

function Write-MonitoringCsv {
    param(
        [object[]] $Rows,
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $directory = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($directory)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }

    if ($null -eq $Rows -or $Rows.Count -eq 0) {
        Set-Content `
            -LiteralPath $Path `
            -Value "timestamp_utc,category,metric,labels_json,value" `
            -Encoding UTF8
        return
    }

    $Rows | Export-Csv -LiteralPath $Path -NoTypeInformation -Encoding UTF8
}

function Copy-ExternalPowerCsv {
    param(
        [string] $ExternalPowerCsv = "",
        [Parameter(Mandatory = $true)]
        [string] $MetricsDirectory
    )

    if ([string]::IsNullOrWhiteSpace($ExternalPowerCsv)) {
        return $null
    }

    $resolved = Resolve-Path -LiteralPath $ExternalPowerCsv -ErrorAction SilentlyContinue
    if ($null -eq $resolved) {
        throw "External power CSV does not exist: $ExternalPowerCsv"
    }

    New-Item -ItemType Directory -Force -Path $MetricsDirectory | Out-Null
    $destination = Join-Path $MetricsDirectory "external-power.csv"
    Copy-Item -LiteralPath $resolved.Path -Destination $destination -Force
    return $destination
}
