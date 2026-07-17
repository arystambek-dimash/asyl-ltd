param([switch]$ValidateOnly)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$installRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $installRoot 'service-env.json'
$pythonPath = Join-Path $installRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "AI service config is missing: $configPath"
}
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "AI service Python is missing: $pythonPath"
}
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($config.PSObject.Properties.Name -contains 'AI_SERVICE_API_KEY') {
    throw 'Plaintext AI_SERVICE_API_KEY is forbidden on camera-PC.'
}
foreach ($property in $config.PSObject.Properties) {
    [Environment]::SetEnvironmentVariable([string]$property.Name, [string]$property.Value, 'Process')
}
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
Set-Location -LiteralPath $installRoot
if ($ValidateOnly) {
    & $pythonPath -m cv_service.validate_install
} else {
    $logPath = Join-Path $installRoot 'service.log'
    if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 10485760) {
        Move-Item -LiteralPath $logPath -Destination ($logPath + '.1') -Force
    }
    & $pythonPath cv_service\ai_service.py *>> $logPath
}
if ($LASTEXITCODE -ne 0) {
    throw "AI service process exited with code $LASTEXITCODE"
}
