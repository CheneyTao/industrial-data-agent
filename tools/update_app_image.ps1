param(
    [Parameter(Mandatory = $true)][string]$RuntimeImage,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectPath
try {
    # Resolve the real configured tag; never assume :latest is the running tag.
    $configurationText = & docker compose config --format json
    if ($LASTEXITCODE -ne 0) { throw 'Could not read Compose configuration' }
    $configuration = ($configurationText | Out-String) | ConvertFrom-Json
    $applicationImage = $configuration.services.backend.image
    if (-not $applicationImage -or $configuration.services.mcp.image -ne $applicationImage) {
        throw 'Backend and MCP must explicitly use the same application image'
    }
    $localManifestHash = (Get-FileHash -LiteralPath 'pyproject.toml' -Algorithm SHA256).Hash.ToLowerInvariant()
    $runtimeManifestHash = & docker run --rm --network none --entrypoint python $RuntimeImage -c "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('/app/pyproject.toml').read_bytes()).hexdigest())"
    if ($LASTEXITCODE -ne 0 -or $runtimeManifestHash.Trim() -ne $localManifestHash) {
        throw 'Runtime dependency manifest differs; use the full Dockerfile build instead'
    }
    $backupTag = 'dataagent-python-backend:before-update-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    & docker image tag $applicationImage $backupTag
    if ($LASTEXITCODE -ne 0) { throw 'Could not preserve current application image' }
    & docker build -f docker/Dockerfile.app-update --build-arg "RUNTIME_IMAGE=$RuntimeImage" -t $applicationImage .
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed; no restart performed' }
    if ($Restart) {
        & docker compose up -d --no-deps --force-recreate backend mcp
        if ($LASTEXITCODE -ne 0) { throw 'Application restart failed' }
        $expectedImage = & docker image inspect --format '{{.Id}}' $applicationImage
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect built image' }
        foreach ($service in @('backend', 'mcp')) {
            $containerId = & docker compose ps -q $service
            if ($LASTEXITCODE -ne 0 -or -not $containerId) { throw "Missing running service: $service" }
            $runningImage = & docker inspect --format '{{.Image}}' $containerId
            if ($LASTEXITCODE -ne 0 -or $runningImage.Trim() -ne $expectedImage.Trim()) {
                throw "Running $service did not load the built image"
            }
        }
    }
    Write-Output "Built configured image: $applicationImage; previous image: $backupTag"
}
finally {
    Pop-Location
}
