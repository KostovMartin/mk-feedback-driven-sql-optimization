param(
    [string] $QueryOutputPath = 'queries\job-imdb',
    [string] $DataOutputPath = 'data\job-imdb',
    [string] $WorkDirectory = '.test-output\job-imdb-download',
    [string] $JobRepositoryZipUrl = 'https://github.com/gregrahn/join-order-benchmark/archive/refs/heads/master.zip',
    [string] $ImdbDataUrl = 'https://event.cwi.nl/da/job/imdb.tgz',
    [string] $ImdbDataMirrorUrl = 'https://bonsai.cedardb.com/job/imdb.tgz',
    [switch] $SkipData,
    [switch] $Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$jobImdbTables = "aka_name aka_title cast_info char_name comp_cast_type company_name company_type complete_cast info_type keyword kind_type link_type movie_companies movie_info movie_info_idx movie_keyword movie_link name person_info role_type title"
$expectedTables = $jobImdbTables -split " "

function Resolve-RepoPath {
    param([string] $Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }

    return (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")) $Path)
}

function Download-File {
    param(
        [string[]] $Uris,
        [string] $OutputPath,
        [string] $Label
    )

    if ((Test-Path -LiteralPath $OutputPath) -and -not $Force) {
        Write-Host "Reusing existing $Label archive at $OutputPath"
        return
    }

    $lastError = $null
    foreach ($uri in $Uris) {
        try {
            Write-Host "Downloading $Label from $uri"
            Invoke-WebRequest -Uri $uri -OutFile $OutputPath -UseBasicParsing
            return
        }
        catch {
            $lastError = $_
            Write-Warning "Failed to download $Label from ${uri}: $($_.Exception.Message)"
            if (Test-Path -LiteralPath $OutputPath) {
                Remove-Item -LiteralPath $OutputPath -Force
            }
        }
    }

    throw "Failed to download $Label. Last error: $($lastError.Exception.Message)"
}

function Copy-FirstMatchingFile {
    param(
        [string] $SourceRoot,
        [string] $FileName,
        [string] $DestinationPath
    )

    $match = Get-ChildItem -LiteralPath $SourceRoot -Recurse -File -Filter $FileName |
        Select-Object -First 1
    if ($null -eq $match) {
        throw "Expected file $FileName was not found under $SourceRoot"
    }

    Copy-Item -LiteralPath $match.FullName -Destination $DestinationPath -Force
}

$queryOutput = Resolve-RepoPath $QueryOutputPath
$dataOutput = Resolve-RepoPath $DataOutputPath
$workRoot = Resolve-RepoPath $WorkDirectory

New-Item -ItemType Directory -Force -Path $queryOutput | Out-Null
New-Item -ItemType Directory -Force -Path $dataOutput | Out-Null
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null

$jobZip = Join-Path $workRoot "join-order-benchmark-master.zip"
$jobExtractRoot = Join-Path $workRoot ("job-sql-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Force -Path $jobExtractRoot | Out-Null

Download-File `
    -Uris @($JobRepositoryZipUrl) `
    -OutputPath $jobZip `
    -Label "JOB SQL/schema"

Expand-Archive -LiteralPath $jobZip -DestinationPath $jobExtractRoot -Force

$queryFiles = @(
    Get-ChildItem -LiteralPath $jobExtractRoot -Recurse -File -Filter "*.sql" |
        Where-Object { $_.Name -match '^[0-9]+[a-z]\.sql$' } |
        Sort-Object Name
)
if ($queryFiles.Count -lt 100) {
    throw "Expected the official JOB query corpus, but found only $($queryFiles.Count) query SQL files."
}

foreach ($queryFile in $queryFiles) {
    Copy-Item -LiteralPath $queryFile.FullName -Destination (Join-Path $queryOutput $queryFile.Name) -Force
}

Copy-FirstMatchingFile -SourceRoot $jobExtractRoot -FileName "schema.sql" -DestinationPath (Join-Path $dataOutput "schema.sql")
Copy-FirstMatchingFile -SourceRoot $jobExtractRoot -FileName "fkindexes.sql" -DestinationPath (Join-Path $dataOutput "fkindexes.sql")

if (-not $SkipData) {
    $imdbArchive = Join-Path $workRoot "imdb.tgz"
    $imdbExtractRoot = Join-Path $workRoot ("imdb-csv-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Force -Path $imdbExtractRoot | Out-Null

    Download-File `
        -Uris @($ImdbDataUrl, $ImdbDataMirrorUrl) `
        -OutputPath $imdbArchive `
        -Label "IMDB CSV"

    Write-Host "Extracting IMDB CSV archive to $imdbExtractRoot"
    & tar -xzf $imdbArchive -C $imdbExtractRoot
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed to extract $imdbArchive with exit code $LASTEXITCODE."
    }

    foreach ($tableName in $expectedTables) {
        Copy-FirstMatchingFile `
            -SourceRoot $imdbExtractRoot `
            -FileName "$tableName.csv" `
            -DestinationPath (Join-Path $dataOutput "$tableName.csv")
    }
}
else {
    Write-Host "Skipping IMDB CSV download because -SkipData was supplied."
}

$manifest = [ordered]@{
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    job_repository_zip_url = $JobRepositoryZipUrl
    imdb_data_url = if ($SkipData) { $null } else { $ImdbDataUrl }
    imdb_data_mirror_url = if ($SkipData) { $null } else { $ImdbDataMirrorUrl }
    query_output_path = $queryOutput
    data_output_path = $dataOutput
    query_file_count = $queryFiles.Count
    csv_file_count = if ($SkipData) { 0 } else { $expectedTables.Count }
    skipped_data_download = [bool]$SkipData
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $dataOutput "job-imdb-resource-manifest.json") -Encoding UTF8

Write-Host "JOB/IMDB query files written to $queryOutput"
Write-Host "JOB/IMDB schema files written to $dataOutput"
if (-not $SkipData) {
    Write-Host "JOB/IMDB CSV files written to $dataOutput"
}
