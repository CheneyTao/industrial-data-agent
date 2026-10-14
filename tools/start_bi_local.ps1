param(
    [ValidateSet('backend', 'frontend')][string]$Mode = 'backend',
    [int]$BackendPort = 8075,
    [int]$FrontendPort = 3005,
    [string]$ScaniaPath = $env:DATAAGENT_SCANIA_HOST_DATA_DIR,
    [switch]$UseComposeManagement,
    [switch]$Reload
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if ($Mode -eq 'frontend') {
    $env:NUXT_BACKEND_URL = "http://127.0.0.1:$BackendPort"
    Set-Location -LiteralPath (Join-Path $projectRoot 'frontend')
    & npm.cmd run dev -- --host 127.0.0.1 --port $FrontendPort
    exit $LASTEXITCODE
}
if ([string]::IsNullOrWhiteSpace($ScaniaPath)) {
    throw 'Specify -ScaniaPath or DATAAGENT_SCANIA_HOST_DATA_DIR with the existing Scania source directory.'
}
if (-not (Test-Path -LiteralPath (Join-Path $ScaniaPath 'raw/train_tte.csv'))) {
    throw 'SCANIA directory does not contain the existing raw files; no download will be attempted.'
}
if ($UseComposeManagement) {
    # Reuse the running stack's management DB/model settings without printing credentials.
    $composeState = (& docker compose config --format json | ConvertFrom-Json)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read existing Compose configuration.' }
    $managementUrl = $composeState.services.backend.environment.DATAAGENT_MANAGEMENT_DATABASE_URL
    if (-not $managementUrl) { throw 'Existing management database URL is unavailable.' }
    $env:DATAAGENT_MANAGEMENT_DATABASE_URL = $managementUrl -replace '@mysql:3306/', '@127.0.0.1:13307/'
    $env:DATAAGENT_REDIS_URL = ''
    $env:DATAAGENT_REDIS_REQUIRED = 'false'
}
$env:DATAAGENT_SCANIA_DATA_DIR = $ScaniaPath
$env:DATAAGENT_VECTOR_FILE_PATH = Join-Path $projectRoot 'work/bi-preview/vectorstore.json'
$env:DATAAGENT_FILE_PATH = Join-Path $projectRoot 'work/bi-preview/uploads'
$serverArgs = @('-m', 'uvicorn', 'dataagent.app:create_app', '--factory', '--host', '127.0.0.1', '--port', "$BackendPort")
if ($Reload) { $serverArgs += @('--reload', '--reload-dir', 'src') }
& (Join-Path $projectRoot '.venv/Scripts/python.exe') @serverArgs
exit $LASTEXITCODE
