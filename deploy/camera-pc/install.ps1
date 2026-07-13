param(
    [string]$MediaRoot = 'C:\mediamtx',
    [string]$InstallRoot = 'C:\mediamtx\camera-agent',
    [string]$TailnetPeer
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run install.ps1 from an elevated PowerShell window.'
}

$packageRoot = $PSScriptRoot
$requiredPackageFiles = @(
    'CameraPc.Common.psm1', 'camera-agent.json', 'mediamtx-supervisor.ps1',
    'run-nvr-sync.ps1', 'update-mediamtx-config.ps1', 'status.ps1', 'rollback.ps1'
)
foreach ($file in $requiredPackageFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $packageRoot $file) -PathType Leaf)) {
        throw "Camera agent package is incomplete: $file"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $MediaRoot 'mediamtx.exe') -PathType Leaf)) {
    throw "mediamtx.exe not found under $MediaRoot"
}
if (-not (Get-ScheduledTask -TaskName 'MediaMTX' -ErrorAction SilentlyContinue)) {
    throw 'Required MediaMTX scheduled task does not exist.'
}
if ((Test-Path -LiteralPath $InstallRoot) -and
    (((Get-Item -LiteralPath $InstallRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
    throw "Refusing reparse-point InstallRoot for SYSTEM scripts: $InstallRoot"
}

Import-Module (Join-Path $packageRoot 'CameraPc.Common.psm1') -Force
$packageSettings = Get-CameraAgentSettings -Path (Join-Path $packageRoot 'camera-agent.json')
$packageSettings.mediaRoot = $MediaRoot
$installedSettings = $null
$installedSettingsPath = Join-Path $InstallRoot 'camera-agent.json'
if (Test-Path -LiteralPath $installedSettingsPath -PathType Leaf) {
    try { $installedSettings = Get-CameraAgentSettings -Path $installedSettingsPath } catch {}
}
if ($PSBoundParameters.ContainsKey('TailnetPeer')) {
    $packageSettings.tailnetPeer = $TailnetPeer
} elseif ($installedSettings) {
    $packageSettings.tailnetPeer = [string]$installedSettings.tailnetPeer
}
if ($installedSettings) {
    $packageSettings.expectedSources = [Math]::Max(
        [int]$packageSettings.expectedSources, [int]$installedSettings.expectedSources
    )
    $packageSettings.minimumConfigSources = [Math]::Max(
        [int]$packageSettings.minimumConfigSources, [int]$installedSettings.minimumConfigSources
    )
    $packageSettings.minimumConfigPaths = [Math]::Max(
        [int]$packageSettings.minimumConfigPaths, [int]$installedSettings.minimumConfigPaths
    )
}
$paths = Get-CameraAgentPaths -Settings $packageSettings
Initialize-CameraAgentDataRoot -Paths $paths

function Export-TaskIfPresent([string]$TaskName, [string]$Destination) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { return $false }
    [IO.File]::WriteAllText($Destination, (Export-ScheduledTask -TaskName $TaskName), (New-Object Text.UTF8Encoding($false)))
    return $true
}

function Get-SafeFileName([string]$Value) {
    return ([regex]::Replace($Value, '[^A-Za-z0-9_.-]', '_'))
}

function Protect-CameraAgentPath([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Cannot protect missing path: $Path"
    }
    $systemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $adminsSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $allowedSids = @('S-1-5-18', 'S-1-5-32-544')
    $items = @((Get-Item -LiteralPath $Path -Force)) +
        @(Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction Stop)
    foreach ($item in $items) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to secure reparse point in SYSTEM script tree: $($item.FullName)"
        }
        if ($item.PSIsContainer) {
            $acl = New-Object Security.AccessControl.DirectorySecurity
            $inheritance = (
                [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
                [Security.AccessControl.InheritanceFlags]::ObjectInherit
            )
        } else {
            $acl = New-Object Security.AccessControl.FileSecurity
            $inheritance = [Security.AccessControl.InheritanceFlags]::None
        }
        $acl.SetAccessRuleProtection($true, $false)
        $acl.SetOwner($adminsSid)
        foreach ($sid in @($systemSid, $adminsSid)) {
            $rule = New-Object Security.AccessControl.FileSystemAccessRule(
                $sid,
                [Security.AccessControl.FileSystemRights]::FullControl,
                $inheritance,
                [Security.AccessControl.PropagationFlags]::None,
                [Security.AccessControl.AccessControlType]::Allow
            )
            $acl.AddAccessRule($rule) | Out-Null
        }
        Set-Acl -LiteralPath $item.FullName -AclObject $acl
        $actualAllowSids = @((Get-Acl -LiteralPath $item.FullName).Access | Where-Object {
            $_.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow
        } | ForEach-Object {
            $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        } | Sort-Object -Unique)
        $unexpectedAllows = @($actualAllowSids | Where-Object { $_ -notin $allowedSids })
        $missingAllows = @($allowedSids | Where-Object { $_ -notin $actualAllowSids })
        if ($unexpectedAllows.Count -gt 0 -or $missingAllows.Count -gt 0) {
            throw "ACL verification failed on $($item.FullName): unexpected=$($unexpectedAllows -join ',') missing=$($missingAllows -join ',')"
        }
    }
}

$installContext = @{
    BackupRoot = $null
    SupervisorWasRunning = $false
    SyncTaskExisted = $false
    SyncTaskWasEnabled = $false
    SyncTaskWasRunning = $false
    NvrSyncWrapped = $false
    LegacyTaskNames = @()
    RegisteredAt = $null
}
try {
Invoke-WithCameraMutationLock -TimeoutSeconds 30 -ScriptBlock {
    $supervisorBefore = Get-ScheduledTask -TaskName 'MediaMTX-Supervisor' -ErrorAction SilentlyContinue
    $supervisorWasRunning = ($null -ne $supervisorBefore -and [string]$supervisorBefore.State -eq 'Running')
    $installContext.SupervisorWasRunning = $supervisorWasRunning
    Stop-ScheduledTask -TaskName 'MediaMTX-Supervisor' -ErrorAction SilentlyContinue

    # The legacy sync does not know the new mutex yet. Disable and stop it
    # before reading/freezing the config baseline, otherwise it can rewrite the
    # file between validation and task wrapping.
    $syncBefore = Get-ScheduledTask -TaskName ([string]$packageSettings.syncTaskName) -ErrorAction SilentlyContinue
    $syncXmlSnapshot = $null
    if ($syncBefore) {
        $syncXmlSnapshot = Export-ScheduledTask -TaskName ([string]$packageSettings.syncTaskName)
        $installContext.SyncTaskExisted = $true
        $installContext.SyncTaskWasEnabled = ([string]$syncBefore.State -ne 'Disabled')
        $installContext.SyncTaskWasRunning = ([string]$syncBefore.State -eq 'Running')
        Disable-ScheduledTask -TaskName ([string]$packageSettings.syncTaskName) -ErrorAction Stop | Out-Null
        Stop-ScheduledTask -TaskName ([string]$packageSettings.syncTaskName) -ErrorAction SilentlyContinue
    }

    $currentConfigValidation = Test-MediaMtxConfig -Path $paths.Config `
        -MinimumSourceCount ([int]$packageSettings.minimumConfigSources) `
        -MinimumPathCount ([int]$packageSettings.minimumConfigPaths) `
        -MinimumEagerSourceCount ([int]$packageSettings.expectedSources)
    if (-not $currentConfigValidation.Valid) {
        throw ('Current MediaMTX config is below the protected site baseline: ' +
            ($currentConfigValidation.Errors -join '; '))
    }
    # Freeze the largest known-good inventory into installed settings. A future
    # truncated sync cannot redefine health expectations downward.
    $packageSettings.expectedSources = [Math]::Max(
        [int]$packageSettings.expectedSources, [int]$currentConfigValidation.EagerSourceCount
    )
    $packageSettings.minimumConfigSources = [Math]::Max(
        [int]$packageSettings.minimumConfigSources, [int]$currentConfigValidation.SourceCount
    )
    $packageSettings.minimumConfigPaths = [Math]::Max(
        [int]$packageSettings.minimumConfigPaths, [int]$currentConfigValidation.PathCount
    )
    $packageSettings.minimumConfigSources = [Math]::Max(
        [int]$packageSettings.minimumConfigSources, [int]$packageSettings.expectedSources
    )
    $packageSettings.minimumConfigPaths = [Math]::Max(
        [int]$packageSettings.minimumConfigPaths, [int]$packageSettings.expectedSources
    )

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $backupParent = Join-Path $MediaRoot 'camera-agent-backups'
    New-Item -ItemType Directory -Path $backupParent -Force | Out-Null
    Protect-CameraAgentPath -Path $backupParent
    $backupRoot = Join-Path $backupParent $stamp
    $installContext.BackupRoot = $backupRoot
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    Protect-CameraAgentPath -Path $backupRoot

    $supervisorXml = Join-Path $backupRoot 'MediaMTX-Supervisor.xml'
    $syncXml = Join-Path $backupRoot 'MediaMTX-NVR-Sync.xml'
    $mediaXml = Join-Path $backupRoot 'MediaMTX.xml'
    $hadSupervisor = Export-TaskIfPresent -TaskName 'MediaMTX-Supervisor' -Destination $supervisorXml
    $hadSync = ($null -ne $syncXmlSnapshot)
    if ($hadSync) {
        [IO.File]::WriteAllText($syncXml, $syncXmlSnapshot, (New-Object Text.UTF8Encoding($false)))
    }
    $hadMedia = Export-TaskIfPresent -TaskName ([string]$packageSettings.mediaTaskName) -Destination $mediaXml
    $mediaTaskBefore = Get-ScheduledTask -TaskName ([string]$packageSettings.mediaTaskName)

    $hadInstallRoot = Test-Path -LiteralPath $InstallRoot
    if ($hadInstallRoot) {
        Copy-Item -LiteralPath $InstallRoot -Destination (Join-Path $backupRoot 'installed-agent') -Recurse -Force
    }
    $legacyFile = Join-Path $MediaRoot 'nvr-watchdog.ps1'
    if (Test-Path -LiteralPath $legacyFile) {
        Copy-Item -LiteralPath $legacyFile -Destination (Join-Path $backupRoot 'nvr-watchdog.ps1') -Force
    }

    $legacyTasks = New-Object System.Collections.Generic.List[object]
    foreach ($task in @(Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        $usesLegacyWatchdog = $false
        foreach ($action in @($task.Actions)) {
            if ([string]$action.Arguments -match '(?i)nvr-watchdog\.ps1') {
                $usesLegacyWatchdog = $true
            }
        }
        if ($usesLegacyWatchdog) {
            $fileName = 'legacy-' + (Get-SafeFileName $task.TaskName) + '.xml'
            $xmlPath = Join-Path $backupRoot $fileName
            [IO.File]::WriteAllText($xmlPath, (Export-ScheduledTask -TaskName $task.TaskName), (New-Object Text.UTF8Encoding($false)))
            $legacyTasks.Add([ordered]@{ TaskName = $task.TaskName; XmlFile = $fileName })
        }
    }

    $manifest = [ordered]@{
        Version = 1
        InstalledAt = (Get-Date).ToString('o')
        BackupDirectory = $backupRoot
        InstallRoot = $InstallRoot
        MediaRoot = $MediaRoot
        HadInstallRoot = $hadInstallRoot
        HadSupervisorTask = $hadSupervisor
        HadSyncTask = $hadSync
        HadMediaTask = $hadMedia
        MediaTaskWasRunning = ([string]$mediaTaskBefore.State -eq 'Running')
        SupervisorTaskWasRunning = $supervisorWasRunning
        SyncTaskWasEnabled = [bool]$installContext.SyncTaskWasEnabled
        SyncTaskWasRunning = [bool]$installContext.SyncTaskWasRunning
        SyncTaskWrapped = $false
        LegacyTasks = @($legacyTasks)
    }
    Write-AtomicJsonFile -Path (Join-Path $backupRoot 'install-manifest.json') -Value $manifest
    Write-AtomicJsonFile -Path $paths.InstallManifest -Value $manifest
    foreach ($legacy in @($legacyTasks)) {
        Disable-ScheduledTask -TaskName ([string]$legacy.TaskName) -ErrorAction Stop | Out-Null
    }

    New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
    foreach ($file in $requiredPackageFiles) {
        $sourcePath = [IO.Path]::GetFullPath((Join-Path $packageRoot $file))
        $destinationPath = [IO.Path]::GetFullPath((Join-Path $InstallRoot $file))
        if ($sourcePath -ne $destinationPath) {
            Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
        }
    }
    Write-AtomicJsonFile -Path (Join-Path $InstallRoot 'camera-agent.json') -Value $packageSettings

    # These scripts execute as SYSTEM. A writable InstallRoot would be a local
    # privilege escalation path, so ACL hardening is a mandatory install gate.
    Protect-CameraAgentPath -Path $InstallRoot
    Protect-CameraAgentPath -Path $paths.DataRoot

    if ($hadSync) {
        $syncTask = Get-ScheduledTask -TaskName ([string]$packageSettings.syncTaskName)
        if (@($syncTask.Actions).Count -ne 1) {
            throw 'NVR sync task must have exactly one action before it can be wrapped safely.'
        }
        $oldAction = $syncTask.Actions[0]
        $match = [regex]::Match([string]$oldAction.Arguments, '(?is)-File\s+(?:"([^"]+)"|''([^'']+)''|(\S+))(.*)$')
        if (-not $match.Success) {
            throw 'Could not parse the existing NVR sync task action; refusing an unsafe unwrapped install.'
        }
        $syncScript = @($match.Groups[1].Value, $match.Groups[2].Value, $match.Groups[3].Value) |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
            Select-Object -First 1
        if ([IO.Path]::GetFileName($syncScript) -ne 'nvr-sync.ps1') {
            throw "Refusing to wrap unexpected sync script: $syncScript"
        }
        $syncTail = $match.Groups[4].Value.Trim()
        $newArguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden ' +
            '-File "' + (Join-Path $InstallRoot 'run-nvr-sync.ps1') + '" ' +
            '-SyncScript "' + $syncScript + '"'
        if ($syncTail) { $newArguments += ' ' + $syncTail }
        $newAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $newArguments
        Set-ScheduledTask -TaskName ([string]$packageSettings.syncTaskName) -Action $newAction | Out-Null
        $manifest.SyncTaskWrapped = $true
        Write-AtomicJsonFile -Path (Join-Path $backupRoot 'install-manifest.json') -Value $manifest
        Write-AtomicJsonFile -Path $paths.InstallManifest -Value $manifest
    }
    $installContext.NvrSyncWrapped = [bool]$manifest.SyncTaskWrapped
    $installContext.LegacyTaskNames = @($legacyTasks | ForEach-Object { $_.TaskName })

    $mediaSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -StartWhenAvailable
    Set-ScheduledTask -TaskName ([string]$packageSettings.mediaTaskName) -Settings $mediaSettings | Out-Null

    $supervisorAction = New-ScheduledTaskAction `
        -Execute 'powershell.exe' `
        -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
            (Join-Path $InstallRoot 'mediamtx-supervisor.ps1') + '"')
    $startupTrigger = New-ScheduledTaskTrigger -AtStartup
    $minuteTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 1)
    $systemPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $supervisorSettings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -MultipleInstances IgnoreNew `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
        -StartWhenAvailable
    Register-ScheduledTask `
        -TaskName 'MediaMTX-Supervisor' `
        -Action $supervisorAction `
        -Trigger @($startupTrigger, $minuteTrigger) `
        -Principal $systemPrincipal `
        -Settings $supervisorSettings `
        -Description 'ASYL camera agent: MediaMTX, source and Tailscale health with bounded automatic recovery.' `
        -Force | Out-Null
    $installContext.RegisteredAt = Get-Date
}

Start-ScheduledTask -TaskName 'MediaMTX-Supervisor'
$deadline = (Get-Date).AddSeconds(35)
$firstRunObserved = $false
$info = $null
do {
    Start-Sleep -Seconds 2
    $info = Get-ScheduledTaskInfo -TaskName 'MediaMTX-Supervisor'
    if (Test-Path -LiteralPath $paths.State -PathType Leaf) {
        try {
            $firstState = Get-Content -LiteralPath $paths.State -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($firstState.LastCheckAt -and
                [datetime]$firstState.LastCheckAt -ge [datetime]$installContext.RegisteredAt) {
                $firstRunObserved = $true
            }
        } catch {}
    }
} while (-not $firstRunObserved -and (Get-Date) -lt $deadline)
if (-not $firstRunObserved) {
    throw 'Camera supervisor did not publish a fresh health state after installation.'
}
if ($info.LastTaskResult -ne 0 -and $info.LastTaskResult -ne 267009) {
    throw "Camera supervisor first run failed with task result $($info.LastTaskResult)."
}
if ($installContext.SyncTaskExisted -and $installContext.SyncTaskWasEnabled) {
    Enable-ScheduledTask -TaskName ([string]$packageSettings.syncTaskName) -ErrorAction Stop | Out-Null
}
if ($installContext.SyncTaskExisted -and $installContext.SyncTaskWasRunning) {
    Start-ScheduledTask -TaskName ([string]$packageSettings.syncTaskName) -ErrorAction Stop
}

[PSCustomObject]@{
    Install = 'OK'
    BackupDirectory = $installContext.BackupRoot
    SupervisorState = [string](Get-ScheduledTask -TaskName 'MediaMTX-Supervisor').State
    SupervisorLastResult = $info.LastTaskResult
    MediaMTXState = [string](Get-ScheduledTask -TaskName ([string]$packageSettings.mediaTaskName)).State
    NvrSyncWrapped = [bool]$installContext.NvrSyncWrapped
    NvrSyncRestoredEnabled = [bool]$installContext.SyncTaskWasEnabled
    ExpectedSources = [int]$packageSettings.expectedSources
    MinimumConfigSources = [int]$packageSettings.minimumConfigSources
    MinimumConfigPaths = [int]$packageSettings.minimumConfigPaths
    LegacyTasksDisabled = @($installContext.LegacyTaskNames)
    TailnetPeer = [string]$packageSettings.tailnetPeer
} | ConvertTo-Json -Depth 5
} catch {
    $installError = $_.Exception.Message
    $backupManifest = $null
    if ($installContext.BackupRoot) {
        $backupManifest = Join-Path $installContext.BackupRoot 'install-manifest.json'
    }
    if ($backupManifest -and (Test-Path -LiteralPath $backupManifest -PathType Leaf)) {
        try {
            & (Join-Path $packageRoot 'rollback.ps1') -BackupDirectory $installContext.BackupRoot | Out-Null
        } catch {
            throw "Camera agent installation failed ($installError); automatic rollback also failed: $($_.Exception.Message)"
        }
        throw "Camera agent installation failed and was rolled back: $installError"
    }
    if ($installContext.SupervisorWasRunning) {
        Start-ScheduledTask -TaskName 'MediaMTX-Supervisor' -ErrorAction SilentlyContinue
    }
    if ($installContext.SyncTaskExisted -and $installContext.SyncTaskWasEnabled) {
        Enable-ScheduledTask -TaskName ([string]$packageSettings.syncTaskName) -ErrorAction SilentlyContinue | Out-Null
    }
    if ($installContext.SyncTaskExisted -and $installContext.SyncTaskWasRunning) {
        Start-ScheduledTask -TaskName ([string]$packageSettings.syncTaskName) -ErrorAction SilentlyContinue
    }
    throw
}
