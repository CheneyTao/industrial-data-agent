param(
    [ValidateSet('Start', 'StopPreview', 'RestoreBaselineSource')][string]$Action = 'Start',
    [ValidateSet('backend', 'mcp', 'frontend')][string]$Service = 'backend',
    [switch]$Build
)
$ErrorActionPreference = 'Stop'
$reportProjectRoot = Split-Path -Parent $PSScriptRoot
$reportRuntime = Join-Path $reportProjectRoot 'work\report-v020'
Set-Location -LiteralPath $reportProjectRoot

function Assert-NativeSuccess([string]$message) {
    if ($LASTEXITCODE -ne 0) { throw $message }
}

function Get-ComparablePreviewPath([string]$previewPath) {
    $normalized = $previewPath.Replace('\', '/').TrimEnd('/')
    # Docker Desktop may return the bind source in either Windows notation or
    # its exact host-drive mount notation. Compare the same canonical location.
    if ($normalized -match '^/run/desktop/mnt/host/([a-zA-Z])/(.+)$') {
        return $Matches[1].ToUpperInvariant() + ':/' + $Matches[2]
    }
    return $normalized
}

if ($Action -eq 'RestoreBaselineSource') {
    $baselineCheckout = Join-Path (Split-Path -Parent $reportProjectRoot) 'DataAgent-Python-baseline-20261013'
    if (Test-Path -LiteralPath $baselineCheckout) { throw "Target already exists: $baselineCheckout. Nothing overwritten." }
    & git worktree add --detach $baselineCheckout baseline-20261013
    Assert-NativeSuccess 'Baseline source recovery failed.'
    Write-Host "Baseline restored into $baselineCheckout. Current code and data were preserved."
    exit 0
}

if ($Action -eq 'StopPreview') {
    $previewVersions = @{
        'dataagent-report-v020-backend' = '0.2.0'; 'dataagent-report-v020-mcp' = '0.2.0'
        'dataagent-report-v021-backend' = '0.2.1'; 'dataagent-report-v021-mcp' = '0.2.1'
    }
    foreach ($previewName in $previewVersions.Keys) {
        $existingName = & docker ps -a --filter "name=^/$previewName$" --format '{{.Names}}'
        if ($existingName -eq $previewName) {
            $previewLabel = & docker inspect $previewName --format '{{index .Config.Labels "dataagent.release"}}'
            if ($previewLabel -ne $previewVersions[$previewName]) { throw "Refusing to stop unrecognized container $previewName" }
            & docker stop $previewName
            Assert-NativeSuccess "Could not stop $previewName"
        }
    }
    $pidRecord = Join-Path $reportRuntime 'frontend-process.json'
    if (Test-Path -LiteralPath $pidRecord) {
        $processRecord = Get-Content -LiteralPath $pidRecord -Raw | ConvertFrom-Json
        $previewProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($processRecord.processId)"
        # PowerShell versions may deserialize an ISO JSON date as DateTime or
        # String. Normalize both before comparing the recorded process identity.
        $recordedCreatedAt = if ($processRecord.createdAt -is [datetime]) {
            $processRecord.createdAt.ToUniversalTime().ToString('o')
        } else {
            [DateTimeOffset]::Parse($processRecord.createdAt).UtcDateTime.ToString('o')
        }
        if ($previewProcess -and $previewProcess.Name -eq 'node.exe' -and
            $previewProcess.CommandLine.Contains($processRecord.script) -and
            $previewProcess.CreationDate.ToUniversalTime().ToString('o') -eq $recordedCreatedAt) {
            Stop-Process -Id $processRecord.processId
        }
        if (Get-NetTCPConnection -State Listen -LocalPort 3010 -ErrorAction SilentlyContinue) {
            throw 'Port 3010 remains occupied. No unverified process was stopped; inspect it before rollback.'
        }
    }
    Write-Host 'Preview stopped. Original http://localhost:3000 and port 8065 were not changed. Preview evidence is retained.'
    exit 0
}

New-Item -ItemType Directory -Path (Join-Path $reportRuntime 'analysis-runs') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $reportRuntime 'uploads') -Force | Out-Null
if ($Service -eq 'frontend') {
    $frontendRoot = Join-Path $reportProjectRoot 'frontend'
    $frontendScript = Join-Path $frontendRoot '.output\server\index.mjs'
    if (Get-NetTCPConnection -State Listen -LocalPort 3010 -ErrorAction SilentlyContinue) {
        throw 'Port 3010 is already in use. No live assets were rebuilt or existing process stopped.'
    }
    # Always build the explicitly targeted preview. A stale general .output can
    # have a baked-in proxy to 8065 and silently mutate the original service.
    $env:NUXT_BACKEND_URL = 'http://127.0.0.1:8085'
    & npm.cmd --prefix $frontendRoot run build
    Assert-NativeSuccess 'Frontend build failed; original Docker frontend is unchanged.'
    $proxyBundle = Join-Path $frontendRoot '.output\server\chunks\_\nitro.mjs'
    if (-not (Test-Path -LiteralPath $proxyBundle)) { throw 'Preview proxy bundle is unavailable.' }
    $proxyContent = Get-Content -LiteralPath $proxyBundle -Raw
    if (-not $proxyContent.Contains('http://127.0.0.1:8085/api/**') -or
        -not $proxyContent.Contains('http://127.0.0.1:8085/nl2sql/**')) {
        throw 'Preview proxy target was not verified. No frontend process started.'
    }
    $env:NITRO_HOST = '127.0.0.1'
    $env:NITRO_PORT = '3010'
    $frontendArgument = '"' + $frontendScript + '"'
    $previewChild = Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList $frontendArgument `
        -WorkingDirectory $frontendRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $reportRuntime 'frontend.log') `
        -RedirectStandardError (Join-Path $reportRuntime 'frontend.error.log')
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($previewChild.Id)"
    @{ processId = $previewChild.Id; script = $frontendScript;
       createdAt = $processInfo.CreationDate.ToUniversalTime().ToString('o') } |
        ConvertTo-Json | Out-File -LiteralPath (Join-Path $reportRuntime 'frontend-process.json') -Encoding utf8
    Write-Host 'Preview UI: http://localhost:3010/report-center. Original UI remains at port 3000.'
    exit 0
}

if ($Build) {
    if (& git status --porcelain) { throw 'Commit the reviewed release before building an identified image.' }
    $releaseRevision = & git rev-parse HEAD
    Assert-NativeSuccess 'Cannot resolve source revision.'
    & docker build -f docker/Dockerfile.app-update --build-arg RUNTIME_IMAGE=dataagent-python-backend:bi-rollback-20261013 `
        --build-arg "BUILD_REVISION=$releaseRevision" -t dataagent-python-backend:report-v0.2.1 .
    Assert-NativeSuccess 'Runtime build failed; original images were preserved.'
}
$previewName = if ($Service -eq 'backend') { 'dataagent-report-v021-backend' } else { 'dataagent-report-v021-mcp' }
$previewPort = if ($Service -eq 'backend') { '127.0.0.1:8085:8065' } else { '127.0.0.1:8086:8066' }
$existingName = & docker ps -a --filter "name=^/$previewName$" --format '{{.Names}}'
if ($existingName -eq $previewName) {
    $existingContainer = & docker inspect $previewName | ConvertFrom-Json
    Assert-NativeSuccess 'Cannot inspect the existing preview.'
    if ($existingContainer.Config.Labels.'dataagent.release' -ne '0.2.1') {
        throw 'Existing container has an unrecognized release label. Nothing started.'
    }
    $previewEnvironment = @{}
    foreach ($entry in $existingContainer.Config.Env) {
        $parts = $entry.Split('=', 2)
        $previewEnvironment[$parts[0]] = $parts[1]
    }
    if ($previewEnvironment['DATAAGENT_MANAGEMENT_DATABASE_URL'] -notmatch '@mysql:3306/saa_data_agent_v020_preview\?charset=utf8mb4$' -or
        $previewEnvironment['DATAAGENT_REDIS_KEY_PREFIX'] -ne 'dataagent-report-v020') {
        throw 'Existing container does not use the isolated preview database/namespace. Nothing started.'
    }
    foreach ($previewMount in @(
        @{ destination = '/app/data/analysis-runs'; relative = 'analysis-runs' },
        @{ destination = '/app/uploads'; relative = 'uploads' }
    )) {
        $actualMount = @($existingContainer.Mounts | Where-Object Destination -eq $previewMount.destination)
        $expectedSource = Get-ComparablePreviewPath (Join-Path $reportRuntime $previewMount.relative)
        if ($actualMount.Count -ne 1 -or $actualMount[0].Type -ne 'bind' -or
            (Get-ComparablePreviewPath $actualMount[0].Source) -ne $expectedSource -or -not $actualMount[0].RW) {
            throw 'Existing container does not have the expected isolated writable directories. Nothing started.'
        }
    }
    $containerImage = & docker inspect $previewName --format '{{.Image}}'
    $releaseImage = & docker image inspect dataagent-python-backend:report-v0.2.1 --format '{{.Id}}'
    Assert-NativeSuccess 'Release image is unavailable.'
    if ($containerImage -ne $releaseImage) { throw 'Existing preview uses a different image; preserve and rename it before making a new preview.' }
    & docker start $previewName
} else {
    & docker compose -f docker-compose.yml -f docker-compose.bi-preview.yml -f docker-compose.report-v020.yml `
        run --no-deps --detach --name $previewName --label dataagent.release=0.2.1 --publish $previewPort $Service
}
Assert-NativeSuccess 'Preview start failed; check its named container logs.'
Write-Host 'Preview uses a separate copied management database and Redis namespace. Original services are untouched.'
