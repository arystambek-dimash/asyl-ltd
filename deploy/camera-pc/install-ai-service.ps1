param(
    [Parameter(Mandatory = $true)][string]$ApiKeySha256,
    [Parameter(Mandatory = $true)][string]$ModelPath,
    [Parameter(Mandatory = $true)][string]$BackendTailnetIp,
    [string]$SourceRoot = (Join-Path $PSScriptRoot '..\..\cv_service'),
    [string]$InstallRoot = 'C:\mediamtx\ai-service',
    [string]$PythonPath = 'python',
    [string]$FfmpegPath = 'C:\mediamtx\ffmpeg.exe',
    [string]$ModelDevice = '0',
    [string]$PrewarmCameras = 'cam2',
    [string]$PrewarmSource = 'sub',
    [int]$MaxActiveProcessors = 2
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$taskName = 'ASYL-AI-Service'
$firewallName = 'ASYL AI service from backend'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run install-ai-service.ps1 from an elevated PowerShell window.'
}
if ($ApiKeySha256 -cnotmatch '^[0-9a-f]{64}$') {
    throw 'ApiKeySha256 must be a lowercase 64-character SHA-256 digest.'
}
if ($BackendTailnetIp -notmatch '^100\.(?:[0-9]{1,3}\.){2}[0-9]{1,3}$') {
    throw 'BackendTailnetIp must be the production backend Tailscale IPv4 address.'
}
if ($PrewarmSource -notin @('sub', 'main')) {
    throw 'PrewarmSource must be sub or main.'
}
foreach ($camera in @($PrewarmCameras -split ',' | Where-Object { $_ })) {
    if ($camera.Trim() -cnotmatch '^cam[1-9][0-9]*$') {
        throw "Invalid prewarm camera: $camera"
    }
}
if ($MaxActiveProcessors -lt 1) {
    throw 'MaxActiveProcessors must be positive.'
}
$source = [IO.Path]::GetFullPath($SourceRoot)
foreach ($required in @(
    'ai_service.py', 'app.py', 'contracts.py', 'processor.py', 'runtime.py',
    'event_journal.py', 'event_journal_verify.py', 'security.py', 'settings.py', 'state.py', 'requirements.txt'
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $source $required) -PathType Leaf)) {
        throw "cv_service source is incomplete: $required"
    }
}
if (-not (Test-Path -LiteralPath $ModelPath -PathType Leaf)) {
    throw "best.pt not found: $ModelPath"
}
if (-not (Test-Path -LiteralPath $FfmpegPath -PathType Leaf)) {
    throw "ffmpeg.exe not found: $FfmpegPath"
}

function Protect-AiServicePath([string]$Path) {
    $systemSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $adminsSid = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    foreach ($item in @((Get-Item -LiteralPath $Path -Force)) + @(Get-ChildItem -LiteralPath $Path -Recurse -Force)) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing reparse point in AI service tree: $($item.FullName)"
        }
        if ($item.PSIsContainer) {
            $acl = New-Object Security.AccessControl.DirectorySecurity
            $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
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
    }
}

if ((Test-Path -LiteralPath $InstallRoot) -and (((Get-Item -LiteralPath $InstallRoot).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
    throw "Refusing reparse-point InstallRoot: $InstallRoot"
}
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
$installedPackage = Join-Path $InstallRoot 'cv_service'
$stateRoot = Join-Path $InstallRoot 'state'
$eventDbPath = Join-Path $stateRoot 'count-events.sqlite3'
$legacyCameraRolesPath = Join-Path $installedPackage 'data\camera-roles.json'
$cameraRolesStatePath = Join-Path $stateRoot 'camera-roles.json'
$countingLinesStatePath = Join-Path $stateRoot 'counting-lines.json'

function Assert-CompatibleCameraRoleState {
    if (
        (Test-Path -LiteralPath $legacyCameraRolesPath -PathType Leaf) -and
        (Test-Path -LiteralPath $cameraRolesStatePath -PathType Leaf)
    ) {
        $legacyHash = (Get-FileHash -LiteralPath $legacyCameraRolesPath -Algorithm SHA256).Hash
        $durableHash = (Get-FileHash -LiteralPath $cameraRolesStatePath -Algorithm SHA256).Hash
        if (-not [String]::Equals($legacyHash, $durableHash, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to overwrite differing camera-role states: legacy=$legacyCameraRolesPath durable=$cameraRolesStatePath"
        }
    }
}

# Refuse an unknown/partial SQLite before stopping the working service. This
# verifier is stdlib-only and opens the existing target read-only; it never
# creates, upgrades or rewrites a journal whose continuity is not proven.
if (Test-Path -LiteralPath $eventDbPath -PathType Leaf) {
    & $PythonPath (Join-Path $source 'event_journal_verify.py') $eventDbPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Existing count-event journal is incompatible; service was not stopped.'
    }
}
$legacyDatabaseArtifacts = @()
if (Test-Path -LiteralPath $installedPackage -PathType Container) {
    $legacyDatabaseArtifacts = @(
        Get-ChildItem -LiteralPath $installedPackage -Recurse -File -Force |
            Where-Object {
                $_.Name -match '(?i)\.(?:sqlite|sqlite3|db)(?:-(?:wal|shm))?$'
            }
    )
}
if ($legacyDatabaseArtifacts.Count -gt 0) {
    throw (
        'Refusing to delete database artifacts from the old cv_service package; ' +
        'migrate and verify them in sibling state first: ' +
        (($legacyDatabaseArtifacts | ForEach-Object { $_.FullName }) -join ', ')
    )
}

# Older canonical installs kept this state below cv_service/data. Validate a
# possible conflict before stopping the working task, then copy the known
# legacy file to the durable sibling directory before deleting the package.
Assert-CompatibleCameraRoleState
Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$aiQuiesceDeadline = [DateTime]::UtcNow.AddSeconds(30)
$aiTaskStopped = $false
$aiListenerStopped = $false
do {
    $installedTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $aiTaskStopped = (
        $null -eq $installedTask -or
        [string]$installedTask.State -ne 'Running'
    )
    $aiListenerStopped = @(
        Get-NetTCPConnection -State Listen -LocalPort 8890 -ErrorAction SilentlyContinue
    ).Count -eq 0
    if ($aiTaskStopped -and $aiListenerStopped) { break }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $aiQuiesceDeadline)
if (-not $aiTaskStopped -or -not $aiListenerStopped) {
    throw "Existing AI service did not quiesce before state migration: taskStopped=$aiTaskStopped listenerStopped=$aiListenerStopped"
}
Assert-CompatibleCameraRoleState
if (Test-Path -LiteralPath $legacyCameraRolesPath -PathType Leaf) {
    New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    if (-not (Test-Path -LiteralPath $cameraRolesStatePath -PathType Leaf)) {
        $migrationTemporary = $cameraRolesStatePath + '.migrating'
        Remove-Item -LiteralPath $migrationTemporary -Force -ErrorAction SilentlyContinue
        try {
            Copy-Item -LiteralPath $legacyCameraRolesPath -Destination $migrationTemporary
            [IO.File]::Move($migrationTemporary, $cameraRolesStatePath)
        } finally {
            Remove-Item -LiteralPath $migrationTemporary -Force -ErrorAction SilentlyContinue
        }
    }
}
New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $installedPackage) {
    Remove-Item -LiteralPath $installedPackage -Recurse -Force
}
Copy-Item -LiteralPath $source -Destination $installedPackage -Recurse -Force
Remove-Item -LiteralPath (Join-Path $installedPackage 'tests') -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path (Join-Path $installedPackage 'models') -Force | Out-Null
Copy-Item -LiteralPath $ModelPath -Destination (Join-Path $installedPackage 'models\best.pt') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'run-ai-service.ps1') -Destination (Join-Path $InstallRoot 'run-ai-service.ps1') -Force

$venvPython = Join-Path $InstallRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $PythonPath -m venv (Join-Path $InstallRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the AI virtual environment.' }
}
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $installedPackage 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install AI service dependencies.' }

$serviceEnvironment = [ordered]@{
    AI_SERVICE_API_KEY_SHA256 = $ApiKeySha256
    AI_MODEL_PATH = (Join-Path $installedPackage 'models\best.pt')
    AI_MODEL_DEVICE = $ModelDevice
    AI_BIND_HOST = '0.0.0.0'
    AI_BIND_PORT = '8890'
    AI_MEDIAMTX_RTSP_URL = 'rtsp://127.0.0.1:8554'
    AI_MEDIAMTX_API_URL = 'http://127.0.0.1:9997'
    AI_FFMPEG_PATH = $FfmpegPath
    AI_MAX_ACTIVE_PROCESSORS = [string]$MaxActiveProcessors
    AI_PREWARM_CAMERAS = $PrewarmCameras
    AI_PREWARM_SOURCE = $PrewarmSource
    AI_FRAME_QUEUE_SIZE = '2'
    AI_ALWAYS_ON_STATE_PATH = (Join-Path $InstallRoot 'state\always-on.json')
    AI_CAMERA_ROLES_STATE_PATH = $cameraRolesStatePath
    AI_COUNTING_LINES_STATE_PATH = $countingLinesStatePath
    AI_EVENT_DB_PATH = $eventDbPath
}
# Harden the parent tree before creating service-env.json so the file is never
# briefly user-readable.
Protect-AiServicePath -Path $InstallRoot
[IO.File]::WriteAllText(
    (Join-Path $InstallRoot 'service-env.json'),
    ($serviceEnvironment | ConvertTo-Json),
    (New-Object Text.UTF8Encoding($false))
)

# This loads and warms best.pt, validates classes and prewarm inventory, and
# probes H.264 before the HTTP listener can ever be registered.
& (Join-Path $InstallRoot 'run-ai-service.ps1') -ValidateOnly
if ($LASTEXITCODE -ne 0) { throw 'AI service preflight failed.' }

Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule `
    -DisplayName $firewallName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 8890 `
    -RemoteAddress $BackendTailnetIp `
    -Profile Any | Out-Null

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $InstallRoot 'run-ai-service.ps1') + '"')
$trigger = New-ScheduledTaskTrigger -AtStartup
$systemPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $systemPrincipal `
    -Settings $settings `
    -Description 'ASYL single-model camera AI service; backend-only API and warm processors.' `
    -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

# `Ready` is not a healthy state for this long-running task: it usually means
# Python exited and Task Scheduler is only waiting for the next trigger. The
# listener appears only after checkpoint/class/warm-up/encoder/prewarm checks,
# so require both a live task and the real HTTP port before declaring success.
$startupDeadline = [DateTime]::UtcNow.AddMinutes(3)
$task = $null
$listenerReady = $false
do {
    $task = Get-ScheduledTask -TaskName $taskName
    $listenerReady = @(
        Get-NetTCPConnection -State Listen -LocalPort 8890 -ErrorAction SilentlyContinue
    ).Count -gt 0
    if (([string]$task.State -eq 'Running') -and $listenerReady) { break }
    Start-Sleep -Seconds 2
} while ([DateTime]::UtcNow -lt $startupDeadline)

if (([string]$task.State -ne 'Running') -or -not $listenerReady) {
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    $lastResult = if ($null -ne $taskInfo) { [string]$taskInfo.LastTaskResult } else { 'unknown' }
    throw "AI service did not stay running/listen on 8890: state=$($task.State), listener=$listenerReady, lastResult=$lastResult"
}

# No plaintext inbound AI_SERVICE_API_KEY is available on this PC. The optional
# outbound cloud credential is unrelated and cannot authenticate this listener.
# An unauthenticated probe still proves the backend-only guard is active.
$unauthenticatedStatus = 0
try {
    $probe = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8890/health' -TimeoutSec 5
    $unauthenticatedStatus = [int]$probe.StatusCode
} catch {
    if ($null -ne $_.Exception.Response) {
        $unauthenticatedStatus = [int]$_.Exception.Response.StatusCode
    } else {
        throw "AI service listener opened but health probe failed: $($_.Exception.Message)"
    }
}
if ($unauthenticatedStatus -ne 401) {
    throw "AI service must reject an unauthenticated health probe with 401; got $unauthenticatedStatus"
}
[PSCustomObject]@{
    Install = 'OK'
    Task = $taskName
    State = [string]$task.State
    Port = 8890
    AllowedBackend = $BackendTailnetIp
    PrewarmCameras = $PrewarmCameras
    PlaintextKeyStored = $false
    CloudCredentialStored = ($cloudCameraList.Count -gt 0)
    ListenerVerified = $listenerReady
    AuthenticationVerified = ($unauthenticatedStatus -eq 401)
} | ConvertTo-Json
