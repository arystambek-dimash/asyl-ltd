param([string]$PackageRoot = (Split-Path -Parent $PSScriptRoot))

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

$scripts = @(Get-ChildItem -LiteralPath $PackageRoot -File -Recurse | Where-Object {
    $_.Extension -in @('.ps1', '.psm1')
})
Assert-True ($scripts.Count -ge 10) 'Expected camera agent and AI service scripts are missing.'
foreach ($script in $scripts) {
    $tokens = $null
    $parseErrors = $null
    [Management.Automation.Language.Parser]::ParseFile(
        $script.FullName, [ref]$tokens, [ref]$parseErrors
    ) | Out-Null
    Assert-True ($parseErrors.Count -eq 0) (
        "$($script.Name) parse errors: " + (($parseErrors | ForEach-Object { $_.Message }) -join '; ')
    )
}

$settingsPath = Join-Path $PackageRoot 'camera-agent.json'
$settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-True ([int]$settings.expectedSources -eq 20) 'Site baseline must remain 20 main+sub sources.'
Assert-True (9996 -in @([int[]]$settings.requiredPorts)) 'Local recording playback port is not health-checked.'
Assert-True ([int]$settings.recordingRetentionDays -eq 14) 'AI recording retention must default to 14 days.'
Assert-True ([int]$settings.minimumConfigSources -ge 20) 'Minimum source-entry floor must be at least 20.'
Assert-True ([int]$settings.minimumConfigPaths -ge 20) 'Minimum path floor must be at least 20.'
Assert-True ([int]$settings.majorityLossPercent -gt 50) 'Majority threshold must not classify one camera as an outage.'
Assert-True (@($settings.restartFailureBackoffMinutes).Count -ge 3) 'Persistent restart backoff ladder is missing.'
Assert-True (-not ([string]$settings.tailnetPeer -match '^100\.')) 'Repository settings must not bind to an unknown tailnet peer.'

$allText = ($scripts | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 }) -join "`n"
Assert-True ($allText -notmatch '192\.168\.7') 'Stale legacy subnet is present in the versioned agent.'
Assert-True ($allText -match 'Global\\ASYL-Camera-MediaMTX-Mutation') 'Shared global mutation mutex is missing.'
Assert-True ((Get-Content -LiteralPath (Join-Path $PackageRoot 'install.ps1') -Raw) -match 'New-ScheduledTaskTrigger -AtStartup') 'AtStartup trigger is missing.'
$aiInstallerText = Get-Content -LiteralPath (Join-Path $PackageRoot 'install-ai-service.ps1') -Raw -Encoding UTF8
$aiRunnerText = Get-Content -LiteralPath (Join-Path $PackageRoot 'run-ai-service.ps1') -Raw -Encoding UTF8
Assert-True ($aiInstallerText -match "taskName = 'ASYL-AI-Service'") 'AI boot task is missing.'
Assert-True ($aiInstallerText -match 'New-ScheduledTaskTrigger -AtStartup') 'AI service AtStartup trigger is missing.'
Assert-True ($aiInstallerText -match 'AI_SERVICE_API_KEY_SHA256') 'AI service digest is not installed.'
Assert-True ($aiInstallerText -notmatch 'AI_SERVICE_API_KEY\s*=') 'AI installer stores a plaintext API key.'
Assert-True ($aiRunnerText -match "Plaintext AI_SERVICE_API_KEY is forbidden") 'AI runner does not reject plaintext secrets.'
Assert-True ($aiInstallerText -match 'New-NetFirewallRule' -and $aiInstallerText -match 'RemoteAddress \$BackendTailnetIp') 'AI service firewall is not restricted to backend Tailscale IP.'
Assert-True ($aiInstallerText -match 'run-ai-service.ps1.*-ValidateOnly') 'Model/encoder validation before task registration is missing.'
$installerText = Get-Content -LiteralPath (Join-Path $PackageRoot 'install.ps1') -Raw -Encoding UTF8
Assert-True ($installerText -match 'Protect-CameraAgentPath -Path \$InstallRoot') 'SYSTEM script InstallRoot ACL protection is missing.'
Assert-True ($installerText -match 'Protect-CameraAgentPath -Path \$backupRoot') 'Backup ACL protection is missing.'
Assert-True ($installerText -match 'S-1-5-18' -and $installerText -match 'S-1-5-32-544') 'ACL must grant only SYSTEM and Administrators.'
Assert-True ($installerText -match 'SetAccessRuleProtection\(\$true, \$false\)') 'ACL inheritance must be removed, not preserved.'
Assert-True ($installerText -match 'Get-ChildItem -LiteralPath \$Path -Recurse') 'ACL hardening must recursively replace child script ACLs.'
Assert-True ($installerText -match '\$actualAllowSids' -and $installerText -match '\$missingAllows') 'Recursive ACL verification is missing.'
Assert-True ($installerText -match 'Disable-ScheduledTask -TaskName \(\[string\]\$packageSettings\.syncTaskName\)') 'Installer must quiesce legacy NVR sync before baseline validation.'
Assert-True ($installerText -match 'SyncTaskWasRunning') 'Installer manifest must preserve the running sync state.'
$syncWrapperText = Get-Content -LiteralPath (Join-Path $PackageRoot 'run-nvr-sync.ps1') -Raw -Encoding UTF8
Assert-True ($syncWrapperText -match '\$configChanged\s*=\s*\(\$currentHash\s+-ne\s+\$snapshot\.BeforeHash\)') 'NVR sync rollback is not guarded by a real config hash change.'
Assert-True ($syncWrapperText -match 'config unchanged, restart suppressed') 'NVR-offline/no-change restart suppression is missing.'
Assert-True ($syncWrapperText -match '\$beforeValidation\.SourceCount') 'NVR sync does not preserve the current config baseline.'
Assert-True ($syncWrapperText -match '(?s)&\s+\$powershellExe.*?-File\s+\$SyncScript') 'Legacy NVR sync is not isolated in a child PowerShell process.'
Assert-True ($syncWrapperText -notmatch '(?m)^\s*& \$SyncScript\b') 'Legacy NVR sync can still exit the protection wrapper in-process.'
$supervisorText = Get-Content -LiteralPath (Join-Path $PackageRoot 'mediamtx-supervisor.ps1') -Raw -Encoding UTF8
Assert-True ($supervisorText -match 'if \(\[bool\]\$CurrentHealth\.Healthy\)') 'Tailscale repair incorrectly trusts only the Windows service state.'
Assert-True ($supervisorText -match 'Restart-Service -Name Tailscale') 'Running-but-unhealthy Tailscale is not restarted.'
$configUpdaterText = Get-Content -LiteralPath (Join-Path $PackageRoot 'update-mediamtx-config.ps1') -Raw -Encoding UTF8
Assert-True ($configUpdaterText -match '\$currentValidation\.SourceCount') 'Manual config update does not preserve the current config baseline.'
$updateLockIndex = $configUpdaterText.IndexOf('Invoke-WithCameraMutationLock')
$updateValidationIndex = $configUpdaterText.IndexOf('$currentValidation = Test-MediaMtxConfig')
Assert-True ($updateLockIndex -ge 0 -and $updateValidationIndex -gt $updateLockIndex) 'Manual config baseline validation occurs before the mutation lock.'
Assert-True ($configUpdaterText -match 'candidateSnapshot') 'Manual candidate is not snapshotted and revalidated under the lock.'

Import-Module (Join-Path $PackageRoot 'CameraPc.Common.psm1') -Force
function New-Health([string]$Task = 'Running', [int]$Processes = 1, [bool]$Ports = $true, [int]$Sources = 10) {
    [PSCustomObject]@{
        TaskState = $Task
        ProcessCount = $Processes
        PortsOk = $Ports
        SourceCount = $Sources
    }
}

$healthy = Get-CameraRepairDecision -Health (New-Health) -ExpectedSources 10 `
    -MajorityLossPercent 60 -ConsecutiveMajorityFailures 0 -FailureThreshold 3 `
    -NvrReachable $true -HardCooldownActive $false -SourceCooldownActive $false
Assert-True ($healthy.Action -eq 'none') 'Healthy MediaMTX must not restart.'

$oneMissing = Get-CameraRepairDecision -Health (New-Health -Sources 9) -ExpectedSources 10 `
    -MajorityLossPercent 60 -ConsecutiveMajorityFailures 0 -FailureThreshold 3 `
    -NvrReachable $true -HardCooldownActive $false -SourceCooldownActive $false
Assert-True ($oneMissing.Action -eq 'alert') 'One missing camera must only mark degradation.'
Assert-True (-not $oneMissing.MajorityLoss) 'One missing camera was incorrectly classified as majority loss.'

# Production config keeps main+sub RTSP connections, so ten physical cameras
# can legitimately report expectedSources=20. Losing one camera (two sources)
# must still remain a non-disruptive degradation.
$onePhysicalMissingAtTwenty = Get-CameraRepairDecision -Health (New-Health -Sources 18) -ExpectedSources 20 `
    -MajorityLossPercent 60 -ConsecutiveMajorityFailures 0 -FailureThreshold 3 `
    -NvrReachable $true -HardCooldownActive $false -SourceCooldownActive $false
Assert-True ($onePhysicalMissingAtTwenty.Action -eq 'alert') '18/20 sources must not restart all cameras.'
Assert-True (-not $onePhysicalMissingAtTwenty.MajorityLoss) '18/20 sources was incorrectly classified as majority loss.'

$majorityPending = Get-CameraRepairDecision -Health (New-Health -Sources 4) -ExpectedSources 10 `
    -MajorityLossPercent 60 -ConsecutiveMajorityFailures 2 -FailureThreshold 3 `
    -NvrReachable $true -HardCooldownActive $false -SourceCooldownActive $false
Assert-True ($majorityPending.Action -eq 'observe') 'Majority loss must be debounced before repair.'

$majorityRepair = Get-CameraRepairDecision -Health (New-Health -Sources 4) -ExpectedSources 10 `
    -MajorityLossPercent 60 -ConsecutiveMajorityFailures 3 -FailureThreshold 3 `
    -NvrReachable $true -HardCooldownActive $false -SourceCooldownActive $false
Assert-True ($majorityRepair.Action -eq 'restart') 'Persistent majority loss must trigger one controlled repair.'

$majorityRepairAtTwenty = Get-CameraRepairDecision -Health (New-Health -Sources 8) -ExpectedSources 20 `
    -MajorityLossPercent 60 -ConsecutiveMajorityFailures 3 -FailureThreshold 3 `
    -NvrReachable $true -HardCooldownActive $false -SourceCooldownActive $false
Assert-True ($majorityRepairAtTwenty.Action -eq 'restart') '8/20 persistent sources must trigger controlled repair.'

$nvrOutage = Get-CameraRepairDecision -Health (New-Health -Sources 0) -ExpectedSources 10 `
    -MajorityLossPercent 60 -ConsecutiveMajorityFailures 3 -FailureThreshold 3 `
    -NvrReachable $false -HardCooldownActive $false -SourceCooldownActive $false
Assert-True ($nvrOutage.Action -eq 'alert') 'NVR outage must suppress MediaMTX restart.'

$hardFailure = Get-CameraRepairDecision -Health (New-Health -Task 'Ready' -Processes 0 -Ports $false -Sources 0) `
    -ExpectedSources 10 -MajorityLossPercent 60 -ConsecutiveMajorityFailures 0 `
    -FailureThreshold 3 -NvrReachable $true -HardCooldownActive $false -SourceCooldownActive $false
Assert-True ($hardFailure.Action -eq 'restart') 'Hard process/listener failure must repair immediately.'

$hardCooldown = Get-CameraRepairDecision -Health (New-Health -Task 'Ready' -Processes 0 -Ports $false -Sources 0) `
    -ExpectedSources 10 -MajorityLossPercent 60 -ConsecutiveMajorityFailures 0 `
    -FailureThreshold 3 -NvrReachable $true -HardCooldownActive $true -SourceCooldownActive $true
Assert-True ($hardCooldown.Action -eq 'suppress') 'Persisted cooldown must stop a hard-failure restart storm.'

$hardDuringSourceCooldown = Get-CameraRepairDecision `
    -Health (New-Health -Task 'Ready' -Processes 0 -Ports $false -Sources 0) `
    -ExpectedSources 20 -MajorityLossPercent 60 -ConsecutiveMajorityFailures 0 `
    -FailureThreshold 3 -NvrReachable $true `
    -HardCooldownActive $false -SourceCooldownActive $true
Assert-True ($hardDuringSourceCooldown.Action -eq 'restart') 'Source cooldown must not delay hard-failure recovery.'

$tempRoot = Join-Path ([IO.Path]::GetTempPath()) ('camera-agent-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
try {
    $full = Join-Path $tempRoot 'full.yml'
    $expanded = Join-Path $tempRoot 'expanded.yml'
    $truncated = Join-Path $tempRoot 'truncated.yml'
    $bad = Join-Path $tempRoot 'bad.yml'
    $inlineFull = Join-Path $tempRoot 'inline-full.yml'
    $inlineTruncated = Join-Path $tempRoot 'inline-truncated.yml'
    $inlineInvalid = Join-Path $tempRoot 'inline-invalid.yml'
    $onDemandInheritance = Join-Path $tempRoot 'on-demand-inheritance.yml'
    $flowFull = Join-Path $tempRoot 'flow-full.yml'
    $flowTruncated = Join-Path $tempRoot 'flow-truncated.yml'
    $flowInvalid = Join-Path $tempRoot 'flow-invalid.yml'
    $flowSecretInvalid = Join-Path $tempRoot 'flow-secret-invalid.yml'
    $recordingConfig = Join-Path $tempRoot 'recording.yml'
    $fullLines = New-Object System.Collections.Generic.List[string]
    $fullLines.Add('paths:') | Out-Null
    foreach ($number in 1..20) {
        $fullLines.Add("  cam$($number):") | Out-Null
        $fullLines.Add("    source: rtsp://viewer:placeholder@192.0.2.10:554/stream$number") | Out-Null
        $fullLines.Add('    sourceOnDemand: no') | Out-Null
    }
    [IO.File]::WriteAllText($full, (($fullLines -join "`n") + "`n"))
    $expandedLines = New-Object System.Collections.Generic.List[string]
    $expandedLines.Add('paths:') | Out-Null
    foreach ($number in 1..22) {
        $expandedLines.Add("  cam$($number):") | Out-Null
        $expandedLines.Add("    source: rtsp://viewer:placeholder@192.0.2.10:554/stream$number") | Out-Null
        $expandedLines.Add('    sourceOnDemand: no') | Out-Null
    }
    [IO.File]::WriteAllText($expanded, (($expandedLines -join "`n") + "`n"))
    [IO.File]::WriteAllText($truncated, "paths:`n  cam1:`n    source: rtsp://viewer:placeholder@192.0.2.10:554/stream`n    sourceOnDemand: no`n")
    [IO.File]::WriteAllText($bad, "paths:`n  cam1:`n    source: invalid-value`n  cam1:`n    source: rtsp://192.0.2.10:554/stream`n")
    $inlineLines = New-Object System.Collections.Generic.List[string]
    $inlineLines.Add('pathDefaults:') | Out-Null
    $inlineLines.Add('  sourceOnDemand: no') | Out-Null
    $inlineLines.Add('paths:') | Out-Null
    foreach ($number in 1..20) {
        $inlineLines.Add("  cam$($number): rtsp://viewer:placeholder@192.0.2.10:554/stream$number") | Out-Null
    }
    $inlineLines.Add('  all_others:') | Out-Null
    [IO.File]::WriteAllText($inlineFull, (($inlineLines -join "`n") + "`n"))
    [IO.File]::WriteAllText(
        $inlineTruncated,
        "pathDefaults:`n  sourceOnDemand: no`npaths:`n  cam1: rtsp://viewer:placeholder@192.0.2.10:554/stream`n  all_others:`n"
    )
    [IO.File]::WriteAllText(
        $inlineInvalid,
        "pathDefaults:`n  sourceOnDemand: no`npaths:`n  cam1: definitely-not-an-rtsp-uri`n  all_others:`n"
    )
    [IO.File]::WriteAllText(
        $onDemandInheritance,
        "pathDefaults:`n  sourceOnDemand: yes`npaths:`n  cam1: rtsp://viewer:placeholder@192.0.2.10:554/stream1`n  cam2:`n    source: rtsp://viewer:placeholder@192.0.2.10:554/stream2`n    sourceOnDemand: no`n  all_others:`n"
    )
    $flowLines = New-Object System.Collections.Generic.List[string]
    $flowLines.Add('pathDefaults:') | Out-Null
    $flowLines.Add('  sourceOnDemand: yes') | Out-Null
    $flowLines.Add('paths:') | Out-Null
    foreach ($number in 1..20) {
        if (($number % 3) -eq 1) {
            $flowLines.Add("  cam$($number): { source: `"rtsp://viewer:FLOW_FIXTURE@192.0.2.10:554/stream$number`", sourceOnDemand: no } # live entry") | Out-Null
        } elseif (($number % 3) -eq 2) {
            $flowLines.Add("  cam$($number): { sourceOnDemand: 'no', source: 'rtsp://viewer:FLOW_FIXTURE@192.0.2.10:554/stream$number' } # property order swapped") | Out-Null
        } else {
            $flowLines.Add("  cam$($number): { source: rtsp://viewer:FLOW_FIXTURE@192.0.2.10:554/stream$number, sourceOnDemand: false } # unquoted source") | Out-Null
        }
    }
    $flowLines.Add('  all_others:') | Out-Null
    [IO.File]::WriteAllText($flowFull, (($flowLines -join "`n") + "`n"))
    [IO.File]::WriteAllText(
        $flowTruncated,
        "pathDefaults:`n  sourceOnDemand: yes`npaths:`n  cam1: { source: `"rtsp://viewer:FLOW_FIXTURE@192.0.2.10:554/stream1`", sourceOnDemand: no } # live entry`n  all_others:`n"
    )
    [IO.File]::WriteAllText(
        $flowInvalid,
        "paths:`n  cam1: { source: rtsp://viewer:FLOW_INVALID_LEAK@192.0.2.10:554/stream1 sourceOnDemand: no } # missing comma`n  cam2: { source: `"rtsp://viewer:FLOW_INVALID_LEAK@192.0.2.10:554/stream2`",, sourceOnDemand: no } # doubled comma`n  all_others:`n"
    )
    [IO.File]::WriteAllText(
        $flowSecretInvalid,
        "paths:`n  cam1: { sourceOnDemand: no, source: `"secretproto://leak-user:FLOW_LEAK_PASSWORD@example.invalid/stream`" } # unsupported and secret`n  cam2: { source: `"rtsp://viewer:placeholder@192.0.2.10:554/stream2`", sourceOnDemand: `"FLOW_LEAK_PASSWORD`" }`n"
    )
    [IO.File]::WriteAllText(
        $recordingConfig,
        "api: no`nplayback: no`npaths:`n  cam1:`n    source: rtsp://viewer:placeholder@192.0.2.10:554/stream`n"
    )
    Enable-AiRecordingConfig -Path $recordingConfig -MediaRoot 'C:\mediamtx' `
        -RetentionDays 14 -SegmentMinutes 5 | Out-Null
    $recordingText = Get-Content -LiteralPath $recordingConfig -Raw -Encoding UTF8
    Assert-True ($recordingText -match '(?m)^playback: yes$') 'Playback server was not enabled.'
    Assert-True ($recordingText -match 'recordDeleteAfter: 336h') 'Two-week retention was not configured.'
    Assert-True ($recordingText.Contains('"~^cam[A-Za-z0-9_]*ai$":')) 'AI-only recording path is missing.'
    Enable-AiRecordingConfig -Path $recordingConfig -MediaRoot 'C:\mediamtx' `
        -RetentionDays 14 -SegmentMinutes 5 | Out-Null
    $markerCount = ([regex]::Matches(
        (Get-Content -LiteralPath $recordingConfig -Raw -Encoding UTF8),
        'ASYL-AI-RECORDING-BEGIN'
    )).Count
    Assert-True ($markerCount -eq 1) 'AI recording config is not idempotent.'
    $floorArgs = @{
        MinimumSourceCount = [int]$settings.minimumConfigSources
        MinimumPathCount = [int]$settings.minimumConfigPaths
        MinimumEagerSourceCount = [int]$settings.expectedSources
    }
    Assert-True ((Test-MediaMtxConfig -Path $full @floorArgs).Valid) 'Full 20-source config was rejected.'
    $expandedResult = Test-MediaMtxConfig -Path $expanded @floorArgs
    Assert-True ($expandedResult.Valid -and $expandedResult.SourceCount -eq 22) 'Expanded current-baseline fixture is invalid.'
    Assert-True (-not (Test-MediaMtxConfig -Path $full -MinimumSourceCount 22 -MinimumPathCount 22 -MinimumEagerSourceCount 22).Valid) '20-source candidate was allowed to shrink a 22-source current baseline.'
    Assert-True ((Test-MediaMtxConfig -Path $truncated).Valid) 'Truncated fixture must remain syntactically valid for this regression test.'
    $truncatedResult = Test-MediaMtxConfig -Path $truncated @floorArgs
    Assert-True (-not $truncatedResult.Valid) 'Syntactically valid one-camera config bypassed protected floors.'
    $fakePaths = [PSCustomObject]@{ Config = $truncated; Inventory = (Join-Path $tempRoot 'missing.json') }
    Assert-True ((Get-ExpectedCameraSourceCount -Settings $settings -Paths $fakePaths) -eq 20) 'Truncated config redefined supervisor expectation below 20.'
    Assert-True (-not (Test-MediaMtxConfig -Path $bad).Valid) 'Invalid/duplicate config was accepted.'
    $inlineFullResult = Test-MediaMtxConfig -Path $inlineFull @floorArgs
    Assert-True ($inlineFullResult.Valid) 'Live-style 20-source inline config was rejected.'
    Assert-True ($inlineFullResult.SourceCount -eq 20) 'Empty all_others was incorrectly counted as a source.'
    Assert-True ($inlineFullResult.EagerSourceCount -eq 20) 'pathDefaults sourceOnDemand:no was not inherited by inline paths.'
    Assert-True ($inlineFullResult.PathCount -eq 21) 'Inline path inventory did not include 20 cameras plus all_others.'
    Assert-True ((Test-MediaMtxConfig -Path $inlineTruncated).Valid) 'Truncated inline fixture must be syntactically valid.'
    Assert-True (-not (Test-MediaMtxConfig -Path $inlineTruncated @floorArgs).Valid) 'Truncated inline config bypassed protected floors.'
    Assert-True (-not (Test-MediaMtxConfig -Path $inlineInvalid).Valid) 'Invalid inline RTSP source was accepted.'
    $inheritanceResult = Test-MediaMtxConfig -Path $onDemandInheritance
    Assert-True ($inheritanceResult.Valid) 'Valid sourceOnDemand inheritance fixture was rejected.'
    Assert-True ($inheritanceResult.SourceCount -eq 2 -and $inheritanceResult.EagerSourceCount -eq 1) 'pathDefaults/per-path sourceOnDemand override was calculated incorrectly.'
    $flowFullResult = Test-MediaMtxConfig -Path $flowFull @floorArgs
    Assert-True ($flowFullResult.Valid) 'Live 20-source flow-mapping config was rejected.'
    Assert-True ($flowFullResult.SourceCount -eq 20) 'Flow mapping sources were not counted correctly.'
    Assert-True ($flowFullResult.EagerSourceCount -eq 20) 'Flow sourceOnDemand overrides were not applied.'
    Assert-True ($flowFullResult.PathCount -eq 21) 'Flow path inventory did not include 20 cameras plus all_others.'
    Assert-True ((Test-MediaMtxConfig -Path $flowTruncated).Valid) 'Truncated flow fixture must remain syntactically valid.'
    Assert-True (-not (Test-MediaMtxConfig -Path $flowTruncated @floorArgs).Valid) 'Truncated flow config bypassed protected floors.'
    $flowInvalidResult = Test-MediaMtxConfig -Path $flowInvalid
    $flowInvalidErrors = @($flowInvalidResult.Errors) -join ' '
    Assert-True (-not $flowInvalidResult.Valid) 'Malformed flow mapping was accepted.'
    Assert-True ($flowInvalidErrors -notmatch '(?i)FLOW_INVALID_LEAK|rtsp://') 'Malformed-flow errors leaked a source URI or credentials.'
    $flowSecretResult = Test-MediaMtxConfig -Path $flowSecretInvalid
    $flowSecretErrors = @($flowSecretResult.Errors) -join ' '
    Assert-True (-not $flowSecretResult.Valid) 'Invalid secret-bearing flow mapping was accepted.'
    Assert-True ($flowSecretResult.SourceCount -eq 2) 'Secret regression did not exercise parsed flow source values.'
    Assert-True ($flowSecretErrors -notmatch '(?i)FLOW_LEAK_PASSWORD|leak-user|secretproto|example\.invalid|rtsp://') 'Config validation errors leaked a source URI or credentials.'
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

[PSCustomObject]@{
    Result = 'PASS'
    ParsedScripts = $scripts.Count
    DecisionCases = 10
    ConfigValidationCases = 15
} | ConvertTo-Json -Depth 3
