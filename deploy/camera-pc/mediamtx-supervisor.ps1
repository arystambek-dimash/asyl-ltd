param(
    [switch]$ForceRepair,
    [string]$SettingsPath = (Join-Path $PSScriptRoot 'camera-agent.json')
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'CameraPc.Common.psm1') -Force
$settings = Get-CameraAgentSettings -Path $SettingsPath
$paths = Get-CameraAgentPaths -Settings $settings
Initialize-CameraAgentDataRoot -Paths $paths

function Convert-ToNullableDate([object]$Value) {
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) { return $null }
    try { return [datetime]$Value } catch { return $null }
}

function Save-HealthSnapshot($State, $Health, $Tailscale, [int]$Expected, [bool]$NvrReachable) {
    $State.LastCheckAt = (Get-Date).ToString('o')
    $State.LastHealth = [ordered]@{
        TaskState = $Health.TaskState
        ProcessCount = [int]$Health.ProcessCount
        ProcessId = [int]$Health.ProcessId
        PortsOk = [bool]$Health.PortsOk
        MissingPorts = @($Health.MissingPorts)
        SourceCount = [int]$Health.SourceCount
        ExpectedSources = $Expected
        NvrReachable = $NvrReachable
    }
    $State.Tailscale = [ordered]@{
        Healthy = [bool]$Tailscale.Healthy
        ServiceState = $Tailscale.ServiceState
        BackendState = $Tailscale.BackendState
        Addresses = @($Tailscale.Addresses)
        Peer = $Tailscale.Peer
        PeerRequired = [bool]$Tailscale.PeerRequired
        PeerReachable = $Tailscale.PeerReachable
        Error = $Tailscale.Error
    }
}

function Try-RepairTailscale($State, $CurrentHealth) {
    if ([bool]$CurrentHealth.Healthy) { return $CurrentHealth }

    $now = Get-Date
    $lastAttempt = Convert-ToNullableDate $State.LastTailscaleRepairAt
    $cooldown = [int]$settings.tailscaleRepairCooldownMinutes
    if ($lastAttempt -and $lastAttempt.AddMinutes($cooldown) -gt $now) {
        return $CurrentHealth
    }

    # Persist before touching the service, so a failed/hung repair cannot be
    # retried every minute after the task is killed.
    $State.LastTailscaleRepairAt = $now.ToString('o')
    Write-CameraAgentState -Paths $paths -State $State
    Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
        "TAILSCALE repair begin service=$($CurrentHealth.ServiceState) " +
        "backend=$($CurrentHealth.BackendState) peer=$($CurrentHealth.PeerReachable)"
    )
    try {
        if ($CurrentHealth.ServiceState -eq 'Running') {
            # A Running SCM state is not enough: tailscaled can have no backend
            # session/address or a dead required peer path. Restarting under a
            # persisted cooldown repairs that otherwise permanent split-brain.
            Restart-Service -Name Tailscale -Force -ErrorAction Stop
        } else {
            Start-Service -Name Tailscale -ErrorAction Stop
        }
        Start-Sleep -Seconds 6
    } catch {
        Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
            'TAILSCALE repair failed: ' + $_.Exception.Message
        )
    }
    $repaired = Get-TailscaleHealth -Settings $settings
    Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
        "TAILSCALE repair result healthy=$($repaired.Healthy) " +
        "service=$($repaired.ServiceState) backend=$($repaired.BackendState) " +
        "peer=$($repaired.PeerReachable)"
    )
    return $repaired
}

function Invoke-MediaMtxRestart($State, [string]$Reason, [bool]$RequireSourceRecovery, [int]$Expected) {
    $now = Get-Date
    $State.LastRestartAttemptAt = $now.ToString('o')
    $State.LastRestartOutcome = 'attempting'
    $State.TotalRestartAttempts = [int]$State.TotalRestartAttempts + 1
    $attemptBackoff = Get-RestartBackoffMinutes -Settings $settings -FailureCount ([int]$State.RestartFailureCount)
    $State.NextRestartAllowedAt = $now.AddMinutes($attemptBackoff).ToString('o')
    $State.LastStatus = 'repairing'
    $State.LastReason = $Reason
    Write-CameraAgentState -Paths $paths -State $State
    Write-CameraAgentLog -Paths $paths -Settings $settings -Message "RESTART begin reason=$Reason"

    $ready = $null
    $failure = $null
    try {
        Stop-ScheduledTask -TaskName ([string]$settings.mediaTaskName) -ErrorAction SilentlyContinue
        Get-Process mediamtx -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep -Seconds 2
        Start-ScheduledTask -TaskName ([string]$settings.mediaTaskName) -ErrorAction Stop
        $ready = Wait-MediaMtxReady -Settings $settings -Paths $paths `
            -TimeoutSeconds ([int]$settings.restartReadyTimeoutSeconds)
        if ($null -eq $ready -or $ready.TaskState -ne 'Running' -or
            $ready.ProcessCount -ne 1 -or -not $ready.PortsOk) {
            $failure = 'MediaMTX process/listeners did not become ready.'
        }

        if (-not $failure -and $RequireSourceRecovery -and $Expected -gt 0) {
            $deadline = (Get-Date).AddSeconds([int]$settings.sourceRecoveryTimeoutSeconds)
            do {
                $ready = Get-MediaMtxHealth -Settings $settings -Paths $paths
                $missing = [Math]::Max(0, $Expected - [int]$ready.SourceCount)
                $loss = [int][Math]::Floor(($missing * 100.0) / $Expected)
                if ($loss -lt [int]$settings.majorityLossPercent) { break }
                Start-Sleep -Seconds 3
            } while ((Get-Date) -lt $deadline)
            if ($loss -ge [int]$settings.majorityLossPercent) {
                $failure = "Restart did not heal majority source loss ($($ready.SourceCount)/$Expected)."
            }
        }
    } catch {
        $failure = $_.Exception.Message
    }

    if ($failure) {
        $State.RestartFailureCount = [int]$State.RestartFailureCount + 1
        $State.LastRestartFailureAt = (Get-Date).ToString('o')
        $State.LastRestartOutcome = 'failed'
        $backoff = Get-RestartBackoffMinutes -Settings $settings -FailureCount ([int]$State.RestartFailureCount)
        $State.NextRestartAllowedAt = (Get-Date).AddMinutes($backoff).ToString('o')
        $State.LastStatus = 'outage'
        $State.LastReason = 'restart-failed'
        Write-CameraAgentState -Paths $paths -State $State
        Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
            "RESTART failed failures=$($State.RestartFailureCount) next=$($State.NextRestartAllowedAt) error=$failure"
        )
        return [PSCustomObject]@{ Success = $false; Health = $ready; Error = $failure }
    }

    $State.RestartFailureCount = 0
    $State.LastRestartSuccessAt = (Get-Date).ToString('o')
    $State.LastRestartOutcome = 'success'
    $State.NextRestartAllowedAt = (Get-Date).AddMinutes([int]$settings.restartCooldownMinutes).ToString('o')
    $State.TotalRestartSuccesses = [int]$State.TotalRestartSuccesses + 1
    $State.ConsecutiveMajoritySourceFailures = 0
    $State.LastStatus = 'restarted'
    $State.LastReason = $Reason
    Write-CameraAgentState -Paths $paths -State $State
    Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
        "RESTART success pid=$($ready.ProcessId) sources=$($ready.SourceCount)/$Expected"
    )
    return [PSCustomObject]@{ Success = $true; Health = $ready; Error = $null }
}

try {
    Invoke-WithCameraMutationLock -TimeoutSeconds 0 -ScriptBlock {
        $now = Get-Date
        $state = Read-CameraAgentState -Paths $paths
        $health = Get-MediaMtxHealth -Settings $settings -Paths $paths
        $expected = Get-ExpectedCameraSourceCount -Settings $settings -Paths $paths
        $nvrIp = Get-NvrAddressFromConfig -Paths $paths
        $nvrReachable = ($null -ne $nvrIp -and (Test-CameraTcpPort -Address $nvrIp -Port 554))
        $tailscale = Get-TailscaleHealth -Settings $settings
        $tailscale = Try-RepairTailscale -State $state -CurrentHealth $tailscale

        $missing = [Math]::Max(0, $expected - [int]$health.SourceCount)
        $lossPercent = 0
        if ($expected -gt 0) {
            $lossPercent = [int][Math]::Floor(($missing * 100.0) / $expected)
        }
        $majorityLoss = ($expected -gt 0 -and $lossPercent -ge [int]$settings.majorityLossPercent)
        if ($majorityLoss -and $nvrReachable) {
            $state.ConsecutiveMajoritySourceFailures = [int]$state.ConsecutiveMajoritySourceFailures + 1
        } else {
            $state.ConsecutiveMajoritySourceFailures = 0
        }

        $nextAllowed = Convert-ToNullableDate $state.NextRestartAllowedAt
        $restartOutcome = [string]$state.LastRestartOutcome
        $failureBackoffActive = (
            $restartOutcome -in @('attempting', 'failed') -and
            $null -ne $nextAllowed -and $nextAllowed -gt $now
        )
        $lastSuccess = Convert-ToNullableDate $state.LastRestartSuccessAt
        $hardSuccessCooldown = (
            $null -ne $lastSuccess -and
            $lastSuccess.AddMinutes([int]$settings.hardFailureCooldownMinutes) -gt $now
        )
        $sourceSuccessCooldown = (
            $null -ne $lastSuccess -and
            $lastSuccess.AddMinutes([int]$settings.restartCooldownMinutes) -gt $now
        )
        # A normal source-repair cooldown must never delay recovery from a
        # subsequently dead process/listener. Only the one-minute hard guard or
        # a persisted failed/incomplete attempt applies to hard failures.
        $hardCooldownActive = ($failureBackoffActive -or $hardSuccessCooldown)
        $sourceCooldownActive = ($failureBackoffActive -or $sourceSuccessCooldown)
        $decision = Get-CameraRepairDecision `
            -Health $health `
            -ExpectedSources $expected `
            -MajorityLossPercent ([int]$settings.majorityLossPercent) `
            -ConsecutiveMajorityFailures ([int]$state.ConsecutiveMajoritySourceFailures) `
            -FailureThreshold ([int]$settings.sourceFailureThreshold) `
            -NvrReachable $nvrReachable `
            -HardCooldownActive $hardCooldownActive `
            -SourceCooldownActive $sourceCooldownActive `
            -ForceRepair:$ForceRepair

        Save-HealthSnapshot -State $state -Health $health -Tailscale $tailscale `
            -Expected $expected -NvrReachable $nvrReachable

        if ($decision.Action -eq 'restart') {
            $restart = Invoke-MediaMtxRestart -State $state -Reason $decision.Reason `
                -RequireSourceRecovery $decision.MajorityLoss -Expected $expected
            if ($null -ne $restart.Health) {
                $health = $restart.Health
                Save-HealthSnapshot -State $state -Health $health -Tailscale $tailscale `
                    -Expected $expected -NvrReachable $nvrReachable
                Write-CameraAgentState -Paths $paths -State $state
            }
            if (-not $restart.Success) { exit 1 }
            exit 0
        }

        $newStatus = 'healthy'
        if ($decision.Action -eq 'suppress') { $newStatus = 'cooldown' }
        elseif ($decision.Reason -eq 'nvr-unreachable') { $newStatus = 'nvr-unreachable' }
        elseif ($decision.Action -in @('alert', 'observe')) { $newStatus = 'degraded' }
        elseif (-not $tailscale.Healthy) { $newStatus = 'tailscale-degraded' }

        $previousStatus = [string]$state.LastStatus
        $state.LastStatus = $newStatus
        $state.LastReason = $decision.Reason
        $heartbeatDue = $true
        $lastHeartbeat = Convert-ToNullableDate $state.LastHeartbeatAt
        if ($lastHeartbeat) {
            $heartbeatDue = ($lastHeartbeat.AddMinutes([int]$settings.heartbeatMinutes) -le $now)
        }
        if ($previousStatus -ne $newStatus -or $heartbeatDue) {
            Write-CameraAgentLog -Paths $paths -Settings $settings -Message (
                "STATUS $newStatus reason=$($decision.Reason) pid=$($health.ProcessId) " +
                "ports=$($health.PortsOk) sources=$($health.SourceCount)/$expected " +
                "nvr=$nvrReachable tailscale=$($tailscale.Healthy)"
            )
            $state.LastHeartbeatAt = $now.ToString('o')
        }
        Write-CameraAgentState -Paths $paths -State $state
    }
} catch {
    if ($_.Exception.Message -eq 'Camera mutation lock is busy.') { exit 0 }
    try {
        Write-CameraAgentLog -Paths $paths -Settings $settings -Message ('ERROR ' + $_.Exception.Message)
    } catch {}
    exit 1
}
