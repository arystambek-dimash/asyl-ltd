param(
    [string]$BackupDirectory,
    [string]$ManifestPath = (Join-Path (Join-Path $env:ProgramData 'ASYL-Camera-Agent') 'install-manifest.json')
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run rollback.ps1 from an elevated PowerShell window.'
}
if ([string]::IsNullOrWhiteSpace($BackupDirectory)) {
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "Install manifest not found: $ManifestPath"
    }
    $currentManifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $BackupDirectory = [string]$currentManifest.BackupDirectory
}
$backupManifestPath = Join-Path $BackupDirectory 'install-manifest.json'
if (-not (Test-Path -LiteralPath $backupManifestPath -PathType Leaf)) {
    throw "Backup manifest not found: $backupManifestPath"
}
$manifest = Get-Content -LiteralPath $backupManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Restore-Task([string]$TaskName, [string]$XmlFile, [bool]$Existed) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    if ($Existed) {
        $path = Join-Path $BackupDirectory $XmlFile
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Task backup is missing: $path"
        }
        Register-ScheduledTask -TaskName $TaskName -Xml (Get-Content -LiteralPath $path -Raw -Encoding UTF8) -Force | Out-Null
    }
}

Stop-ScheduledTask -TaskName 'MediaMTX-Supervisor' -ErrorAction SilentlyContinue
Restore-Task -TaskName 'MediaMTX-Supervisor' -XmlFile 'MediaMTX-Supervisor.xml' `
    -Existed ([bool]$manifest.HadSupervisorTask)
if ([bool]$manifest.HadSyncTask) {
    Restore-Task -TaskName 'MediaMTX-NVR-Sync' -XmlFile 'MediaMTX-NVR-Sync.xml' -Existed $true
}
if ([bool]$manifest.HadMediaTask) {
    Restore-Task -TaskName 'MediaMTX' -XmlFile 'MediaMTX.xml' -Existed $true
}
foreach ($legacy in @($manifest.LegacyTasks)) {
    Restore-Task -TaskName ([string]$legacy.TaskName) -XmlFile ([string]$legacy.XmlFile) -Existed $true
}

$installRoot = [string]$manifest.InstallRoot
$oldInstall = Join-Path $BackupDirectory 'installed-agent'
if ([bool]$manifest.HadInstallRoot -and (Test-Path -LiteralPath $oldInstall)) {
    Remove-Item -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $oldInstall -Destination $installRoot -Recurse -Force
} elseif (-not [bool]$manifest.HadInstallRoot) {
    Remove-Item -LiteralPath $installRoot -Recurse -Force -ErrorAction SilentlyContinue
}

if ([bool]$manifest.MediaTaskWasRunning -and [bool]$manifest.HadMediaTask) {
    Start-ScheduledTask -TaskName 'MediaMTX' -ErrorAction SilentlyContinue
}
if ([bool]$manifest.SupervisorTaskWasRunning -and [bool]$manifest.HadSupervisorTask) {
    Start-ScheduledTask -TaskName 'MediaMTX-Supervisor' -ErrorAction SilentlyContinue
}
$syncWasRunning = $false
if ($null -ne $manifest.PSObject.Properties['SyncTaskWasRunning']) {
    $syncWasRunning = [bool]$manifest.SyncTaskWasRunning
}
if ($syncWasRunning -and [bool]$manifest.HadSyncTask) {
    Start-ScheduledTask -TaskName 'MediaMTX-NVR-Sync' -ErrorAction SilentlyContinue
}

[PSCustomObject]@{
    Rollback = 'OK'
    BackupDirectory = $BackupDirectory
    MediaMTXState = $(
        $task = Get-ScheduledTask -TaskName 'MediaMTX' -ErrorAction SilentlyContinue
        if ($task) { [string]$task.State } else { 'missing' }
    )
    SupervisorState = $(
        $task = Get-ScheduledTask -TaskName 'MediaMTX-Supervisor' -ErrorAction SilentlyContinue
        if ($task) { [string]$task.State } else { 'missing' }
    )
} | ConvertTo-Json -Depth 4
