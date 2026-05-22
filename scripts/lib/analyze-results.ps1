param(
    [Parameter(Mandatory = $true)]
    [string] $InputDirectory,
    [string] $OutputDirectory = "",
    [int] $BootstrapIterations = 2000,
    [int] $Seed = 8675309,
    [double] $Alpha = 0.05,
    [double] $ImprovementThresholdPct = 2.0
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = $InputDirectory
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$requiredFiles = @(
    "benchmark_runs.csv",
    "candidates.csv",
    "decisions.csv",
    "equivalence_checks.csv",
    "invocations.csv",
    "bandit_state.csv",
    "pool_summary.csv",
    "query_templates.csv",
    "manifest.json"
)

foreach ($file in $requiredFiles) {
    $path = Join-Path $InputDirectory $file
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Controlled analysis input is missing required file: $path"
    }
}

$invariantCulture = [System.Globalization.CultureInfo]::InvariantCulture

function ConvertTo-DoubleOrNull {
    param([AllowNull()] [object] $Value)

    if ($null -eq $Value) {
        return $null
    }

    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }

    return [double]::Parse($text, [System.Globalization.CultureInfo]::InvariantCulture)
}

function ConvertFrom-PostgresBool {
    param([AllowNull()] [object] $Value)

    if ($null -eq $Value) {
        return $false
    }

    $text = ([string]$Value).Trim()
    return $text -in @("t", "true", "1", "yes")
}

function Format-NullableNumber {
    param([AllowNull()] [object] $Value, [int] $Digits = 3)

    if ($null -eq $Value) {
        return "n/a"
    }

    $number = [double]$Value
    if ([double]::IsNaN($number) -or [double]::IsInfinity($number)) {
        return "n/a"
    }

    return $number.ToString("F$Digits", [System.Globalization.CultureInfo]::InvariantCulture)
}

function Format-MarkdownCell {
    param([AllowNull()] [object] $Value)

    if ($null -eq $Value) {
        return ""
    }

    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        return ""
    }

    return (($text -replace "`r?`n", " ") -replace "\|", "\|")
}

function Format-CodeBlockText {
    param([AllowNull()] [object] $Value)

    if ($null -eq $Value) {
        return "n/a"
    }

    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        return "n/a"
    }

    return (($text -replace "`r`n", "`n") -replace '```', "'''")
}

function Get-NumericColumnSum {
    param(
        [object[]] $Rows,
        [string] $ColumnName
    )

    $sum = 0.0
    foreach ($row in $Rows) {
        $value = Get-ObjectPropertyValue -Object $row -Name $ColumnName -Default $null
        $number = ConvertTo-DoubleOrNull -Value $value
        if ($null -ne $number) {
            $sum += $number
        }
    }

    return $sum
}

function Get-NonEmptyColumnCount {
    param(
        [object[]] $Rows,
        [string] $ColumnName
    )

    return @(
        $Rows |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace(
                    [string](Get-ObjectPropertyValue -Object $_ -Name $ColumnName -Default "")
                )
            }
    ).Count
}

function Get-Percentile {
    param([double[]] $Values, [double] $Percentile)

    $clean = @(
        $Values |
            Where-Object { $null -ne $_ -and -not [double]::IsNaN([double]$_) } |
            Sort-Object
    )

    if ($clean.Count -eq 0) {
        return $null
    }

    if ($clean.Count -eq 1) {
        return [double]$clean[0]
    }

    $rank = ($Percentile / 100.0) * ($clean.Count - 1)
    $lowerIndex = [int][math]::Floor($rank)
    $upperIndex = [int][math]::Ceiling($rank)
    if ($lowerIndex -eq $upperIndex) {
        return [double]$clean[$lowerIndex]
    }

    $weight = $rank - $lowerIndex
    return ([double]$clean[$lowerIndex] * (1.0 - $weight)) + ([double]$clean[$upperIndex] * $weight)
}

function Get-Median {
    param([double[]] $Values)
    return Get-Percentile -Values $Values -Percentile 50
}

function Get-CoefficientOfVariation {
    param([double[]] $Values)

    $clean = @($Values | Where-Object { $null -ne $_ -and -not [double]::IsNaN([double]$_) })
    if ($clean.Count -le 1) {
        return 0.0
    }

    $mean = ($clean | Measure-Object -Average).Average
    if ($mean -le 0) {
        return [double]::PositiveInfinity
    }

    $sumSquares = 0.0
    foreach ($value in $clean) {
        $sumSquares += [math]::Pow(([double]$value) - $mean, 2.0)
    }

    return [math]::Sqrt($sumSquares / ($clean.Count - 1)) / $mean
}

function Get-PairedReward {
    param([double] $BaselineMs, [double] $CandidateMs)

    if ($BaselineMs -le 0 -or $CandidateMs -ge $BaselineMs) {
        return 0.0
    }

    return ($BaselineMs - $CandidateMs) / $BaselineMs
}

function Get-WilcoxonSignedRankStats {
    param([object[]] $Pairs)

    $zeroTolerance = 1e-12
    $differences = New-Object "System.Collections.Generic.List[object]"
    foreach ($pair in $Pairs) {
        $difference = ([double]$pair.baseline_median_ms) - ([double]$pair.candidate_median_ms)
        if ([math]::Abs($difference) -gt $zeroTolerance) {
            $differences.Add([pscustomobject]@{
                difference = $difference
                absolute_difference = [math]::Abs($difference)
            })
        }
    }

    if ($differences.Count -eq 0) {
        return [pscustomobject]@{
            p_value = 1.0
            rank_biserial = 0.0
            nonzero_pairs = 0
            positive_rank_sum = 0.0
            negative_rank_sum = 0.0
        }
    }

    $ordered = @($differences | Sort-Object absolute_difference)
    $ranked = New-Object "System.Collections.Generic.List[object]"
    $index = 0
    while ($index -lt $ordered.Count) {
        $start = $index
        $absoluteDifference = [double]$ordered[$index].absolute_difference
        while (
            $index -lt $ordered.Count -and
            [math]::Abs(([double]$ordered[$index].absolute_difference) - $absoluteDifference) -le $zeroTolerance
        ) {
            $index++
        }

        $end = $index - 1
        $averageRank = (($start + 1) + ($end + 1)) / 2.0
        for ($rankIndex = $start; $rankIndex -le $end; $rankIndex++) {
            $ranked.Add([pscustomobject]@{
                difference = [double]$ordered[$rankIndex].difference
                rank = [double]$averageRank
            })
        }
    }

    $positiveRankSum = 0.0
    $negativeRankSum = 0.0
    foreach ($rankedDifference in $ranked) {
        if ([double]$rankedDifference.difference -gt 0) {
            $positiveRankSum += [double]$rankedDifference.rank
        }
        else {
            $negativeRankSum += [double]$rankedDifference.rank
        }
    }

    $observed = [int][math]::Round($positiveRankSum * 2.0, [System.MidpointRounding]::AwayFromZero)
    $scaledRanks = New-Object "System.Collections.Generic.List[int]"
    $maxSum = 0
    foreach ($rankedDifference in $ranked) {
        $scaledRank = [int][math]::Round(([double]$rankedDifference.rank) * 2.0, [System.MidpointRounding]::AwayFromZero)
        $scaledRanks.Add($scaledRank)
        $maxSum += $scaledRank
    }

    $counts = [double[]]::new($maxSum + 1)
    $counts[0] = 1.0
    foreach ($rank in $scaledRanks) {
        for ($sum = $maxSum - $rank; $sum -ge 0; $sum--) {
            if ($counts[$sum] -gt 0) {
                $counts[$sum + $rank] += $counts[$sum]
            }
        }
    }

    $favorable = 0.0
    for ($sum = $observed; $sum -lt $counts.Length; $sum++) {
        $favorable += $counts[$sum]
    }

    $pValue = $favorable / [math]::Pow(2.0, $ranked.Count)
    $pValue = [math]::Min([math]::Max($pValue, 0.0), 1.0)
    $totalRankSum = $positiveRankSum + $negativeRankSum
    $rankBiserial = 0.0
    if ($totalRankSum -gt 0) {
        $rankBiserial = ($positiveRankSum - $negativeRankSum) / $totalRankSum
    }

    return [pscustomobject]@{
        p_value = $pValue
        rank_biserial = $rankBiserial
        nonzero_pairs = $ranked.Count
        positive_rank_sum = $positiveRankSum
        negative_rank_sum = $negativeRankSum
    }
}

function Get-BootstrapMedianImprovementInterval {
    param(
        [object[]] $Pairs,
        [int] $Iterations,
        [System.Random] $Random
    )

    if ($Pairs.Count -eq 0 -or $Iterations -le 0) {
        return [pscustomobject]@{
            low = $null
            high = $null
            iterations = 0
        }
    }

    $estimates = [double[]]::new($Iterations)
    for ($iteration = 0; $iteration -lt $Iterations; $iteration++) {
        $baselineSample = [double[]]::new($Pairs.Count)
        $candidateSample = [double[]]::new($Pairs.Count)
        for ($sampleIndex = 0; $sampleIndex -lt $Pairs.Count; $sampleIndex++) {
            $selectedPair = $Pairs[$Random.Next($Pairs.Count)]
            $baselineSample[$sampleIndex] = [double]$selectedPair.baseline_median_ms
            $candidateSample[$sampleIndex] = [double]$selectedPair.candidate_median_ms
        }

        $baselineMedian = Get-Median -Values $baselineSample
        $candidateMedian = Get-Median -Values $candidateSample
        if ($baselineMedian -le 0) {
            $estimates[$iteration] = 0.0
        }
        else {
            $estimates[$iteration] = (($baselineMedian - $candidateMedian) / $baselineMedian) * 100.0
        }
    }

    return [pscustomobject]@{
        low = Get-Percentile -Values $estimates -Percentile 2.5
        high = Get-Percentile -Values $estimates -Percentile 97.5
        iterations = $Iterations
    }
}

function Get-RunMetadata {
    param([object[]] $Rows)

    foreach ($row in $Rows) {
        if (-not [string]::IsNullOrWhiteSpace($row.reproducibility_metadata)) {
            try {
                return ($row.reproducibility_metadata | ConvertFrom-Json)
            }
            catch {
                return $null
            }
        }
    }

    return $null
}

function Get-ObjectPropertyValue {
    param(
        [AllowNull()] [object] $Object,
        [string] $Name,
        [AllowNull()] [object] $Default = ""
    )

    if ($null -eq $Object) {
        return $Default
    }

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $Default
    }

    return $property.Value
}

function Get-UniqueColumnValues {
    param(
        [object[]] $Rows,
        [string] $ColumnName
    )

    $values = New-Object "System.Collections.Generic.List[string]"
    foreach ($row in $Rows) {
        $property = $row.PSObject.Properties[$ColumnName]
        if ($null -ne $property -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
            $values.Add([string]$property.Value)
        }
    }

    return @($values | Select-Object -Unique)
}

$benchmarkRows = @(Import-Csv -LiteralPath (Join-Path $InputDirectory "benchmark_runs.csv"))
$candidateRows = @(Import-Csv -LiteralPath (Join-Path $InputDirectory "candidates.csv"))
$decisionRows = @(Import-Csv -LiteralPath (Join-Path $InputDirectory "decisions.csv"))
$equivalenceRows = @(Import-Csv -LiteralPath (Join-Path $InputDirectory "equivalence_checks.csv"))
$invocationRows = @(Import-Csv -LiteralPath (Join-Path $InputDirectory "invocations.csv"))
$banditRows = @(Import-Csv -LiteralPath (Join-Path $InputDirectory "bandit_state.csv"))
$poolRows = @(Import-Csv -LiteralPath (Join-Path $InputDirectory "pool_summary.csv"))
$queryTemplateRows = @(Import-Csv -LiteralPath (Join-Path $InputDirectory "query_templates.csv"))
$workloadCasePath = Join-Path $InputDirectory "workload_case_results.csv"
$workloadCaseRows = @(
    if (Test-Path -LiteralPath $workloadCasePath) {
        Import-Csv -LiteralPath $workloadCasePath
    }
)
$runManifest = Get-Content -Raw -LiteralPath (Join-Path $InputDirectory "manifest.json") | ConvertFrom-Json
$effectiveImprovementThresholdPct = $ImprovementThresholdPct
if (-not $PSBoundParameters.ContainsKey("ImprovementThresholdPct")) {
    $manifestImprovementThreshold = Get-ObjectPropertyValue `
        -Object $runManifest `
        -Name "promotion_min_improvement_pct" `
        -Default $null
    if (-not [string]::IsNullOrWhiteSpace([string]$manifestImprovementThreshold)) {
        $effectiveImprovementThresholdPct = [double]$manifestImprovementThreshold
    }
}

$expectedRunId = Get-ObjectPropertyValue -Object $runManifest -Name "run_id" -Default ""
if (-not [string]::IsNullOrWhiteSpace([string]$expectedRunId)) {
    $runScopedTables = @(
        @{ Name = "benchmark_runs.csv"; Rows = $benchmarkRows; Column = "experiment_run_id" },
        @{ Name = "invocations.csv"; Rows = $invocationRows; Column = "experiment_run_id" },
        @{ Name = "workload_case_results.csv"; Rows = $workloadCaseRows; Column = "experiment_run_id" }
    )

    foreach ($table in $runScopedTables) {
        $observedRunIds = @(Get-UniqueColumnValues -Rows $table.Rows -ColumnName $table.Column)
        $unexpectedRunIds = @($observedRunIds | Where-Object { $_ -ne [string]$expectedRunId })
        if ($unexpectedRunIds.Count -gt 0) {
            throw "$($table.Name) contains rows from unexpected experiment_run_id values: $($unexpectedRunIds -join ', '). Expected only $expectedRunId."
        }
    }
}

$candidateById = @{}
foreach ($candidate in $candidateRows) {
    $candidateById[$candidate.id] = $candidate
}

$templateByFingerprint = @{}
foreach ($template in $queryTemplateRows) {
    if (-not [string]::IsNullOrWhiteSpace($template.template_fingerprint)) {
        $templateByFingerprint[$template.template_fingerprint] = $template
    }
}

$decisionByCandidateId = @{}
foreach ($decision in $decisionRows) {
    if (-not [string]::IsNullOrWhiteSpace($decision.candidate_id)) {
        $decisionByCandidateId[$decision.candidate_id] = $decision
    }
}

$pairSummaries = New-Object "System.Collections.Generic.List[object]"
$pairGroups = @($benchmarkRows | Where-Object { -not [string]::IsNullOrWhiteSpace($_.run_pair_id) } | Group-Object run_pair_id)
foreach ($group in $pairGroups) {
    $rows = @($group.Group)
    $baselineRows = @($rows | Where-Object { ConvertFrom-PostgresBool -Value $_.is_baseline })
    $candidateRunRows = @($rows | Where-Object { -not (ConvertFrom-PostgresBool -Value $_.is_baseline) })
    $phases = @($rows | Select-Object -ExpandProperty benchmark_phase -Unique)
    $templates = @($rows | Select-Object -ExpandProperty template_fingerprint -Unique)
    $parameterSets = @($rows | Select-Object -ExpandProperty parameter_set_id -Unique)

    $issues = New-Object "System.Collections.Generic.List[string]"
    if ($rows.Count -ne 2) {
        $issues.Add("expected_two_rows")
    }
    if ($baselineRows.Count -ne 1) {
        $issues.Add("expected_one_baseline")
    }
    if ($candidateRunRows.Count -ne 1) {
        $issues.Add("expected_one_candidate")
    }
    if ($phases.Count -ne 1) {
        $issues.Add("phase_mismatch")
    }
    if ($templates.Count -ne 1) {
        $issues.Add("template_mismatch")
    }
    if ($parameterSets.Count -ne 1) {
        $issues.Add("parameter_set_mismatch")
    }

    $baselineRow = if ($baselineRows.Count -gt 0) { $baselineRows[0] } else { $null }
    $candidateRunRow = if ($candidateRunRows.Count -gt 0) { $candidateRunRows[0] } else { $null }
    $metadata = Get-RunMetadata -Rows $rows
    $candidateId = if ($null -ne $candidateRunRow) { $candidateRunRow.candidate_id } else { "" }
    if ([string]::IsNullOrWhiteSpace($candidateId)) {
        $issues.Add("missing_candidate_id")
    }

    $baselineMedian = if ($null -ne $baselineRow) { ConvertTo-DoubleOrNull -Value $baselineRow.median_execution_time } else { $null }
    $candidateMedian = if ($null -ne $candidateRunRow) { ConvertTo-DoubleOrNull -Value $candidateRunRow.median_execution_time } else { $null }
    if ($null -eq $baselineMedian -or $null -eq $candidateMedian) {
        $issues.Add("missing_median_execution_time")
    }

    $improvementPct = $null
    $reward = $null
    if ($null -ne $baselineMedian -and $null -ne $candidateMedian -and $baselineMedian -gt 0) {
        $improvementPct = (($baselineMedian - $candidateMedian) / $baselineMedian) * 100.0
        $reward = Get-PairedReward -BaselineMs $baselineMedian -CandidateMs $candidateMedian
    }

    $baselineTimedOut = if ($null -ne $baselineRow) { ConvertFrom-PostgresBool -Value $baselineRow.timed_out } else { $false }
    $candidateTimedOut = if ($null -ne $candidateRunRow) { ConvertFrom-PostgresBool -Value $candidateRunRow.timed_out } else { $false }
    $baselineError = if ($null -ne $baselineRow) { $baselineRow.error_message } else { "" }
    $candidateError = if ($null -ne $candidateRunRow) { $candidateRunRow.error_message } else { "" }

    $pairSummaries.Add([pscustomobject]@{
        template_fingerprint = if ($templates.Count -gt 0) { $templates[0] } else { "" }
        workload_label = Get-ObjectPropertyValue -Object $metadata -Name "workload_label"
        query_file = Get-ObjectPropertyValue -Object $metadata -Name "query_file"
        benchmark_phase = if ($phases.Count -gt 0) { $phases[0] } else { "" }
        run_pair_id = $group.Name
        parameter_set_id = if ($parameterSets.Count -gt 0) { $parameterSets[0] } else { "" }
        candidate_id = $candidateId
        baseline_execution_order = if ($null -ne $baselineRow) { $baselineRow.execution_order } else { "" }
        candidate_execution_order = if ($null -ne $candidateRunRow) { $candidateRunRow.execution_order } else { "" }
        baseline_median_ms = $baselineMedian
        candidate_median_ms = $candidateMedian
        signed_improvement_pct = $improvementPct
        clipped_reward = $reward
        baseline_rows_returned = if ($null -ne $baselineRow) { $baselineRow.rows_returned } else { "" }
        candidate_rows_returned = if ($null -ne $candidateRunRow) { $candidateRunRow.rows_returned } else { "" }
        baseline_timed_out = $baselineTimedOut
        candidate_timed_out = $candidateTimedOut
        baseline_error = $baselineError
        candidate_error = $candidateError
        integrity_status = if ($issues.Count -eq 0) { "valid" } else { "invalid" }
        integrity_issues = ($issues -join ";")
    })
}

$validPairs = @($pairSummaries | Where-Object { $_.integrity_status -eq "valid" })
$random = [System.Random]::new($Seed)
$templateSummaries = New-Object "System.Collections.Generic.List[object]"
$templatePhaseGroups = @($validPairs | Group-Object template_fingerprint, candidate_id, benchmark_phase)

foreach ($group in $templatePhaseGroups) {
    $pairs = @($group.Group)
    $firstPair = $pairs[0]
    $candidate = if ($candidateById.ContainsKey($firstPair.candidate_id)) { $candidateById[$firstPair.candidate_id] } else { $null }
    $decision = if ($decisionByCandidateId.ContainsKey($firstPair.candidate_id)) { $decisionByCandidateId[$firstPair.candidate_id] } else { $null }
    $baselineValues = [double[]]@($pairs | ForEach-Object { [double]$_.baseline_median_ms })
    $candidateValues = [double[]]@($pairs | ForEach-Object { [double]$_.candidate_median_ms })
    $improvementValues = [double[]]@($pairs | ForEach-Object { [double]$_.signed_improvement_pct })
    $rewardValues = [double[]]@($pairs | ForEach-Object { [double]$_.clipped_reward })
    $baselineMedian = Get-Median -Values $baselineValues
    $candidateMedian = Get-Median -Values $candidateValues
    $medianImprovementPct = if ($baselineMedian -gt 0) { (($baselineMedian - $candidateMedian) / $baselineMedian) * 100.0 } else { 0.0 }
    $wilcoxon = Get-WilcoxonSignedRankStats -Pairs $pairs
    $bootstrap = Get-BootstrapMedianImprovementInterval -Pairs $pairs -Iterations $BootstrapIterations -Random $random
    $timeoutPairCount = @($pairs | Where-Object { $_.baseline_timed_out -or $_.candidate_timed_out }).Count
    $errorPairCount = @($pairs | Where-Object {
        -not [string]::IsNullOrWhiteSpace($_.baseline_error) -or
        -not [string]::IsNullOrWhiteSpace($_.candidate_error)
    }).Count

    $templateSummaries.Add([pscustomobject]@{
        template_fingerprint = $firstPair.template_fingerprint
        workload_label = $firstPair.workload_label
        query_file = $firstPair.query_file
        benchmark_phase = $firstPair.benchmark_phase
        candidate_id = $firstPair.candidate_id
        candidate_source_type = if ($null -ne $candidate) { $candidate.source_type } else { "" }
        candidate_source_detail = if ($null -ne $candidate) { $candidate.source_detail } else { "" }
        semantic_status = if ($null -ne $candidate) { $candidate.semantic_status } else { "" }
        promotion_status = if ($null -ne $candidate) { $candidate.promotion_status } else { "" }
        decision_type = if ($null -ne $decision) { $decision.decision_type } else { "" }
        decision_reason = if ($null -ne $decision) { $decision.reason } else { "" }
        pair_count = $pairs.Count
        timeout_pair_count = $timeoutPairCount
        error_pair_count = $errorPairCount
        baseline_median_ms = $baselineMedian
        baseline_p75_ms = Get-Percentile -Values $baselineValues -Percentile 75
        baseline_p95_ms = Get-Percentile -Values $baselineValues -Percentile 95
        candidate_median_ms = $candidateMedian
        candidate_p75_ms = Get-Percentile -Values $candidateValues -Percentile 75
        candidate_p95_ms = Get-Percentile -Values $candidateValues -Percentile 95
        median_improvement_pct = $medianImprovementPct
        mean_pair_improvement_pct = ($improvementValues | Measure-Object -Average).Average
        pair_improvement_p25_pct = Get-Percentile -Values $improvementValues -Percentile 25
        pair_improvement_p75_pct = Get-Percentile -Values $improvementValues -Percentile 75
        paired_reward_cv = Get-CoefficientOfVariation -Values $rewardValues
        wilcoxon_one_sided_p_value = $wilcoxon.p_value
        rank_biserial_correlation = $wilcoxon.rank_biserial
        wilcoxon_nonzero_pairs = $wilcoxon.nonzero_pairs
        bootstrap_median_improvement_ci_low_pct = $bootstrap.low
        bootstrap_median_improvement_ci_high_pct = $bootstrap.high
        bootstrap_iterations = $bootstrap.iterations
        improved_at_threshold = $medianImprovementPct -ge $effectiveImprovementThresholdPct
        statistically_significant = $wilcoxon.p_value -lt $Alpha
        reportable_positive_result = ($medianImprovementPct -ge $effectiveImprovementThresholdPct) -and ($wilcoxon.p_value -lt $Alpha)
    })
}

$invalidPairs = @($pairSummaries | Where-Object { $_.integrity_status -ne "valid" })
$heldOutSummaries = @($templateSummaries | Where-Object { $_.benchmark_phase -eq "held_out" })
$searchSummaries = @($templateSummaries | Where-Object { $_.benchmark_phase -eq "search" })
$promotedHeldOutSummaries = @($heldOutSummaries | Where-Object {
    $_.promotion_status -eq "promoted" -or $_.decision_type -eq "promote"
})
$h1Positive = @($promotedHeldOutSummaries | Where-Object { $_.reportable_positive_result })
$attemptedTemplateCount = if ($workloadCaseRows.Count -gt 0) {
    @($workloadCaseRows | Select-Object -ExpandProperty workload_label -Unique).Count
}
else {
    $promotedHeldOutSummaries.Count
}
$h1HitRate = if ($attemptedTemplateCount -gt 0) {
    ($h1Positive.Count / $attemptedTemplateCount) * 100.0
}
else {
    $null
}

$modelCandidateGroups = @($candidateRows | Group-Object source_type, source_detail)
$modelRows = New-Object "System.Collections.Generic.List[object]"
foreach ($group in $modelCandidateGroups) {
    $rows = @($group.Group)
    $safe = @($rows | Where-Object { $_.safety_status -eq "safe" }).Count
    $validated = @($rows | Where-Object { $_.semantic_status -eq "validated" }).Count
    $failed = @($rows | Where-Object { $_.semantic_status -eq "failed" }).Count
    $promoted = @($rows | Where-Object { $_.promotion_status -eq "promoted" }).Count
    $rejected = @($rows | Where-Object { -not [string]::IsNullOrWhiteSpace($_.rejection_reason) }).Count
    $rejectionReasons = @(
        $rows |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_.rejection_reason) } |
            Select-Object -ExpandProperty rejection_reason -Unique
    )
    $generated = $rows.Count
    $modelRows.Add([pscustomobject]@{
        source_type = $rows[0].source_type
        source_detail = $rows[0].source_detail
        candidates = $generated
        safe = $safe
        validated = $validated
        failed = $failed
        promoted = $promoted
        rejected_with_reason = $rejected
        rejection_reasons = ($rejectionReasons -join ";")
        valid_candidate_rate_pct = if ($generated -gt 0) { ($validated / $generated) * 100.0 } else { 0.0 }
        promotion_rate_pct = if ($generated -gt 0) { ($promoted / $generated) * 100.0 } else { 0.0 }
    })
}

$workloadOutcomeRows = New-Object "System.Collections.Generic.List[object]"
foreach ($group in @($workloadCaseRows | Group-Object status, outcome)) {
    $rows = @($group.Group)
    $workloadOutcomeRows.Add([pscustomobject]@{
        status = $rows[0].status
        outcome = $rows[0].outcome
        count = $rows.Count
        candidates_generated = Get-NumericColumnSum -Rows $rows -ColumnName "candidates_generated"
        candidates_returned = Get-NumericColumnSum -Rows $rows -ColumnName "candidates_returned"
        candidates_after_safety = Get-NumericColumnSum -Rows $rows -ColumnName "candidates_after_safety"
        benchmark_pairs = Get-NumericColumnSum -Rows $rows -ColumnName "benchmark_pairs"
        held_out_benchmark_pairs = Get-NumericColumnSum -Rows $rows -ColumnName "held_out_benchmark_pairs"
        failures = Get-NonEmptyColumnCount -Rows $rows -ColumnName "failure_reason"
    })
}

$candidateFunnel = [ordered]@{
    workload_case_rows = $workloadCaseRows.Count
    invocations = $invocationRows.Count
    candidates_generated_from_workload_cases = Get-NumericColumnSum -Rows $workloadCaseRows -ColumnName "candidates_generated"
    candidates_returned_from_workload_cases = Get-NumericColumnSum -Rows $workloadCaseRows -ColumnName "candidates_returned"
    candidates_rejected_from_workload_cases = Get-NumericColumnSum -Rows $workloadCaseRows -ColumnName "candidates_rejected"
    candidates_after_dedup_from_workload_cases = Get-NumericColumnSum -Rows $workloadCaseRows -ColumnName "candidates_after_dedup"
    candidates_after_safety_from_workload_cases = Get-NumericColumnSum -Rows $workloadCaseRows -ColumnName "candidates_after_safety"
    candidates_recorded = $candidateRows.Count
    candidates_safe = @($candidateRows | Where-Object { $_.safety_status -eq "safe" }).Count
    candidates_validated = @($candidateRows | Where-Object { $_.semantic_status -eq "validated" }).Count
    candidates_semantic_failed = @($candidateRows | Where-Object { $_.semantic_status -eq "failed" }).Count
    candidates_promoted = @($candidateRows | Where-Object { $_.promotion_status -eq "promoted" }).Count
    candidates_rejected_with_reason = @($candidateRows | Where-Object { -not [string]::IsNullOrWhiteSpace($_.rejection_reason) }).Count
}

$equivalenceSummaryRows = New-Object "System.Collections.Generic.List[object]"
foreach ($group in @($equivalenceRows | Group-Object method, passed)) {
    $rows = @($group.Group)
    $rowCountValues = [double[]]@(
        $rows |
            ForEach-Object { ConvertTo-DoubleOrNull -Value $_.rows_compared } |
            Where-Object { $null -ne $_ }
    )
    $equivalenceSummaryRows.Add([pscustomobject]@{
        method = $rows[0].method
        passed = ConvertFrom-PostgresBool -Value $rows[0].passed
        checks = $rows.Count
        rows_compared_total = Get-NumericColumnSum -Rows $rows -ColumnName "rows_compared"
        rows_compared_median = Get-Median -Values $rowCountValues
        execution_time_ms_median = Get-Median -Values ([double[]]@(
            $rows |
                ForEach-Object { ConvertTo-DoubleOrNull -Value $_.execution_time_ms } |
                Where-Object { $null -ne $_ }
        ))
    })
}

$failedEquivalenceRows = @($equivalenceRows | Where-Object { -not (ConvertFrom-PostgresBool -Value $_.passed) })
$negativeWorkloadRows = @($workloadCaseRows | Where-Object {
    $_.outcome -ne "promoted" -or
    $_.status -ne "completed" -or
    -not [string]::IsNullOrWhiteSpace($_.failure_reason)
})
$rejectedCandidateRows = @($candidateRows | Where-Object {
    $_.semantic_status -eq "failed" -or
    -not [string]::IsNullOrWhiteSpace($_.rejection_reason) -or
    $_.promotion_status -in @("demoted", "evicted")
})

$promotedCandidateIds = New-Object "System.Collections.Generic.HashSet[string]"
foreach ($candidate in @($candidateRows | Where-Object { $_.promotion_status -eq "promoted" })) {
    [void]$promotedCandidateIds.Add([string]$candidate.id)
}
foreach ($decision in @($decisionRows | Where-Object { $_.decision_type -eq "promote" })) {
    if (-not [string]::IsNullOrWhiteSpace($decision.candidate_id)) {
        [void]$promotedCandidateIds.Add([string]$decision.candidate_id)
    }
}

$promotedQueryRows = New-Object "System.Collections.Generic.List[object]"
foreach ($candidateId in @($promotedCandidateIds)) {
    if (-not $candidateById.ContainsKey($candidateId)) {
        continue
    }

    $candidate = $candidateById[$candidateId]
    $template = if ($templateByFingerprint.ContainsKey($candidate.template_fingerprint)) {
        $templateByFingerprint[$candidate.template_fingerprint]
    }
    else {
        $null
    }
    $matchingWorkloadCase = @(
        $workloadCaseRows |
            Where-Object {
                (Get-ObjectPropertyValue -Object $_ -Name "candidate_id" -Default "") -eq $candidateId -or
                (Get-ObjectPropertyValue -Object $_ -Name "template_fingerprint" -Default "") -eq $candidate.template_fingerprint
            } |
            Select-Object -First 1
    )
    $decision = if ($decisionByCandidateId.ContainsKey($candidateId)) {
        $decisionByCandidateId[$candidateId]
    }
    else {
        $null
    }

    $promotedQueryRows.Add([pscustomobject]@{
        candidate_id = $candidateId
        template_fingerprint = $candidate.template_fingerprint
        workload_label = if ($matchingWorkloadCase.Count -gt 0) {
            Get-ObjectPropertyValue -Object $matchingWorkloadCase[0] -Name "workload_label" -Default ""
        } else { "" }
        query_file = if ($matchingWorkloadCase.Count -gt 0) {
            Get-ObjectPropertyValue -Object $matchingWorkloadCase[0] -Name "query_file" -Default ""
        } else { "" }
        source_type = $candidate.source_type
        source_detail = $candidate.source_detail
        decision_reason = if ($null -ne $decision) { $decision.reason } else { "" }
        original_sql = Get-ObjectPropertyValue -Object $template -Name "normalized_sql" -Default ""
        promoted_sql = Get-ObjectPropertyValue -Object $candidate -Name "sql_text" -Default ""
    })
}

$metricsDirectory = Join-Path $InputDirectory "metrics"
$metricFileRows = @(
    if (Test-Path -LiteralPath $metricsDirectory -PathType Container) {
        Get-ChildItem -LiteralPath $metricsDirectory -File |
            Sort-Object Name |
            ForEach-Object {
                [pscustomobject]@{
                    file = $_.Name
                    size_bytes = $_.Length
                }
            }
    }
)

$invocationCountByTemplate = @{}
foreach ($group in @($invocationRows | Group-Object template_fingerprint)) {
    $invocationCountByTemplate[$group.Name] = $group.Count
}

$templatesWithMultipleInvocations = @($invocationCountByTemplate.GetEnumerator() | Where-Object { $_.Value -gt 1 }).Count
$templatesWithMultipleCandidates = @(
    $candidateRows |
        Group-Object template_fingerprint |
        Where-Object { $_.Count -gt 1 }
).Count

$planAnalysisRows = @($benchmarkRows | Where-Object {
    -not [string]::IsNullOrWhiteSpace($_.plan_json) -or
    -not [string]::IsNullOrWhiteSpace($_.plan_analysis)
})

$analysisSummary = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    input_directory = (Resolve-Path -LiteralPath $InputDirectory).Path
    output_directory = (Resolve-Path -LiteralPath $OutputDirectory).Path
    alpha = $Alpha
    improvement_threshold_pct = $effectiveImprovementThresholdPct
    bootstrap_iterations = $BootstrapIterations
    seed = $Seed
    run_manifest = $runManifest
    integrity = [ordered]@{
        benchmark_rows = $benchmarkRows.Count
        pair_groups = $pairGroups.Count
        valid_pairs = $validPairs.Count
        invalid_pairs = $invalidPairs.Count
        search_pairs = @($validPairs | Where-Object { $_.benchmark_phase -eq "search" }).Count
        held_out_pairs = @($validPairs | Where-Object { $_.benchmark_phase -eq "held_out" }).Count
    }
    workload_cases = [ordered]@{
        rows = $workloadCaseRows.Count
        by_outcome = $workloadOutcomeRows
    }
    candidate_funnel = $candidateFunnel
    equivalence = [ordered]@{
        rows = $equivalenceRows.Count
        passed = @($equivalenceRows | Where-Object { ConvertFrom-PostgresBool -Value $_.passed }).Count
        failed = $failedEquivalenceRows.Count
        by_method = $equivalenceSummaryRows
    }
    monitoring = [ordered]@{
        enabled = ConvertFrom-PostgresBool -Value (Get-ObjectPropertyValue -Object $runManifest -Name "monitoring_enabled" -Default $false)
        metrics_path = Get-ObjectPropertyValue -Object $runManifest -Name "monitoring_metrics_path" -Default ""
        metric_files = $metricFileRows
    }
    promoted_queries = $promotedQueryRows
    h1_improvement = [ordered]@{
        status = if ($promotedHeldOutSummaries.Count -gt 0) { "pilot_evidence_available" } else { "not_estimated" }
        attempted_templates = $attemptedTemplateCount
        promoted_templates_with_held_out = $promotedHeldOutSummaries.Count
        positive_templates = $h1Positive.Count
        hit_rate_pct = $h1HitRate
        limitation = "This is a controlled pilot summary unless the input directory contains the selected public/controlled benchmark corpus."
    }
    h2_convergence = [ordered]@{
        status = if ($templatesWithMultipleInvocations -gt 0 -and $templatesWithMultipleCandidates -gt 0) { "partial_evidence_available" } else { "not_estimated" }
        templates_with_multiple_invocations = $templatesWithMultipleInvocations
        templates_with_multiple_candidates = $templatesWithMultipleCandidates
        limitation = "Convergence requires repeated non-deterministic invocations and a growing candidate pool."
    }
    h3_regret = [ordered]@{
        status = if ($banditRows.Count -gt 1 -and $templatesWithMultipleCandidates -gt 0) { "partial_evidence_available" } else { "not_estimated" }
        bandit_state_rows = $banditRows.Count
        limitation = "Empirical regret needs multiple benchmark-selection rounds over at least two validated candidates per template."
    }
    h4_model_scale = [ordered]@{
        status = if (@($candidateRows | Where-Object { $_.source_type -eq "llm" }).Count -gt 0) { "candidate_source_summary_available" } else { "not_estimated" }
        source_groups = $modelRows
        limitation = "Model-scale claims require comparable local model runs at a fixed invocation budget."
    }
    h5_complementarity = [ordered]@{
        status = if ($planAnalysisRows.Count -gt 0) { "plan_artifacts_available" } else { "not_estimated" }
        benchmark_rows_with_plan_artifacts = $planAnalysisRows.Count
        limitation = "Complementarity needs EXPLAIN/plan evidence and optional PostgreSQL planner-toggle probes."
    }
}

$pairSummaryPath = Join-Path $OutputDirectory "controlled_pair_summary.csv"
$templateSummaryPath = Join-Path $OutputDirectory "controlled_template_summary.csv"
$sourceSummaryPath = Join-Path $OutputDirectory "controlled_candidate_source_summary.csv"
$jsonSummaryPath = Join-Path $OutputDirectory "controlled_analysis_summary.json"
$markdownSummaryPath = Join-Path $OutputDirectory "controlled_hypothesis_summary.md"

$pairSummaries | Export-Csv -LiteralPath $pairSummaryPath -NoTypeInformation -Encoding UTF8
$templateSummaries | Export-Csv -LiteralPath $templateSummaryPath -NoTypeInformation -Encoding UTF8
$modelRows | Export-Csv -LiteralPath $sourceSummaryPath -NoTypeInformation -Encoding UTF8
$analysisSummary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $jsonSummaryPath -Encoding UTF8

$summaryLines = New-Object "System.Collections.Generic.List[string]"
$summaryLines.Add("# Controlled Post-Hoc Analysis")
$summaryLines.Add("")
$summaryLines.Add("Input directory: ``$InputDirectory``")
$summaryLines.Add("")
$summaryLines.Add("Generated at: $($analysisSummary.generated_at)")
$summaryLines.Add("")
$summaryLines.Add("## Run Provenance")
$summaryLines.Add("")
$summaryLines.Add("| Field | Value |")
$summaryLines.Add("|-------|-------|")
$summaryLines.Add("| Run ID | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'run_id' -Default '')) |")
$summaryLines.Add("| Candidate source | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'candidate_source' -Default '')) |")
$summaryLines.Add("| Model | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'model' -Default 'n/a')) |")
$summaryLines.Add("| Scale factor | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'scale_factor' -Default 'n/a')) |")
$summaryLines.Add("| Workload manifest | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'workload_manifest_file' -Default '')) |")
$summaryLines.Add("| Search parameter sets/query | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'search_parameter_sets_per_query' -Default 'n/a')) |")
$summaryLines.Add("| Held-out parameter sets/query | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'held_out_parameter_sets_per_query' -Default 'n/a')) |")
$summaryLines.Add("| Benchmark iterations | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'benchmark_iterations' -Default 'n/a')) |")
$summaryLines.Add("| Minimum promotion pairs | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'minimum_promotion_pairs' -Default 'n/a')) |")
$summaryLines.Add("| Promotion alpha | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'promotion_alpha' -Default $Alpha)) |")
$summaryLines.Add("| Promotion improvement threshold % | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'promotion_min_improvement_pct' -Default $effectiveImprovementThresholdPct)) |")
$summaryLines.Add("| Git commit | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'git_commit' -Default 'n/a')) |")
$summaryLines.Add("| Git branch | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'git_branch' -Default 'n/a')) |")
$summaryLines.Add("| Git dirty | $(Format-MarkdownCell -Value (Get-ObjectPropertyValue -Object $runManifest -Name 'git_worktree_dirty' -Default 'n/a')) |")
$summaryLines.Add("")
$summaryLines.Add("## Integrity")
$summaryLines.Add("")
$summaryLines.Add("| Metric | Value |")
$summaryLines.Add("|--------|-------|")
$summaryLines.Add("| Benchmark rows | $($analysisSummary.integrity.benchmark_rows) |")
$summaryLines.Add("| Valid pairs | $($analysisSummary.integrity.valid_pairs) |")
$summaryLines.Add("| Invalid pairs | $($analysisSummary.integrity.invalid_pairs) |")
$summaryLines.Add("| Search pairs | $($analysisSummary.integrity.search_pairs) |")
$summaryLines.Add("| Held-out pairs | $($analysisSummary.integrity.held_out_pairs) |")
$summaryLines.Add("| Workload case rows | $($analysisSummary.workload_cases.rows) |")
$summaryLines.Add("")
$summaryLines.Add("## Workload Outcomes")
$summaryLines.Add("")
if ($workloadOutcomeRows.Count -gt 0) {
    $summaryLines.Add("| Status / outcome | Cases | Generated | Returned | Safety-passed | Search pairs | Held-out pairs | Failures |")
    $summaryLines.Add("|------------------|-------|-----------|----------|---------------|--------------|----------------|----------|")
    foreach ($row in $workloadOutcomeRows) {
        $statusOutcome = "$(Format-MarkdownCell -Value $row.status) / $(Format-MarkdownCell -Value $row.outcome)"
        $summaryLines.Add("| $statusOutcome | $($row.count) | $(Format-NullableNumber -Value $row.candidates_generated -Digits 0) | $(Format-NullableNumber -Value $row.candidates_returned -Digits 0) | $(Format-NullableNumber -Value $row.candidates_after_safety -Digits 0) | $(Format-NullableNumber -Value $row.benchmark_pairs -Digits 0) | $(Format-NullableNumber -Value $row.held_out_benchmark_pairs -Digits 0) | $($row.failures) |")
    }
}
else {
    $summaryLines.Add("No workload-case rows were exported.")
}
$summaryLines.Add("")
$summaryLines.Add("## Candidate Funnel")
$summaryLines.Add("")
$summaryLines.Add("| Stage | Count |")
$summaryLines.Add("|-------|-------|")
$summaryLines.Add("| Workload cases | $($candidateFunnel.workload_case_rows) |")
$summaryLines.Add("| Invocations | $($candidateFunnel.invocations) |")
$summaryLines.Add("| Candidates generated from workload cases | $(Format-NullableNumber -Value $candidateFunnel.candidates_generated_from_workload_cases -Digits 0) |")
$summaryLines.Add("| Candidates returned from workload cases | $(Format-NullableNumber -Value $candidateFunnel.candidates_returned_from_workload_cases -Digits 0) |")
$summaryLines.Add("| Candidates rejected from workload cases | $(Format-NullableNumber -Value $candidateFunnel.candidates_rejected_from_workload_cases -Digits 0) |")
$summaryLines.Add("| Candidates after dedup from workload cases | $(Format-NullableNumber -Value $candidateFunnel.candidates_after_dedup_from_workload_cases -Digits 0) |")
$summaryLines.Add("| Candidates after safety from workload cases | $(Format-NullableNumber -Value $candidateFunnel.candidates_after_safety_from_workload_cases -Digits 0) |")
$summaryLines.Add("| Candidate rows recorded | $($candidateFunnel.candidates_recorded) |")
$summaryLines.Add("| Safe candidates | $($candidateFunnel.candidates_safe) |")
$summaryLines.Add("| Semantically validated candidates | $($candidateFunnel.candidates_validated) |")
$summaryLines.Add("| Semantically failed candidates | $($candidateFunnel.candidates_semantic_failed) |")
$summaryLines.Add("| Promoted candidates | $($candidateFunnel.candidates_promoted) |")
$summaryLines.Add("| Candidates rejected with reason | $($candidateFunnel.candidates_rejected_with_reason) |")
$summaryLines.Add("")
$summaryLines.Add("## Candidate Source Breakdown")
$summaryLines.Add("")
if ($modelRows.Count -gt 0) {
    $summaryLines.Add("| Source | Detail | Candidates | Safe | Validated | Failed | Promoted | Validation rate % | Promotion rate % | Rejection reasons |")
    $summaryLines.Add("|--------|--------|------------|------|-----------|--------|----------|-------------------|------------------|-------------------|")
    foreach ($row in $modelRows) {
        $summaryLines.Add("| $(Format-MarkdownCell -Value $row.source_type) | $(Format-MarkdownCell -Value $row.source_detail) | $($row.candidates) | $($row.safe) | $($row.validated) | $($row.failed) | $($row.promoted) | $(Format-NullableNumber -Value $row.valid_candidate_rate_pct -Digits 2) | $(Format-NullableNumber -Value $row.promotion_rate_pct -Digits 2) | $(Format-MarkdownCell -Value $row.rejection_reasons) |")
    }
}
else {
    $summaryLines.Add("No candidate rows were exported.")
}
$summaryLines.Add("")
$summaryLines.Add("## Search-Phase Evidence")
$summaryLines.Add("")
if ($searchSummaries.Count -gt 0) {
    $summaryLines.Add("| Workload | Source | Pairs | Baseline median ms | Candidate median ms | Improvement % | p-value | 95% bootstrap CI % | Promotion status |")
    $summaryLines.Add("|----------|--------|-------|--------------------|---------------------|---------------|---------|--------------------|------------------|")
    foreach ($row in $searchSummaries) {
        $ci = "$(Format-NullableNumber -Value $row.bootstrap_median_improvement_ci_low_pct -Digits 2) to $(Format-NullableNumber -Value $row.bootstrap_median_improvement_ci_high_pct -Digits 2)"
        $source = "$(Format-MarkdownCell -Value $row.candidate_source_type):$(Format-MarkdownCell -Value $row.candidate_source_detail)"
        $summaryLines.Add("| $(Format-MarkdownCell -Value $row.workload_label) | $source | $($row.pair_count) | $(Format-NullableNumber -Value $row.baseline_median_ms -Digits 2) | $(Format-NullableNumber -Value $row.candidate_median_ms -Digits 2) | $(Format-NullableNumber -Value $row.median_improvement_pct -Digits 2) | $(Format-NullableNumber -Value $row.wilcoxon_one_sided_p_value -Digits 6) | $ci | $(Format-MarkdownCell -Value $row.promotion_status) |")
    }
}
else {
    $summaryLines.Add("No valid search-phase benchmark pairs were exported.")
}
$summaryLines.Add("")
$summaryLines.Add("## Held-Out Evidence")
$summaryLines.Add("")
if ($heldOutSummaries.Count -gt 0) {
    $summaryLines.Add("| Workload | Source | Pairs | Baseline median ms | Candidate median ms | Improvement % | p-value | 95% bootstrap CI % | Decision | Result |")
    $summaryLines.Add("|----------|--------|-------|--------------------|---------------------|---------------|---------|--------------------|----------|--------|")
    foreach ($row in $heldOutSummaries) {
        $result = if ($row.reportable_positive_result) { "positive pilot evidence" } else { "not significant or below threshold" }
        $ci = "$(Format-NullableNumber -Value $row.bootstrap_median_improvement_ci_low_pct -Digits 2) to $(Format-NullableNumber -Value $row.bootstrap_median_improvement_ci_high_pct -Digits 2)"
        $source = "$(Format-MarkdownCell -Value $row.candidate_source_type):$(Format-MarkdownCell -Value $row.candidate_source_detail)"
        $decision = if (-not [string]::IsNullOrWhiteSpace($row.decision_type)) { $row.decision_type } else { $row.promotion_status }
        $summaryLines.Add("| $(Format-MarkdownCell -Value $row.workload_label) | $source | $($row.pair_count) | $(Format-NullableNumber -Value $row.baseline_median_ms -Digits 2) | $(Format-NullableNumber -Value $row.candidate_median_ms -Digits 2) | $(Format-NullableNumber -Value $row.median_improvement_pct -Digits 2) | $(Format-NullableNumber -Value $row.wilcoxon_one_sided_p_value -Digits 6) | $ci | $(Format-MarkdownCell -Value $decision) | $result |")
    }
}
else {
    $summaryLines.Add("Not estimated: no valid held-out benchmark pairs were exported.")
}
$summaryLines.Add("")
$summaryLines.Add("## Promoted Query Text")
$summaryLines.Add("")
if ($promotedQueryRows.Count -gt 0) {
    foreach ($row in $promotedQueryRows) {
        $heading = if (-not [string]::IsNullOrWhiteSpace($row.workload_label)) {
            Format-MarkdownCell -Value $row.workload_label
        }
        else {
            Format-MarkdownCell -Value $row.template_fingerprint
        }
        $summaryLines.Add("### $heading")
        $summaryLines.Add("")
        $summaryLines.Add("| Field | Value |")
        $summaryLines.Add("|-------|-------|")
        $summaryLines.Add("| Candidate ID | $(Format-MarkdownCell -Value $row.candidate_id) |")
        $summaryLines.Add("| Template fingerprint | $(Format-MarkdownCell -Value $row.template_fingerprint) |")
        $summaryLines.Add("| Query file | $(Format-MarkdownCell -Value $row.query_file) |")
        $summaryLines.Add("| Source | $(Format-MarkdownCell -Value $row.source_type):$(Format-MarkdownCell -Value $row.source_detail) |")
        $summaryLines.Add("| Decision reason | $(Format-MarkdownCell -Value $row.decision_reason) |")
        $summaryLines.Add("")
        $summaryLines.Add("Original SQL:")
        $summaryLines.Add("")
        $summaryLines.Add("````sql")
        $summaryLines.Add((Format-CodeBlockText -Value $row.original_sql))
        $summaryLines.Add("````")
        $summaryLines.Add("")
        $summaryLines.Add("Promoted SQL:")
        $summaryLines.Add("")
        $summaryLines.Add("````sql")
        $summaryLines.Add((Format-CodeBlockText -Value $row.promoted_sql))
        $summaryLines.Add("````")
        $summaryLines.Add("")
    }
}
else {
    $summaryLines.Add("No promoted candidate SQL text was exported.")
    $summaryLines.Add("")
}
$summaryLines.Add("## H1 Improvement")
$summaryLines.Add("")
$summaryLines.Add("Attempted templates used for hit-rate denominator: $($analysisSummary.h1_improvement.attempted_templates)")
$summaryLines.Add("")
if ($promotedHeldOutSummaries.Count -gt 0) {
    $summaryLines.Add("| Workload | Pairs | Held-out improvement % | p-value | 95% bootstrap CI % | Result |")
    $summaryLines.Add("|----------|-------|------------------------|---------|--------------------|--------|")
    foreach ($row in $promotedHeldOutSummaries) {
        $result = if ($row.reportable_positive_result) { "positive pilot evidence" } else { "not significant or below threshold" }
        $ci = "$(Format-NullableNumber -Value $row.bootstrap_median_improvement_ci_low_pct -Digits 2) to $(Format-NullableNumber -Value $row.bootstrap_median_improvement_ci_high_pct -Digits 2)"
        $summaryLines.Add("| $($row.workload_label) | $($row.pair_count) | $(Format-NullableNumber -Value $row.median_improvement_pct -Digits 2) | $(Format-NullableNumber -Value $row.wilcoxon_one_sided_p_value -Digits 6) | $ci | $result |")
    }
}
else {
    $summaryLines.Add("Not estimated: no promoted candidate has held-out paired measurements in this export.")
}
$summaryLines.Add("")
$summaryLines.Add("## Equivalence Evidence")
$summaryLines.Add("")
$summaryLines.Add("| Metric | Value |")
$summaryLines.Add("|--------|-------|")
$summaryLines.Add("| Equivalence checks | $($analysisSummary.equivalence.rows) |")
$summaryLines.Add("| Passed checks | $($analysisSummary.equivalence.passed) |")
$summaryLines.Add("| Failed checks | $($analysisSummary.equivalence.failed) |")
$summaryLines.Add("")
if ($equivalenceSummaryRows.Count -gt 0) {
    $summaryLines.Add("| Method | Passed | Checks | Rows compared total | Rows compared median | Median execution ms |")
    $summaryLines.Add("|--------|--------|--------|---------------------|----------------------|---------------------|")
    foreach ($row in $equivalenceSummaryRows) {
        $summaryLines.Add("| $(Format-MarkdownCell -Value $row.method) | $($row.passed) | $($row.checks) | $(Format-NullableNumber -Value $row.rows_compared_total -Digits 0) | $(Format-NullableNumber -Value $row.rows_compared_median -Digits 0) | $(Format-NullableNumber -Value $row.execution_time_ms_median -Digits 2) |")
    }
}
else {
    $summaryLines.Add("No equivalence-check rows were exported.")
}
$summaryLines.Add("")
if ($failedEquivalenceRows.Count -gt 0) {
    $summaryLines.Add("Failed equivalence checks:")
    $summaryLines.Add("")
    $summaryLines.Add("| Candidate | Method | Check type | Original rows | Candidate rows | Rows compared | Mismatch detail |")
    $summaryLines.Add("|-----------|--------|------------|---------------|----------------|---------------|-----------------|")
    foreach ($row in $failedEquivalenceRows) {
        $summaryLines.Add("| $(Format-MarkdownCell -Value $row.candidate_id) | $(Format-MarkdownCell -Value $row.method) | $(Format-MarkdownCell -Value $row.check_type) | $(Format-MarkdownCell -Value $row.original_row_count) | $(Format-MarkdownCell -Value $row.candidate_row_count) | $(Format-MarkdownCell -Value $row.rows_compared) | $(Format-MarkdownCell -Value $row.mismatch_detail) |")
    }
    $summaryLines.Add("")
}
$summaryLines.Add("## Negative and Null Outcomes")
$summaryLines.Add("")
if ($negativeWorkloadRows.Count -gt 0) {
    $summaryLines.Add("Workload cases that did not produce a promoted outcome:")
    $summaryLines.Add("")
    $summaryLines.Add("| Workload | Status / outcome | Failure stage | Failure reason | Generated | Returned | Safety-passed | Benchmark pairs |")
    $summaryLines.Add("|----------|------------------|---------------|----------------|-----------|----------|---------------|-----------------|")
    foreach ($row in $negativeWorkloadRows) {
        $statusOutcome = "$(Format-MarkdownCell -Value $row.status) / $(Format-MarkdownCell -Value $row.outcome)"
        $summaryLines.Add("| $(Format-MarkdownCell -Value $row.workload_label) | $statusOutcome | $(Format-MarkdownCell -Value $row.failure_stage) | $(Format-MarkdownCell -Value $row.failure_reason) | $(Format-MarkdownCell -Value $row.candidates_generated) | $(Format-MarkdownCell -Value $row.candidates_returned) | $(Format-MarkdownCell -Value $row.candidates_after_safety) | $(Format-MarkdownCell -Value $row.benchmark_pairs) |")
    }
    $summaryLines.Add("")
}
else {
    $summaryLines.Add("No non-promoted workload outcomes were exported.")
    $summaryLines.Add("")
}
if ($rejectedCandidateRows.Count -gt 0) {
    $summaryLines.Add("Rejected, failed, demoted, or evicted candidates:")
    $summaryLines.Add("")
    $summaryLines.Add("| Candidate | Template | Source | Semantic status | Promotion status | Rejection reason |")
    $summaryLines.Add("|-----------|----------|--------|-----------------|------------------|------------------|")
    foreach ($row in $rejectedCandidateRows) {
        $source = "$(Format-MarkdownCell -Value $row.source_type):$(Format-MarkdownCell -Value $row.source_detail)"
        $summaryLines.Add("| $(Format-MarkdownCell -Value $row.id) | $(Format-MarkdownCell -Value $row.template_fingerprint) | $source | $(Format-MarkdownCell -Value $row.semantic_status) | $(Format-MarkdownCell -Value $row.promotion_status) | $(Format-MarkdownCell -Value $row.rejection_reason) |")
    }
    $summaryLines.Add("")
}
else {
    $summaryLines.Add("No rejected, failed, demoted, or evicted candidate rows were exported.")
    $summaryLines.Add("")
}
$summaryLines.Add("## Monitoring Evidence")
$summaryLines.Add("")
$summaryLines.Add("| Metric | Value |")
$summaryLines.Add("|--------|-------|")
$summaryLines.Add("| Monitoring enabled | $($analysisSummary.monitoring.enabled) |")
$summaryLines.Add("| Metrics path | $(Format-MarkdownCell -Value $analysisSummary.monitoring.metrics_path) |")
if ($metricFileRows.Count -gt 0) {
    $summaryLines.Add("| Metrics files | $($metricFileRows.Count) |")
    $summaryLines.Add("")
    $summaryLines.Add("| File | Size bytes |")
    $summaryLines.Add("|------|------------|")
    foreach ($row in $metricFileRows) {
        $summaryLines.Add("| $(Format-MarkdownCell -Value $row.file) | $($row.size_bytes) |")
    }
}
else {
    $summaryLines.Add("| Metrics files | not captured |")
}
$summaryLines.Add("")
$summaryLines.Add("## H2-H5 Status")
$summaryLines.Add("")
$summaryLines.Add("| Hypothesis | Status | Reason |")
$summaryLines.Add("|------------|--------|--------|")
$summaryLines.Add("| H2 convergence | $($analysisSummary.h2_convergence.status) | $($analysisSummary.h2_convergence.limitation) |")
$summaryLines.Add("| H3 empirical regret | $($analysisSummary.h3_regret.status) | $($analysisSummary.h3_regret.limitation) |")
$summaryLines.Add("| H4 model scale | $($analysisSummary.h4_model_scale.status) | $($analysisSummary.h4_model_scale.limitation) |")
$summaryLines.Add("| H5 complementarity | $($analysisSummary.h5_complementarity.status) | $($analysisSummary.h5_complementarity.limitation) |")
$summaryLines.Add("")
$summaryLines.Add("This report is a post-hoc analysis artifact. It does not turn a controlled pilot into a public benchmark result.")
$summaryLines | Set-Content -LiteralPath $markdownSummaryPath -Encoding UTF8

Write-Host "Controlled analysis artifacts written to $OutputDirectory"
Write-Host "  $pairSummaryPath"
Write-Host "  $templateSummaryPath"
Write-Host "  $sourceSummaryPath"
Write-Host "  $jsonSummaryPath"
Write-Host "  $markdownSummaryPath"
