param(
    [string]$SettingsPath = (Join-Path $PSScriptRoot 'camera-agent.json'),
    [string]$SyncScript,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$SyncArguments
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'CameraPc.Common.psm1') -Force

$settings = Get-CameraAgentSettings -Path $SettingsPath
$paths = Get-CameraAgentPaths -Settings $settings
Initialize-CameraAgentDataRoot -Paths $paths
if ([string]::IsNullOrWhiteSpace($SyncScript)) {
    $SyncScript = Join-Path ([string]$settings.mediaRoot) 'nvr-sync.ps1'
}
if (-not (Test-Path -LiteralPath $SyncScript -PathType Leaf)) {
    throw "NVR sync script not found: $SyncScript"
}

$snapshot = @{
    HadConfig = $false
    Before = $null
    BeforeHash = $null
    MinimumSources = [int]$settings.minimumConfigSources
    MinimumPaths = [int]$settings.minimumConfigPaths
    ExpectedSources = [int]$settings.expectedSources
}
try {
    Invoke-WithCameraMutationLock -TimeoutSeconds 30 -ScriptBlock {
        $snapshot.HadConfig = Test-Path -LiteralPath $paths.Config
        if ($snapshot.HadConfig) {
            $snapshot.Before = Get-Content -LiteralPath $paths.Config -Raw -Encoding UTF8
            $snapshot.BeforeHash = (Get-FileHash -LiteralPath $paths.Config -Algorithm SHA256).Hash
            $beforeValidation = Test-MediaMtxConfig -Path $paths.Config
            $snapshot.MinimumSources = [Math]::Max(
                [int]$snapshot.MinimumSources, [int]$beforeValidation.SourceCount
            )
            $snapshot.MinimumPaths = [Math]::Max(
                [int]$snapshot.MinimumPaths, [int]$beforeValidation.PathCount
            )
            $snapshot.ExpectedSources = [Math]::Max(
                [int]$snapshot.ExpectedSources, [int]$beforeValidation.EagerSourceCount
            )
        }

        Write-CameraAgentLog -Paths $paths -Settings $settings -Message 'NVR-SYNC begin'
        $powershellExe = Join-Path $PSHOME 'powershell.exe'
        if (-not (Test-Path -LiteralPath $powershellExe -PathType Leaf)) {
            throw "Windows PowerShell executable not found: $powershellExe"
        }
        $global:LASTEXITCODE = 0
        # The legacy script is untrusted from a control-flow perspective: its
        # normal discovery-failure path uses `exit 1`. Running it in-process
        # would terminate this wrapper before config validation and rollback.
        # A child powershell.exe contains `exit` and gives us a durable code.
        & $powershellExe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File $SyncScript @SyncArguments
        $syncExitCode = $LASTEXITCODE
        if ($null -eq $syncExitCode) { $syncExitCode = 0 }
        if ([int]$syncExitCode -ne 0) {
            throw "NVR sync exited with code $syncExitCode."
        }

        if (-not (Test-Path -LiteralPath $paths.Config -PathType Leaf)) {
            throw 'NVR sync removed mediamtx.yml.'
        }
        Enable-AiRecordingConfig -Path $paths.Config -MediaRoot ([string]$settings.mediaRoot) `
            -RetentionDays ([int]$settings.recordingRetentionDays) `
            -SegmentMinutes ([int]$settings.recordingSegmentMinutes) | Out-Null
        $afterHash = (Get-FileHash -LiteralPath $paths.Config -Algorithm SHA256).Hash
        $changed = ($snapshot.BeforeHash -ne $afterHash)
        if ($changed) {
            $validation = Test-MediaMtxConfig -Path $paths.Config `
                -MinimumSourceCount ([int]$snapshot.MinimumSources) `
                -MinimumPathCount ([int]$snapshot.MinimumPaths) `
                -MinimumEagerSourceCount ([int]$snapshot.ExpectedSources)
            if (-not $validation.Valid) {
                throw ('NVR sync produced invalid config: ' + ($validation.Errors -join '; '))
            }

            # Normalize process ownership after old sync versions that launch
            # mediamtx.exe directly instead of going through Task Scheduler.
            Stop-ScheduledTask -TaskName ([string]$settings.mediaTaskName) -ErrorAction SilentlyContinue
            Get-Process mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force
            Start-Sleep -Seconds 2
            Start-ScheduledTask -TaskName ([string]$settings.mediaTaskName) -ErrorAction Stop
            $health = Wait-MediaMtxReady -Settings $settings -Paths $paths `
                -TimeoutSeconds ([int]$settings.restartReadyTimeoutSeconds)
            if ($null -eq $health -or $health.TaskState -ne 'Running' -or
                $health.ProcessCount -ne 1 -or -not $health.PortsOk) {
                throw 'MediaMTX did not become ready after NVR sync.'
            }
            $settings.expectedSources = [Math]::Max(
                [int]$settings.expectedSources, [int]$validation.EagerSourceCount
            )
            $settings.minimumConfigSources = [Math]::Max(
                [int]$settings.minimumConfigSources, [int]$validation.SourceCount
            )
            $settings.minimumConfigPaths = [Math]::Max(
                [int]$settings.minimumConfigPaths, [int]$validation.PathCount
            )
            $settings.minimumConfigSources = [Math]::Max(
                [int]$settings.minimumConfigSources, [int]$settings.expectedSources
            )
            $settings.minimumConfigPaths = [Math]::Max(
                [int]$settings.minimumConfigPaths, [int]$settings.expectedSources
            )
        }
        Write-CameraAgentLog -Paths $paths -Settings $settings -Message "NVR-SYNC success changed=$changed"
        if ($changed) {
            Write-AtomicJsonFile -Path $SettingsPath -Value $settings
        }
    }
} catch {
    $syncError = $_.Exception.Message
    try {
        Invoke-WithCameraMutationLock -TimeoutSeconds 30 -ScriptBlock {
            $configExists = Test-Path -LiteralPath $paths.Config -PathType Leaf
            $configChanged = $false
            if ($snapshot.HadConfig) {
                if (-not $configExists) {
                    $configChanged = $true
                } else {
                    $currentHash = (Get-FileHash -LiteralPath $paths.Config -Algorithm SHA256).Hash
                    $configChanged = ($currentHash -ne $snapshot.BeforeHash)
                }
            } elseif ($configExists) {
                $configChanged = $true
            }

            if ($configChanged -and $snapshot.HadConfig -and $null -ne $snapshot.Before) {
                Write-AtomicTextFile -Path $paths.Config -Content $snapshot.Before
                Stop-ScheduledTask -TaskName ([string]$settings.mediaTaskName) -ErrorAction SilentlyContinue
                Get-Process mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force
                Start-Sleep -Seconds 2
                Start-ScheduledTask -TaskName ([string]$settings.mediaTaskName) -ErrorAction SilentlyContinue
                Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
                    "NVR-SYNC failed; previous config restored error=$syncError"
                )
            } elseif ($configChanged -and -not $snapshot.HadConfig) {
                Remove-Item -LiteralPath $paths.Config -Force -ErrorAction SilentlyContinue
                Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
                    "NVR-SYNC failed; newly-created config removed error=$syncError"
                )
            } else {
                # A normal discovery failure (for example NVR offline) exits 1
                # without changing mediamtx.yml. Do not disrupt healthy streams
                # by restarting MediaMTX in that case.
                Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
                    "NVR-SYNC failed; config unchanged, restart suppressed error=$syncError"
                )
            }
        }
    } catch {}
    exit 1
}

Start-ScheduledTask -TaskName 'MediaMTX-Supervisor' -ErrorAction SilentlyContinue
