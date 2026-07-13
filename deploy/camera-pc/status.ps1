param(
    [string]$SettingsPath = (Join-Path $PSScriptRoot 'camera-agent.json'),
    [switch]$AsJson,
    [int]$LogLines = 15
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'CameraPc.Common.psm1') -Force

$settings = Get-CameraAgentSettings -Path $SettingsPath
$paths = Get-CameraAgentPaths -Settings $settings
$media = Get-MediaMtxHealth -Settings $settings -Paths $paths
$tailscale = Get-TailscaleHealth -Settings $settings
$expected = Get-ExpectedCameraSourceCount -Settings $settings -Paths $paths
$config = Test-MediaMtxConfig -Path $paths.Config `
    -MinimumSourceCount ([int]$settings.minimumConfigSources) `
    -MinimumPathCount ([int]$settings.minimumConfigPaths) `
    -MinimumEagerSourceCount ([int]$settings.expectedSources)
$state = Read-CameraAgentState -Paths $paths
$supervisorTask = $null
$supervisorInfo = $null
try { $supervisorTask = Get-ScheduledTask -TaskName 'MediaMTX-Supervisor' -ErrorAction Stop } catch {}
try { $supervisorInfo = Get-ScheduledTaskInfo -TaskName 'MediaMTX-Supervisor' -ErrorAction Stop } catch {}

$result = [ordered]@{
    CheckedAt = (Get-Date).ToString('o')
    AgentStatus = $state.LastStatus
    AgentReason = $state.LastReason
    LastCheckAt = $state.LastCheckAt
    NextRestartAllowedAt = $state.NextRestartAllowedAt
    LastRestartOutcome = $state.LastRestartOutcome
    RestartFailureCount = [int]$state.RestartFailureCount
    TotalRestartAttempts = [int]$state.TotalRestartAttempts
    TotalRestartSuccesses = [int]$state.TotalRestartSuccesses
    SupervisorTask = [ordered]@{
        State = $(if ($supervisorTask) { [string]$supervisorTask.State } else { 'missing' })
        LastRunTime = $(if ($supervisorInfo) { $supervisorInfo.LastRunTime } else { $null })
        LastTaskResult = $(if ($supervisorInfo) { $supervisorInfo.LastTaskResult } else { $null })
        NextRunTime = $(if ($supervisorInfo) { $supervisorInfo.NextRunTime } else { $null })
    }
    MediaMTX = [ordered]@{
        TaskState = $media.TaskState
        ProcessCount = $media.ProcessCount
        ProcessId = $media.ProcessId
        PortsOk = $media.PortsOk
        MissingPorts = @($media.MissingPorts)
        Sources = $media.SourceCount
        ExpectedSources = $expected
    }
    Tailscale = $tailscale
    Config = [ordered]@{
        Valid = $config.Valid
        SourceCount = $config.SourceCount
        EagerSourceCount = $config.EagerSourceCount
        PathCount = $config.PathCount
        MinimumSourceCount = [int]$settings.minimumConfigSources
        MinimumPathCount = [int]$settings.minimumConfigPaths
        Errors = @($config.Errors)
    }
    StatePath = $paths.State
    LogPath = $paths.Log
}

if ($AsJson) {
    $result | ConvertTo-Json -Depth 8
} else {
    $result | ConvertTo-Json -Depth 8
    if (Test-Path -LiteralPath $paths.Log) {
        ''
        "--- last $LogLines agent log lines ---"
        Get-Content -LiteralPath $paths.Log -Tail $LogLines -Encoding UTF8
    }
}
