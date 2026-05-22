$ErrorActionPreference = "Stop"

function Read-JsonArrayFile {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $value = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    if ($null -eq $value) {
        return @()
    }

    if ($value -is [array]) {
        return @($value)
    }

    return @($value)
}

function Write-JsonArrayFile {
    param(
        [Parameter(Mandatory = $true)]
        [object[]] $Items,

        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    ConvertTo-Json -InputObject @($Items) -Depth 100 |
        Set-Content -LiteralPath $Path -Encoding UTF8
}

function New-LimitedJsonFileName {
    param(
        [Parameter(Mandatory = $true)]
        [string] $FileName,

        [Parameter(Mandatory = $true)]
        [string] $Suffix
    )

    $safeSuffix = $Suffix -replace "[^A-Za-z0-9._-]", "_"
    return "$([System.IO.Path]::GetFileNameWithoutExtension($FileName))-$safeSuffix$([System.IO.Path]::GetExtension($FileName))"
}

function Limit-JsonArrayFile {
    param(
        [Parameter(Mandatory = $true)]
        [string] $InputPath,

        [Parameter(Mandatory = $true)]
        [string] $OutputPath,

        [Parameter(Mandatory = $true)]
        [int] $Limit
    )

    if ($Limit -lt 1) {
        throw "Limit must be greater than zero."
    }

    $items = Read-JsonArrayFile -Path $InputPath
    if ($items.Count -lt 1) {
        throw "No JSON array entries found in $InputPath."
    }

    Write-JsonArrayFile -Items @($items | Select-Object -First $Limit) -Path $OutputPath
}

function New-ReducedWorkloadFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string] $ManifestPath,

        [Parameter(Mandatory = $true)]
        [string] $OutputManifestPath,

        [Parameter(Mandatory = $true)]
        [string] $ParameterDirectory,

        [Parameter(Mandatory = $true)]
        [string] $Suffix,

        [int] $WorkloadLimit = 0,
        [int] $SearchParameterLimit = 0,
        [int] $HeldOutParameterLimit = 0
    )

    if ($WorkloadLimit -lt 0 -or $SearchParameterLimit -lt 0 -or $HeldOutParameterLimit -lt 0) {
        throw "Workload and parameter limits must be zero or greater."
    }

    if ($WorkloadLimit -eq 0 -and $SearchParameterLimit -eq 0 -and $HeldOutParameterLimit -eq 0) {
        return
    }

    $manifest = Read-JsonArrayFile -Path $ManifestPath
    if ($manifest.Count -lt 1) {
        throw "No workload entries found in $ManifestPath."
    }

    $selectedEntries = if ($WorkloadLimit -gt 0) {
        @($manifest | Select-Object -First $WorkloadLimit)
    }
    else {
        @($manifest)
    }

    foreach ($entry in $selectedEntries) {
        $parameterFile = [string]$entry.parameter_file
        if ([string]::IsNullOrWhiteSpace($parameterFile)) {
            throw "Workload entry is missing parameter_file in $ManifestPath."
        }

        if ($SearchParameterLimit -gt 0) {
            $searchOutputFile = New-LimitedJsonFileName -FileName $parameterFile -Suffix $Suffix
            Limit-JsonArrayFile `
                -InputPath (Join-Path $ParameterDirectory $parameterFile) `
                -OutputPath (Join-Path $ParameterDirectory $searchOutputFile) `
                -Limit $SearchParameterLimit
            $entry.parameter_file = $searchOutputFile
        }

        $heldOutProperty = $entry.PSObject.Properties["held_out_parameter_file"]
        if ($null -ne $heldOutProperty -and -not [string]::IsNullOrWhiteSpace([string]$heldOutProperty.Value) -and $HeldOutParameterLimit -gt 0) {
            $heldOutFile = [string]$heldOutProperty.Value
            $heldOutOutputFile = New-LimitedJsonFileName -FileName $heldOutFile -Suffix $Suffix
            Limit-JsonArrayFile `
                -InputPath (Join-Path $ParameterDirectory $heldOutFile) `
                -OutputPath (Join-Path $ParameterDirectory $heldOutOutputFile) `
                -Limit $HeldOutParameterLimit
            $entry.held_out_parameter_file = $heldOutOutputFile
        }
    }

    Write-JsonArrayFile -Items $selectedEntries -Path $OutputManifestPath
}
