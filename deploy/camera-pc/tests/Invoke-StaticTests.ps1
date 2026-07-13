param([string]$PackageRoot = (Split-Path -Parent $PSScriptRoot))

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

$scripts = @(Get-ChildItem -LiteralPath $PackageRoot -File -Recurse | Where-Object {
    $_.Extension -in @('.ps1', '.psm1')
})
Assert-True ($scripts.Count -ge 8) 'Expected camera agent scripts are missing.'
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
Assert-True ([int]$settings.minimumConfigSources -ge 20) 'Minimum source-entry floor must be at least 20.'
Assert-True ([int]$settings.minimumConfigPaths -ge 20) 'Minimum path floor must be at least 20.'
Assert-True ([int]$settings.majorityLossPercent -gt 50) 'Majority threshold must not classify one camera as an outage.'
Assert-True (@($settings.restartFailureBackoffMinutes).Count -ge 3) 'Persistent restart backoff ladder is missing.'
Assert-True (-not ([string]$settings.tailnetPeer -match '^100\.')) 'Repository settings must not bind to an unknown tailnet peer.'

$allText = ($scripts | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 }) -join "`n"
Assert-True ($allText -notmatch '192\.168\.7') 'Stale legacy subnet is present in the versioned agent.'
Assert-True ($allText -match 'Global\\ASYL-Camera-MediaMTX-Mutation') 'Shared global mutation mutex is missing.'
Assert-True ((Get-Content -LiteralPath (Join-Path $PackageRoot 'install.ps1') -Raw) -match 'New-ScheduledTaskTrigger -AtStartup') 'AtStartup trigger is missing.'
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
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

[PSCustomObject]@{
    Result = 'PASS'
    ParsedScripts = $scripts.Count
    DecisionCases = 10
    ConfigValidationCases = 5
} | ConvertTo-Json -Depth 3
