param(
    [switch]$Portable
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $projectRoot "frontend"
$releaseDir = Join-Path $projectRoot "release"
$buildDir = Join-Path $projectRoot "build\pyinstaller"
$seedCacheDir = Join-Path $projectRoot "build\seed_cache"

Push-Location $frontendDir
try {
    $previousApiBase = $env:VITE_API_BASE_URL
    $previousDesktopBuild = $env:VITE_DESKTOP_BUILD
    $env:VITE_API_BASE_URL = ""
    $env:VITE_DESKTOP_BUILD = "1"
    npm run build
}
finally {
    $env:VITE_API_BASE_URL = $previousApiBase
    $env:VITE_DESKTOP_BUILD = $previousDesktopBuild
    Pop-Location
}

$pyInstallerAvailable = python -c "import importlib.util; print('yes' if importlib.util.find_spec('PyInstaller') else 'no')"
if ($pyInstallerAvailable.Trim() -ne "yes") {
    python -m pip install pyinstaller
}

New-Item -ItemType Directory -Force -Path $seedCacheDir | Out-Null
foreach ($cacheName in @("catalog.json", "chapters.json", "custom_catalog.json")) {
    $cacheSource = Join-Path $projectRoot "backend\.cache\$cacheName"
    if (Test-Path -LiteralPath $cacheSource) {
        Copy-Item -LiteralPath $cacheSource -Destination (Join-Path $seedCacheDir $cacheName) -Force
    }
}

Push-Location $projectRoot
try {
    $appName = if ($Portable) { "Kari-Portable" } else { "Kari" }
    $bundleMode = if ($Portable) { "--onefile" } else { "--onedir" }
    python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        $bundleMode `
        --name $appName `
        --distpath $releaseDir `
        --workpath $buildDir `
        --specpath (Join-Path $projectRoot "build") `
        --add-data "$frontendDir\dist;frontend_dist" `
        --add-data "$projectRoot\backend\static;backend\static" `
        --add-data "$seedCacheDir;seed_cache" `
        --add-data "$projectRoot\reader_sites.json;." `
        --collect-all curl_cffi `
        --collect-all playwright `
        --hidden-import fitz `
        --hidden-import rarfile `
        --exclude-module torch `
        --exclude-module pandas `
        --exclude-module scipy `
        --exclude-module matplotlib `
        --exclude-module sklearn `
        --exclude-module cv2 `
        kari_desktop.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller falhou com codigo $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

if ($Portable) {
    Write-Host "Kari portatil pronto em: $releaseDir\Kari-Portable.exe"
}
else {
    Write-Host "Kari pronto em: $releaseDir\Kari\Kari.exe"
}
