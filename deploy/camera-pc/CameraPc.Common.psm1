Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$script:MutationMutexName = 'Global\ASYL-Camera-MediaMTX-Mutation'
$script:Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Get-CameraAgentSettings {
    param([string]$Path = (Join-Path $PSScriptRoot 'camera-agent.json'))

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Camera agent settings not found: $Path"
    }
    $settings = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    $required = @(
        'mediaRoot', 'mediaTaskName', 'requiredPorts', 'expectedSources',
        'recordingRetentionDays', 'recordingSegmentMinutes',
        'minimumConfigSources', 'minimumConfigPaths', 'majorityLossPercent',
        'sourceFailureThreshold', 'hardFailureCooldownMinutes', 'restartCooldownMinutes',
        'restartFailureBackoffMinutes', 'restartReadyTimeoutSeconds',
        'sourceRecoveryTimeoutSeconds', 'tailscaleRepairCooldownMinutes',
        'heartbeatMinutes', 'maxLogBytes'
    )
    foreach ($name in $required) {
        if ($null -eq $settings.PSObject.Properties[$name]) {
            throw "Camera agent setting is missing: $name"
        }
    }
    if ([int]$settings.majorityLossPercent -lt 51 -or [int]$settings.majorityLossPercent -gt 100) {
        throw 'majorityLossPercent must be between 51 and 100.'
    }
    if (@($settings.requiredPorts).Count -eq 0) {
        throw 'requiredPorts must not be empty.'
    }
    if ([int]$settings.expectedSources -lt 1) {
        throw 'expectedSources must be a positive site baseline.'
    }
    if ([int]$settings.recordingRetentionDays -lt 1 -or
        [int]$settings.recordingRetentionDays -gt 90) {
        throw 'recordingRetentionDays must be between 1 and 90.'
    }
    if ([int]$settings.recordingSegmentMinutes -lt 1 -or
        [int]$settings.recordingSegmentMinutes -gt 60) {
        throw 'recordingSegmentMinutes must be between 1 and 60.'
    }
    if ([int]$settings.minimumConfigSources -lt [int]$settings.expectedSources) {
        throw 'minimumConfigSources must not be below expectedSources.'
    }
    if ([int]$settings.minimumConfigPaths -lt [int]$settings.expectedSources) {
        throw 'minimumConfigPaths must not be below expectedSources.'
    }
    return $settings
}

function Get-CameraAgentPaths {
    param($Settings)

    $dataRoot = Join-Path $env:ProgramData 'ASYL-Camera-Agent'
    [PSCustomObject]@{
        DataRoot = $dataRoot
        State = Join-Path $dataRoot 'state.json'
        Log = Join-Path $dataRoot 'agent.log'
        InstallManifest = Join-Path $dataRoot 'install-manifest.json'
        Config = Join-Path ([string]$Settings.mediaRoot) 'mediamtx.yml'
        Inventory = Join-Path ([string]$Settings.mediaRoot) 'cameras.json'
        Executable = Join-Path ([string]$Settings.mediaRoot) 'mediamtx.exe'
    }
}

function Initialize-CameraAgentDataRoot {
    param($Paths)
    if (-not (Test-Path -LiteralPath $Paths.DataRoot)) {
        New-Item -ItemType Directory -Path $Paths.DataRoot -Force | Out-Null
    }
}

function Invoke-WithCameraMutationLock {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$ScriptBlock,
        [int]$TimeoutSeconds = 0
    )

    $mutex = New-Object System.Threading.Mutex($false, $script:MutationMutexName)
    $taken = $false
    try {
        try {
            $taken = $mutex.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds))
        } catch [System.Threading.AbandonedMutexException] {
            $taken = $true
        }
        if (-not $taken) {
            throw 'Camera mutation lock is busy.'
        }
        & $ScriptBlock
    } finally {
        if ($taken) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}

function Write-AtomicTextFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )

    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $temporary = Join-Path $directory ('.' + [IO.Path]::GetFileName($Path) + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [IO.File]::WriteAllText($temporary, $Content, $script:Utf8NoBom)
        if (Test-Path -LiteralPath $Path) {
            $backup = $Path + '.atomic-backup'
            [IO.File]::Replace($temporary, $Path, $backup, $true)
            Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        } else {
            Move-Item -LiteralPath $temporary -Destination $Path -Force
        }
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Write-AtomicJsonFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value,
        [int]$Depth = 8
    )
    Write-AtomicTextFile -Path $Path -Content ($Value | ConvertTo-Json -Depth $Depth)
}

function Enable-AiRecordingConfig {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$MediaRoot,
        [int]$RetentionDays = 14,
        [int]$SegmentMinutes = 5
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "MediaMTX config not found: $Path"
    }
    if ($RetentionDays -lt 1 -or $RetentionDays -gt 90) {
        throw 'Recording retention must be between 1 and 90 days.'
    }
    if ($SegmentMinutes -lt 1 -or $SegmentMinutes -gt 60) {
        throw 'Recording segment duration must be between 1 and 60 minutes.'
    }

    $content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $original = $content
    $globals = [ordered]@{
        api = 'yes'
        apiAddress = '127.0.0.1:9997'
        playback = 'yes'
        playbackAddress = ':9996'
    }
    foreach ($entry in $globals.GetEnumerator()) {
        $pattern = '(?m)^' + [regex]::Escape([string]$entry.Key) + ':\s*.*$'
        if ([regex]::IsMatch($content, $pattern)) {
            $content = [regex]::Replace(
                $content, $pattern, ([string]$entry.Key + ': ' + [string]$entry.Value)
            )
        } else {
            $content = ([string]$entry.Key + ': ' + [string]$entry.Value + "`r`n") + $content
        }
    }

    # The NVR synchronizer rewrites camera paths. Recreate this marked regex
    # block after every sync, so only annotated AI publisher paths are recorded.
    $markerPattern = '(?ms)^  # ASYL-AI-RECORDING-BEGIN\r?\n.*?^  # ASYL-AI-RECORDING-END\r?\n?'
    $content = [regex]::Replace($content, $markerPattern, '')
    if (-not [regex]::IsMatch($content, '(?m)^paths:\s*$')) {
        throw 'MediaMTX config has no block-style paths section.'
    }
    $recordRoot = ($MediaRoot -replace '\\', '/').TrimEnd('/')
    $block = @(
        '  # ASYL-AI-RECORDING-BEGIN',
        '  "~^cam[A-Za-z0-9_]*ai$":',
        '    record: yes',
        ('    recordPath: "' + $recordRoot + '/recordings/%path/%Y-%m-%d_%H-%M-%S-%f"'),
        '    recordFormat: fmp4',
        '    recordPartDuration: 1s',
        ('    recordSegmentDuration: ' + $SegmentMinutes + 'm'),
        ('    recordDeleteAfter: ' + ($RetentionDays * 24) + 'h'),
        '  # ASYL-AI-RECORDING-END'
    ) -join "`r`n"
    $pathsPattern = New-Object Text.RegularExpressions.Regex('(?m)^paths:\s*$')
    $content = $pathsPattern.Replace($content, ("paths:`r`n" + $block), 1)

    if ($content -ne $original) {
        Write-AtomicTextFile -Path $Path -Content $content
        return $true
    }
    return $false
}

function Read-CameraAgentState {
    param([Parameter(Mandatory = $true)]$Paths)

    $state = [ordered]@{
        Version = 1
        LastCheckAt = $null
        LastHeartbeatAt = $null
        LastStatus = 'unknown'
        LastReason = 'not-checked'
        ConsecutiveMajoritySourceFailures = 0
        LastRestartAttemptAt = $null
        LastRestartSuccessAt = $null
        LastRestartFailureAt = $null
        LastRestartOutcome = 'none'
        RestartFailureCount = 0
        NextRestartAllowedAt = $null
        TotalRestartAttempts = 0
        TotalRestartSuccesses = 0
        LastTailscaleRepairAt = $null
        LastHealth = $null
        Tailscale = $null
    }
    if (Test-Path -LiteralPath $Paths.State) {
        try {
            $saved = Get-Content -LiteralPath $Paths.State -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($property in $saved.PSObject.Properties) {
                $state[$property.Name] = $property.Value
            }
        } catch {
            # A corrupt state file must never prevent recovery. The next write
            # atomically replaces it and the error is visible in the agent log.
        }
    }
    return $state
}

function Write-CameraAgentState {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$State
    )
    Write-AtomicJsonFile -Path $Paths.State -Value $State
}

function Write-CameraAgentLog {
    param(
        [Parameter(Mandatory = $true)]$Paths,
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)][string]$Message
    )

    Initialize-CameraAgentDataRoot -Paths $Paths
    if ((Test-Path -LiteralPath $Paths.Log) -and
        (Get-Item -LiteralPath $Paths.Log).Length -gt [long]$Settings.maxLogBytes) {
        $old = $Paths.Log + '.1'
        Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $Paths.Log -Destination $old -Force
    }
    ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) |
        Add-Content -LiteralPath $Paths.Log -Encoding UTF8
}

function Test-CameraTcpPort {
    param(
        [Parameter(Mandatory = $true)][string]$Address,
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutMilliseconds = 1500
    )

    $client = New-Object Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($Address, $Port, $null, $null)
        return ($async.AsyncWaitHandle.WaitOne($TimeoutMilliseconds) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-ExpectedCameraSourceCount {
    param(
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)]$Paths
    )

    if ([int]$Settings.expectedSources -gt 0) {
        return [int]$Settings.expectedSources
    }
    if (Test-Path -LiteralPath $Paths.Config) {
        $config = Get-Content -LiteralPath $Paths.Config -Raw -Encoding UTF8
        $count = [regex]::Matches($config, '(?im)^\s*sourceOnDemand:\s*no\s*$').Count
        if ($count -gt 0) { return $count }
    }
    if (Test-Path -LiteralPath $Paths.Inventory) {
        try {
            $inventory = Get-Content -LiteralPath $Paths.Inventory -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($null -ne $inventory.PSObject.Properties['proxied']) {
                return [Math]::Max(0, [int]$inventory.proxied)
            }
        } catch {}
    }
    return 0
}

function Get-NvrAddressFromConfig {
    param([Parameter(Mandatory = $true)]$Paths)
    if (-not (Test-Path -LiteralPath $Paths.Config)) { return $null }
    $config = Get-Content -LiteralPath $Paths.Config -Raw -Encoding UTF8
    $match = [regex]::Match($config, '(?i)rtsp://[^@\r\n]+@(\d{1,3}(?:\.\d{1,3}){3}):554/')
    if ($match.Success) { return $match.Groups[1].Value }
    return $null
}

function Get-MediaMtxHealth {
    param(
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)]$Paths
    )

    $taskState = 'missing'
    try {
        $taskState = [string](Get-ScheduledTask -TaskName ([string]$Settings.mediaTaskName) -ErrorAction Stop).State
    } catch {}
    $processes = @(Get-Process mediamtx -ErrorAction SilentlyContinue)
    $mediaPid = 0
    $portsOk = $false
    $missingPorts = @([int[]]$Settings.requiredPorts)
    $sources = 0

    if ($processes.Count -eq 1) {
        $mediaPid = [int]$processes[0].Id
        $listeners = @(Get-NetTCPConnection -State Listen -OwningProcess $mediaPid -ErrorAction SilentlyContinue)
        $listenerPorts = @($listeners | ForEach-Object { [int]$_.LocalPort })
        $missingPorts = @([int[]]$Settings.requiredPorts | Where-Object { $_ -notin $listenerPorts })
        $portsOk = ($missingPorts.Count -eq 0)
        $sources = @(
            Get-NetTCPConnection -State Established -OwningProcess $mediaPid -ErrorAction SilentlyContinue |
                Where-Object { $_.RemotePort -eq 554 }
        ).Count
    }

    [PSCustomObject]@{
        TaskState = $taskState
        ProcessCount = $processes.Count
        ProcessId = $mediaPid
        PortsOk = $portsOk
        MissingPorts = $missingPorts
        SourceCount = $sources
    }
}

function Get-TailscaleExecutable {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace([string]$env:ProgramFiles)) {
        $candidates.Add((Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'))
    }
    $programFilesX86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    if (-not [string]::IsNullOrWhiteSpace($programFilesX86)) {
        $candidates.Add((Join-Path $programFilesX86 'Tailscale\tailscale.exe'))
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $candidate }
    }
    $command = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Get-TailscaleHealth {
    param($Settings)

    $serviceState = 'missing'
    try { $serviceState = [string](Get-Service -Name Tailscale -ErrorAction Stop).Status } catch {}
    $executable = Get-TailscaleExecutable
    $backendState = 'unknown'
    $addresses = @()
    $peerRequired = -not [string]::IsNullOrWhiteSpace([string]$Settings.tailnetPeer)
    $peerReachable = $null
    $errorMessage = $null

    if ($executable) {
        try {
            $status = (& $executable status --json 2>$null | Out-String) | ConvertFrom-Json
            $backendState = [string]$status.BackendState
            if ($status.Self -and $status.Self.TailscaleIPs) {
                $addresses = @($status.Self.TailscaleIPs | ForEach-Object { [string]$_ })
            }
            if ($peerRequired -and $serviceState -eq 'Running' -and $backendState -eq 'Running') {
                $pingOutput = (& $executable ping '--timeout=3s' '--c=1' ([string]$Settings.tailnetPeer) 2>&1 | Out-String)
                $peerReachable = ($LASTEXITCODE -eq 0 -and $pingOutput -match '(?i)pong')
            }
        } catch {
            $errorMessage = $_.Exception.Message
        }
    } else {
        $errorMessage = 'tailscale.exe not found'
    }

    $healthy = (
        $serviceState -eq 'Running' -and
        $backendState -eq 'Running' -and
        $addresses.Count -gt 0 -and
        (-not $peerRequired -or $peerReachable -eq $true)
    )
    [PSCustomObject]@{
        Healthy = $healthy
        ServiceState = $serviceState
        BackendState = $backendState
        Addresses = $addresses
        Peer = [string]$Settings.tailnetPeer
        PeerRequired = $peerRequired
        PeerReachable = $peerReachable
        Error = $errorMessage
    }
}

function Test-MediaMtxConfig {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$MinimumSourceCount = 0,
        [int]$MinimumPathCount = 0,
        [int]$MinimumEagerSourceCount = 0
    )

    $errors = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        $errors.Add("Config does not exist: $Path")
        return [PSCustomObject]@{
            Valid = $false
            SourceCount = 0
            EagerSourceCount = 0
            PathCount = 0
            Errors = @($errors)
        }
    }
    $content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($content)) { $errors.Add('Config is empty.') }
    if ($content -notmatch '(?m)^paths:\s*$') { $errors.Add('Top-level paths: section is missing.') }
    if ($content.IndexOf([char]0) -ge 0) { $errors.Add('Config contains NUL bytes.') }

    function Get-YamlScalar([string]$Value) {
        $scalar = $Value.Trim()
        if ($scalar.Length -ge 2 -and
            (($scalar.StartsWith('"') -and $scalar.EndsWith('"')) -or
             ($scalar.StartsWith("'") -and $scalar.EndsWith("'")))) {
            return $scalar.Substring(1, $scalar.Length - 2)
        }
        return $scalar
    }

    function Remove-YamlTrailingComment([string]$Value) {
        $quote = [char]0
        $escaped = $false
        for ($index = 0; $index -lt $Value.Length; $index++) {
            $character = $Value[$index]
            if ($quote -ne [char]0) {
                if ($quote -eq '"' -and $character -eq '\' -and -not $escaped) {
                    $escaped = $true
                    continue
                }
                if ($character -eq $quote -and -not $escaped) { $quote = [char]0 }
                $escaped = $false
                continue
            }
            if ($character -eq '"' -or $character -eq "'") {
                $quote = $character
                continue
            }
            if ($character -eq '#' -and
                ($index -eq 0 -or [char]::IsWhiteSpace($Value[$index - 1]))) {
                return $Value.Substring(0, $index).TrimEnd()
            }
        }
        return $Value.TrimEnd()
    }

    function Split-YamlFlowItems([string]$Body, [string]$Context) {
        $items = New-Object System.Collections.Generic.List[string]
        $quote = [char]0
        $escaped = $false
        $start = 0
        for ($index = 0; $index -lt $Body.Length; $index++) {
            $character = $Body[$index]
            if ($quote -ne [char]0) {
                if ($quote -eq '"' -and $character -eq '\' -and -not $escaped) {
                    $escaped = $true
                    continue
                }
                if ($character -eq $quote -and -not $escaped) { $quote = [char]0 }
                $escaped = $false
                continue
            }
            if ($character -eq '"' -or $character -eq "'") {
                $quote = $character
            } elseif ($character -eq ',') {
                $item = $Body.Substring($start, $index - $start).Trim()
                if ($item) {
                    $items.Add($item)
                } else {
                    $errors.Add("Malformed flow mapping for path $Context.")
                }
                $start = $index + 1
            }
        }
        if ($quote -ne [char]0) {
            $errors.Add("Malformed flow mapping for path $Context.")
        }
        $last = $Body.Substring($start).Trim()
        if ($last) {
            $items.Add($last)
        } elseif (-not [string]::IsNullOrWhiteSpace($Body)) {
            $errors.Add("Malformed flow mapping for path $Context.")
        }
        return $items.ToArray()
    }

    function Get-YamlPropertySeparator([string]$Item) {
        $quote = [char]0
        $escaped = $false
        for ($index = 0; $index -lt $Item.Length; $index++) {
            $character = $Item[$index]
            if ($quote -ne [char]0) {
                if ($quote -eq '"' -and $character -eq '\' -and -not $escaped) {
                    $escaped = $true
                    continue
                }
                if ($character -eq $quote -and -not $escaped) { $quote = [char]0 }
                $escaped = $false
                continue
            }
            if ($character -eq '"' -or $character -eq "'") { $quote = $character }
            elseif ($character -eq ':') { return $index }
        }
        return -1
    }

    function Test-YamlFlowPropertyValue([string]$Value) {
        $scalar = $Value.Trim()
        if ([string]::IsNullOrWhiteSpace($scalar)) { return $true }

        # A quoted flow scalar must end when its closing quote ends. This also
        # rejects a missing comma before the next property.
        if ($scalar[0] -eq '"' -or $scalar[0] -eq "'") {
            $quote = $scalar[0]
            $escaped = $false
            for ($index = 1; $index -lt $scalar.Length; $index++) {
                $character = $scalar[$index]
                if ($quote -eq '"' -and $character -eq '\' -and -not $escaped) {
                    $escaped = $true
                    continue
                }
                if ($quote -eq "'" -and $character -eq "'" -and
                    ($index + 1) -lt $scalar.Length -and $scalar[$index + 1] -eq "'") {
                    $index++
                    continue
                }
                if ($character -eq $quote -and -not $escaped) {
                    return [string]::IsNullOrWhiteSpace($scalar.Substring($index + 1))
                }
                $escaped = $false
            }
            return $false
        }

        # In the supported MediaMTX subset these are the only path properties
        # whose appearance after whitespace can indicate an omitted comma.
        $quote = [char]0
        $escaped = $false
        for ($index = 0; $index -lt $scalar.Length; $index++) {
            $character = $scalar[$index]
            if ($quote -ne [char]0) {
                if ($quote -eq '"' -and $character -eq '\' -and -not $escaped) {
                    $escaped = $true
                    continue
                }
                if ($character -eq $quote -and -not $escaped) { $quote = [char]0 }
                $escaped = $false
                continue
            }
            if ($character -eq '"' -or $character -eq "'") {
                $quote = $character
                continue
            }
            if ([char]::IsWhiteSpace($character)) {
                $remainder = $scalar.Substring($index).TrimStart()
                if ($remainder -match '^(?i:source|sourceOnDemand)\s*:') { return $false }
            }
        }
        return $true
    }

    function Convert-SourceOnDemand([string]$Value, [string]$Context) {
        $normalized = (Get-YamlScalar $Value).Trim().ToLowerInvariant()
        if ($normalized -in @('yes', 'true', '1')) { return $true }
        if ($normalized -in @('no', 'false', '0')) { return $false }
        $errors.Add("Invalid sourceOnDemand value for path $Context.")
        return $null
    }

    function Convert-YamlFlowPath([string]$Value, [string]$Context) {
        $clean = (Remove-YamlTrailingComment $Value).Trim()
        $result = [ordered]@{ Source = $null; SourceOnDemand = $null }
        if (-not $clean.StartsWith('{') -or -not $clean.EndsWith('}')) {
            $errors.Add("Malformed flow mapping for path $Context.")
            return $result
        }
        $body = $clean.Substring(1, $clean.Length - 2)
        $seenSource = $false
        $seenOnDemand = $false
        foreach ($item in @(Split-YamlFlowItems $body $Context)) {
            $separator = Get-YamlPropertySeparator $item
            if ($separator -le 0) {
                $errors.Add("Malformed flow property for path $Context.")
                continue
            }
            $key = (Get-YamlScalar $item.Substring(0, $separator)).Trim().ToLowerInvariant()
            $rawPropertyValue = $item.Substring($separator + 1)
            if (-not (Test-YamlFlowPropertyValue $rawPropertyValue)) {
                $errors.Add("Malformed flow property for path $Context.")
                continue
            }
            $propertyValue = Get-YamlScalar $rawPropertyValue
            if ($key -eq 'source') {
                if ($seenSource) { $errors.Add("Duplicate source property for path $Context.") }
                $seenSource = $true
                if (-not [string]::IsNullOrWhiteSpace($propertyValue)) {
                    $result.Source = $propertyValue
                }
            } elseif ($key -eq 'sourceondemand') {
                if ($seenOnDemand) { $errors.Add("Duplicate sourceOnDemand property for path $Context.") }
                $seenOnDemand = $true
                $result.SourceOnDemand = Convert-SourceOnDemand $propertyValue $Context
            }
        }
        return $result
    }

    $lines = @($content -split "`r?`n")
    # MediaMTX defaults sourceOnDemand to no. A path-level value overrides the
    # global pathDefaults value; inline shorthand paths inherit pathDefaults.
    $defaultSourceOnDemand = $false
    $insidePathDefaults = $false
    foreach ($line in $lines) {
        if ($line -match '^pathDefaults:\s*$') {
            $insidePathDefaults = $true
            continue
        }
        if ($insidePathDefaults -and $line -match '^\S') {
            $insidePathDefaults = $false
        }
        if ($insidePathDefaults -and $line -match '^  sourceOnDemand:\s*(.*?)\s*$') {
            $parsedDefault = Convert-SourceOnDemand $Matches[1] 'pathDefaults'
            if ($null -ne $parsedDefault) { $defaultSourceOnDemand = [bool]$parsedDefault }
        }
    }

    # Support both MediaMTX forms:
    #   cam1: rtsp://...              (inline shorthand)
    #   cam1:                         (nested mapping)
    #     source: rtsp://...
    # An empty catch-all such as all_others: is a path, not a source.
    $pathRecords = New-Object System.Collections.Generic.List[object]
    $pathNames = New-Object System.Collections.Generic.List[string]
    $insidePaths = $false
    $currentPath = $null
    foreach ($line in $lines) {
        if ($line -match '^paths:\s*$') { $insidePaths = $true; continue }
        if ($insidePaths -and $line -match '^\S') {
            $insidePaths = $false
            $currentPath = $null
            continue
        }
        if (-not $insidePaths) { continue }

        if ($line -match '^  ([A-Za-z0-9_.-]+):\s*(.*?)\s*$') {
            $name = $Matches[1]
            $inlineValue = (Remove-YamlTrailingComment $Matches[2]).Trim()
            $inlineSource = $null
            $inlineOnDemand = $null
            if ($inlineValue.StartsWith('{')) {
                $flow = Convert-YamlFlowPath $inlineValue $name
                $inlineSource = $flow.Source
                $inlineOnDemand = $flow.SourceOnDemand
            } else {
                $scalarSource = Get-YamlScalar $inlineValue
                if (-not [string]::IsNullOrWhiteSpace($scalarSource)) { $inlineSource = $scalarSource }
            }
            $currentPath = [ordered]@{
                Name = $name
                Source = $inlineSource
                SourceOnDemand = $inlineOnDemand
            }
            $pathNames.Add($name)
            $pathRecords.Add($currentPath)
            continue
        }
        if ($null -ne $currentPath -and $line -match '^    source:\s*(.*?)\s*$') {
            $nestedSource = Get-YamlScalar $Matches[1]
            $currentPath.Source = $(if ([string]::IsNullOrWhiteSpace($nestedSource)) { $null } else { $nestedSource })
            continue
        }
        if ($null -ne $currentPath -and $line -match '^    sourceOnDemand:\s*(.*?)\s*$') {
            $currentPath.SourceOnDemand = Convert-SourceOnDemand $Matches[1] ([string]$currentPath.Name)
        }
    }

    $duplicates = @($pathNames | Group-Object | Where-Object { $_.Count -gt 1 } | ForEach-Object { $_.Name })
    if ($duplicates.Count -gt 0) {
        $errors.Add('Duplicate path names: ' + ($duplicates -join ', '))
    }

    $sourceRecords = @($pathRecords | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_.Source) })
    $eagerSourceCount = 0
    foreach ($record in $sourceRecords) {
        $source = [string]$record.Source
        if ($source -match '(?i)^rtsp://') {
            $uri = $null
            if (-not [Uri]::TryCreate($source, [UriKind]::Absolute, [ref]$uri)) {
                $errors.Add("Invalid RTSP source URI for $($record.Name).")
            } elseif ([string]::IsNullOrWhiteSpace($uri.Host)) {
                $errors.Add("RTSP source for $($record.Name) has no host.")
            }
        } elseif ($source -notin @('publisher', 'redirect', 'rpiCamera')) {
            $errors.Add("Unsupported source type for path $($record.Name).")
        }

        $sourceOnDemand = $defaultSourceOnDemand
        if ($null -ne $record.SourceOnDemand) {
            $sourceOnDemand = [bool]$record.SourceOnDemand
        }
        if (-not $sourceOnDemand) { $eagerSourceCount++ }
    }
    if ($sourceRecords.Count -eq 0) { $errors.Add('No source entries were found.') }
    if ($sourceRecords.Count -lt $MinimumSourceCount) {
        $errors.Add("Config has $($sourceRecords.Count) sources; minimum is $MinimumSourceCount.")
    }
    if ($pathNames.Count -lt $MinimumPathCount) {
        $errors.Add("Config has $($pathNames.Count) paths; minimum is $MinimumPathCount.")
    }
    if ($eagerSourceCount -lt $MinimumEagerSourceCount) {
        $errors.Add("Config has $eagerSourceCount eager sources; minimum is $MinimumEagerSourceCount.")
    }
    [PSCustomObject]@{
        Valid = ($errors.Count -eq 0)
        SourceCount = $sourceRecords.Count
        EagerSourceCount = $eagerSourceCount
        PathCount = $pathNames.Count
        Errors = @($errors)
    }
}

function Get-CameraRepairDecision {
    param(
        [Parameter(Mandatory = $true)]$Health,
        [Parameter(Mandatory = $true)][int]$ExpectedSources,
        [Parameter(Mandatory = $true)][int]$MajorityLossPercent,
        [Parameter(Mandatory = $true)][int]$ConsecutiveMajorityFailures,
        [Parameter(Mandatory = $true)][int]$FailureThreshold,
        [Parameter(Mandatory = $true)][bool]$NvrReachable,
        [Parameter(Mandatory = $true)][bool]$HardCooldownActive,
        [Parameter(Mandatory = $true)][bool]$SourceCooldownActive,
        [switch]$ForceRepair
    )

    $hardFailure = (
        $Health.TaskState -ne 'Running' -or
        [int]$Health.ProcessCount -ne 1 -or
        -not [bool]$Health.PortsOk
    )
    $missingSources = 0
    if ($ExpectedSources -gt 0) {
        $missingSources = [Math]::Max(0, $ExpectedSources - [int]$Health.SourceCount)
    }
    $lossPercent = 0
    if ($ExpectedSources -gt 0) {
        $lossPercent = [int][Math]::Floor(($missingSources * 100.0) / $ExpectedSources)
    }
    $majorityLoss = ($ExpectedSources -gt 0 -and $lossPercent -ge $MajorityLossPercent)
    $singleOrMinorLoss = ($missingSources -gt 0 -and -not $majorityLoss)
    $thresholdReached = ($ConsecutiveMajorityFailures -ge $FailureThreshold)

    $action = 'none'
    $reason = 'healthy'
    if ($ForceRepair) {
        if ($HardCooldownActive) { $action = 'suppress'; $reason = 'restart-cooldown' }
        else { $action = 'restart'; $reason = 'forced' }
    } elseif ($hardFailure) {
        if ($HardCooldownActive) { $action = 'suppress'; $reason = 'hard-failure-cooldown' }
        else { $action = 'restart'; $reason = 'hard-failure' }
    } elseif ($majorityLoss -and -not $NvrReachable) {
        $action = 'alert'
        $reason = 'nvr-unreachable'
    } elseif ($majorityLoss -and -not $thresholdReached) {
        $action = 'observe'
        $reason = 'majority-loss-pending'
    } elseif ($majorityLoss -and $thresholdReached) {
        if ($SourceCooldownActive) { $action = 'suppress'; $reason = 'majority-loss-cooldown' }
        else { $action = 'restart'; $reason = 'majority-source-loss' }
    } elseif ($singleOrMinorLoss) {
        # Never sacrifice healthy cameras to recover one source. The server-side
        # monitor consumes this degraded state and raises the alert.
        $action = 'alert'
        $reason = 'minor-source-loss'
    }

    [PSCustomObject]@{
        Action = $action
        Reason = $reason
        HardFailure = $hardFailure
        MissingSources = $missingSources
        LossPercent = $lossPercent
        MajorityLoss = $majorityLoss
        MinorLoss = $singleOrMinorLoss
    }
}

function Get-RestartBackoffMinutes {
    param(
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)][int]$FailureCount
    )
    if ($FailureCount -le 0) { return [int]$Settings.restartCooldownMinutes }
    $backoffs = @([int[]]$Settings.restartFailureBackoffMinutes)
    $index = [Math]::Min($FailureCount - 1, $backoffs.Count - 1)
    return [int]$backoffs[$index]
}

function Wait-MediaMtxReady {
    param(
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)]$Paths,
        [int]$TimeoutSeconds = 45
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $health = $null
    do {
        Start-Sleep -Seconds 2
        $health = Get-MediaMtxHealth -Settings $Settings -Paths $Paths
        if ($health.TaskState -eq 'Running' -and $health.ProcessCount -eq 1 -and $health.PortsOk) {
            return $health
        }
    } while ((Get-Date) -lt $deadline)
    return $health
}

Export-ModuleMember -Function @(
    'Get-CameraAgentSettings', 'Get-CameraAgentPaths', 'Initialize-CameraAgentDataRoot',
    'Invoke-WithCameraMutationLock', 'Write-AtomicTextFile', 'Write-AtomicJsonFile',
    'Enable-AiRecordingConfig',
    'Read-CameraAgentState', 'Write-CameraAgentState', 'Write-CameraAgentLog',
    'Test-CameraTcpPort', 'Get-ExpectedCameraSourceCount', 'Get-NvrAddressFromConfig',
    'Get-MediaMtxHealth', 'Get-TailscaleHealth', 'Test-MediaMtxConfig',
    'Get-CameraRepairDecision', 'Get-RestartBackoffMinutes', 'Wait-MediaMtxReady'
)
