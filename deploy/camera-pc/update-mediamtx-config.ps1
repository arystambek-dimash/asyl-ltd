param(
    [Parameter(Mandatory = $true)][string]$CandidatePath,
    [string]$SettingsPath = (Join-Path $PSScriptRoot 'camera-agent.json'),
    [switch]$SkipRestart
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'CameraPc.Common.psm1') -Force

$settings = Get-CameraAgentSettings -Path $SettingsPath
$paths = Get-CameraAgentPaths -Settings $settings
Initialize-CameraAgentDataRoot -Paths $paths
$candidate = (Resolve-Path -LiteralPath $CandidatePath -ErrorAction Stop).Path

Invoke-WithCameraMutationLock -TimeoutSeconds 30 -ScriptBlock {
    # Snapshot and validate both current and candidate only after taking the
    # shared lock. This closes the 20->22 sync / stale 20-candidate TOCTOU race.
    $settings = Get-CameraAgentSettings -Path $SettingsPath
    $candidateSnapshot = Join-Path $paths.DataRoot ('.candidate-' + [guid]::NewGuid().ToString('N') + '.yml')
    try {
    $candidateContent = Get-Content -LiteralPath $candidate -Raw -Encoding UTF8
    Write-AtomicTextFile -Path $candidateSnapshot -Content $candidateContent
    $currentValidation = Test-MediaMtxConfig -Path $paths.Config
    $effectiveMinimumSources = [Math]::Max(
        [int]$settings.minimumConfigSources, [int]$currentValidation.SourceCount
    )
    $effectiveMinimumPaths = [Math]::Max(
        [int]$settings.minimumConfigPaths, [int]$currentValidation.PathCount
    )
    $effectiveExpectedSources = [Math]::Max(
        [int]$settings.expectedSources, [int]$currentValidation.EagerSourceCount
    )
    $validation = Test-MediaMtxConfig -Path $candidateSnapshot `
        -MinimumSourceCount $effectiveMinimumSources `
        -MinimumPathCount $effectiveMinimumPaths `
        -MinimumEagerSourceCount $effectiveExpectedSources
    if (-not $validation.Valid) {
        throw ('Candidate MediaMTX config rejected: ' + ($validation.Errors -join '; '))
    }

    $hadOriginal = Test-Path -LiteralPath $paths.Config
    $original = $null
    if ($hadOriginal) {
        $original = Get-Content -LiteralPath $paths.Config -Raw -Encoding UTF8
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $backupDirectory = Join-Path $paths.DataRoot 'config-backups'
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    if ($hadOriginal) {
        Copy-Item -LiteralPath $paths.Config `
            -Destination (Join-Path $backupDirectory "mediamtx-$stamp.yml") -Force
    }

    Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
        "CONFIG update begin candidate=$([IO.Path]::GetFileName($candidate)) sources=$($validation.SourceCount)"
    )
    try {
        Write-AtomicTextFile -Path $paths.Config -Content $candidateContent
        Enable-AiRecordingConfig -Path $paths.Config -MediaRoot ([string]$settings.mediaRoot) `
            -RetentionDays ([int]$settings.recordingRetentionDays) `
            -SegmentMinutes ([int]$settings.recordingSegmentMinutes) | Out-Null
        $installedValidation = Test-MediaMtxConfig -Path $paths.Config `
            -MinimumSourceCount $effectiveMinimumSources `
            -MinimumPathCount $effectiveMinimumPaths `
            -MinimumEagerSourceCount $effectiveExpectedSources
        if (-not $installedValidation.Valid) {
            throw ('Installed config failed validation: ' + ($installedValidation.Errors -join '; '))
        }

        if (-not $SkipRestart) {
            Stop-ScheduledTask -TaskName ([string]$settings.mediaTaskName) -ErrorAction SilentlyContinue
            Get-Process mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force
            Start-Sleep -Seconds 2
            Start-ScheduledTask -TaskName ([string]$settings.mediaTaskName) -ErrorAction Stop
            $ready = Wait-MediaMtxReady -Settings $settings -Paths $paths `
                -TimeoutSeconds ([int]$settings.restartReadyTimeoutSeconds)
            if ($null -eq $ready -or $ready.TaskState -ne 'Running' -or
                $ready.ProcessCount -ne 1 -or -not $ready.PortsOk) {
                throw 'MediaMTX did not become ready with the candidate config.'
            }
        }
        # Baselines are monotonic. A successfully expanded config raises the
        # floor so a later sync cannot silently drop the new cameras.
        $settings.expectedSources = [Math]::Max(
            [int]$settings.expectedSources, [int]$installedValidation.EagerSourceCount
        )
        $settings.minimumConfigSources = [Math]::Max(
            [int]$settings.minimumConfigSources, [int]$installedValidation.SourceCount
        )
        $settings.minimumConfigPaths = [Math]::Max(
            [int]$settings.minimumConfigPaths, [int]$installedValidation.PathCount
        )
        $settings.minimumConfigSources = [Math]::Max(
            [int]$settings.minimumConfigSources, [int]$settings.expectedSources
        )
        $settings.minimumConfigPaths = [Math]::Max(
            [int]$settings.minimumConfigPaths, [int]$settings.expectedSources
        )
        Write-CameraAgentLog -Paths $paths -Settings $settings -Message 'CONFIG update success'
        Write-AtomicJsonFile -Path $SettingsPath -Value $settings
    } catch {
        $updateError = $_.Exception.Message
        Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
            "CONFIG update failed; rollback begin error=$updateError"
        )
        if ($hadOriginal) {
            Write-AtomicTextFile -Path $paths.Config -Content $original
        } else {
            Remove-Item -LiteralPath $paths.Config -Force -ErrorAction SilentlyContinue
        }
        if (-not $SkipRestart -and $hadOriginal) {
            Stop-ScheduledTask -TaskName ([string]$settings.mediaTaskName) -ErrorAction SilentlyContinue
            Get-Process mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force
            Start-Sleep -Seconds 2
            Start-ScheduledTask -TaskName ([string]$settings.mediaTaskName) -ErrorAction SilentlyContinue
            $rollbackHealth = Wait-MediaMtxReady -Settings $settings -Paths $paths `
                -TimeoutSeconds ([int]$settings.restartReadyTimeoutSeconds)
            if ($null -eq $rollbackHealth -or -not $rollbackHealth.PortsOk) {
                Write-CameraAgentLog -Paths $paths -Settings $settings -Message 'CONFIG rollback restored file but MediaMTX is not ready'
            } else {
                Write-CameraAgentLog -Paths $paths -Settings $settings -Message 'CONFIG rollback success'
            }
        }
        throw "MediaMTX config update rolled back: $updateError"
    }
    } finally {
        Remove-Item -LiteralPath $candidateSnapshot -Force -ErrorAction SilentlyContinue
    }
}

Start-ScheduledTask -TaskName 'MediaMTX-Supervisor' -ErrorAction SilentlyContinue
