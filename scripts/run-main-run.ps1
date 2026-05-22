param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $Model,

    [ValidateSet("tpch", "real-world", "job-imdb")]
    [string] $Corpus,

    [switch] $All
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Model)) {
    throw "-Model must be a non-empty string."
}

$corpusSupplied = $PSBoundParameters.ContainsKey("Corpus")
if ($All -and $corpusSupplied) {
    throw "Use either -All or -Corpus, not both."
}

if (-not $All -and -not $corpusSupplied) {
    throw "Supply either -All or -Corpus."
}

$modulePath = Join-Path $PSScriptRoot "lib\ExperimentRun.psm1"
Import-Module $modulePath -Force

if ($All) {
    Invoke-TpchMainRun -Model $Model
    Invoke-RealWorldMainRun -Model $Model
    Invoke-JobImdbMainRun -Model $Model
    return
}

switch ($Corpus) {
    "tpch" {
        Invoke-TpchMainRun -Model $Model
    }
    "real-world" {
        Invoke-RealWorldMainRun -Model $Model
    }
    "job-imdb" {
        Invoke-JobImdbMainRun -Model $Model
    }
}
