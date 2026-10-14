param(
    [ValidateSet('backend', 'mcp', 'frontend')][string]$Mode = 'backend',
    [switch]$Build
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if ($Mode -eq 'frontend') {
    Set-Location -LiteralPath (Join-Path $projectRoot 'frontend')
    $env:NUXT_BACKEND_URL = 'http://127.0.0.1:8076'
    if ($Build -or -not (Test-Path -LiteralPath '.output/server/index.mjs')) {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    $env:NITRO_HOST = '127.0.0.1'
    $env:NITRO_PORT = '3006'
    & node .output/server/index.mjs
    exit $LASTEXITCODE
}
if ($Build) {
    & docker build -f docker/Dockerfile.sandbox-analytics -t dataagent-python-sandbox:analytics .
    if ($LASTEXITCODE -ne 0) { throw 'Analytics sandbox build failed; original services were not changed.' }
    & docker build -f docker/Dockerfile.bi-preview --build-arg RUNTIME_IMAGE=dataagent-python-backend:local -t dataagent-python-backend:bi-preview .
    if ($LASTEXITCODE -ne 0) { throw 'BI runtime build failed; original services were not changed.' }
}
& docker image inspect dataagent-python-sandbox:analytics --format '{{.Id}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Analytics sandbox image missing. Run this script with -Build first.' }
$containerName = if ($Mode -eq 'backend') { 'dataagent-bi-preview' } else { 'dataagent-bi-mcp' }
$mapping = if ($Mode -eq 'backend') { '127.0.0.1:8076:8065' } else { '127.0.0.1:8077:8066' }
$existing = & docker ps -a --filter "name=^/$containerName$" --format '{{.Names}}'
if ($existing -eq $containerName) {
    $runningImage = & docker inspect $containerName --format '{{.Image}}'
    $builtImage = & docker image inspect dataagent-python-backend:bi-preview --format '{{.Id}}'
    if ($runningImage -ne $builtImage) {
        throw "Existing $containerName uses an older image. Preserve its evidence and explicitly recreate this preview container."
    }
    & docker start $containerName
} else {
    New-Item -ItemType Directory -Force -Path 'work/bi-preview-docker/analysis-runs' | Out-Null
    & docker compose -f docker-compose.yml -f docker-compose.bi-preview.yml run --no-deps --detach --name $containerName --publish $mapping $Mode
}
if ($LASTEXITCODE -ne 0) { throw 'Preview start failed; inspect the named container logs.' }
