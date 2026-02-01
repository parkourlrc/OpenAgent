param(
  [switch]$Stamp
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$orchestratorDir = Join-Path $repoRoot "services\\orchestrator"
$venvPython = Join-Path $orchestratorDir ".venv\\Scripts\\python.exe"

if (!(Test-Path $venvPython)) {
  Set-Location $orchestratorDir
  python -m venv .venv
  $venvPython = Join-Path $orchestratorDir ".venv\\Scripts\\python.exe"
  & $venvPython -m pip install --upgrade pip
}

Set-Location $orchestratorDir
& $venvPython -m pip install -r requirements-desktop.txt -i https://pypi.org/simple --timeout 120

# UAK (agent runtime) - install from local source path.
# Prefer an explicit env var to keep this script ASCII-only (robust on Windows PowerShell encodings).
$uakRoot = $env:UAK_SOURCE_DIR
if ([string]::IsNullOrWhiteSpace($uakRoot)) {
  # Best-effort discovery: look for a sibling directory of this repo that contains a top-level "uak" package.
  try {
    $base = Split-Path -Parent $repoRoot
    if (Test-Path $base) {
      $cand = Get-ChildItem -Path $base -Directory -ErrorAction SilentlyContinue | Where-Object {
        (Test-Path (Join-Path $_.FullName 'uak\\__init__.py')) -or (Test-Path (Join-Path $_.FullName 'src\\uak\\__init__.py'))
      } | Select-Object -First 1
      if ($cand) { $uakRoot = $cand.FullName }
    }
  } catch {}
}
if ([string]::IsNullOrWhiteSpace($uakRoot)) {
  # Secondary discovery: look for a folder under Desktop\research_copilot that contains a top-level "uak" package.
  try {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $base = Join-Path $desktop 'research_copilot'
    if (Test-Path $base) {
      $cand = Get-ChildItem -Path $base -Directory -ErrorAction SilentlyContinue | Where-Object {
        (Test-Path (Join-Path $_.FullName 'uak\\__init__.py')) -or (Test-Path (Join-Path $_.FullName 'src\\uak\\__init__.py'))
      } | Select-Object -First 1
      if ($cand) { $uakRoot = $cand.FullName }
    }
  } catch {}
}
if ([string]::IsNullOrWhiteSpace($uakRoot) -or !(Test-Path $uakRoot)) {
  throw "UAK source not found. Set env var UAK_SOURCE_DIR to the local UAK repo path, then re-run build_desktop.ps1."
}
# NOTE: use a wheel install (not editable) to avoid .pth path-encoding issues on Windows.
& $venvPython -m pip install --upgrade --force-reinstall $uakRoot -i https://pypi.org/simple --timeout 120

Write-Host "Verifying Python dependencies..."
& $venvPython -c "import fastapi, uvicorn, jinja2, pydantic, requests, yaml; import webview, pystray, PIL; import playwright; import uak; import pptx" | Out-Null
& $venvPython -c "from playwright._impl._driver import compute_driver_executable; compute_driver_executable()" | Out-Null

$distPath = Join-Path $repoRoot "dist-desktop"
$workPath = Join-Path $repoRoot "build-desktop"

# Write build metadata so the running app can show which EXE is active.
$buildId = Get-Date -Format "yyyyMMdd-HHmmss"
$buildTime = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$buildInfoPath = Join-Path $orchestratorDir "app\\build_info.py"
$buildInfo = @'
"""
Build metadata.

scripts/build_desktop.ps1 overwrites this file during packaging so the desktop app UI can show
which build is running (helps debugging "old exe/cache" issues).
"""

BUILD_ID = "__BUILD_ID__"
BUILD_TIME = "__BUILD_TIME__"

'@
$buildInfo = $buildInfo.Replace("__BUILD_ID__", $buildId).Replace("__BUILD_TIME__", $buildTime)
$buildInfo | Set-Content -Encoding UTF8 $buildInfoPath

# Generate icon assets (original pixel-art) for the EXE.
New-Item -ItemType Directory -Force -Path $workPath | Out-Null
$iconIco = Join-Path $workPath "owb-icon.ico"
& $venvPython -m app.desktop.icon_assets --out-ico $iconIco | Out-Null

# WebView2 bootstrapper (bundled for first-run installation if missing).
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
$webview2Url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'
$webview2Dir = Join-Path $workPath 'vendor\\webview2'
New-Item -ItemType Directory -Force -Path $webview2Dir | Out-Null
$webview2Exe = Join-Path $webview2Dir 'MicrosoftEdgeWebView2Setup.exe'
if (!(Test-Path $webview2Exe)) {
  try {
    Invoke-WebRequest -Uri $webview2Url -OutFile $webview2Exe -UseBasicParsing | Out-Null
  } catch {
    Write-Warning ("Failed to download WebView2 bootstrapper: {0}" -f $_.Exception.Message)
  }
}

# If a previous build is still running, the EXE will be locked and PyInstaller will fail.
# Also stop stamped copies (OpenAgentWorkbench-YYYYMMDD-HHMMSS.exe) which otherwise lock old EXEs.
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like "OpenAgentWorkbench*" } | ForEach-Object {
  try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {}
}

# Force regeneration of spec to reflect latest sources.
$specPath = Join-Path $orchestratorDir "OpenAgentWorkbench.spec"
if (Test-Path $specPath) { Remove-Item $specPath -Force }

$exePath = Join-Path $distPath "OpenAgentWorkbench.exe"
if (Test-Path $exePath) {
  for ($i = 0; $i -lt 10; $i++) {
    try {
      Remove-Item $exePath -Force
      break
    } catch {
      Start-Sleep -Milliseconds 300
    }
  }
}

$pyInstallerArgs = @(
  '--noconfirm',
  '--onefile',
  '--windowed',
  '--name', 'OpenAgentWorkbench',
  '--icon', $iconIco,
  '--distpath', $distPath,
  '--workpath', $workPath,
  '--clean',
  '--collect-all', 'webview',
  '--collect-all', 'pystray',
  '--collect-all', 'playwright',
  '--collect-all', 'pptx',
  '--collect-submodules', 'uak',
  '--add-data', 'app\\webui\\static;app\\webui\\static',
  '--add-data', 'app\\webui\\templates;app\\webui\\templates',
  '--add-data', '..\\..\\skills;skills'
)
if (Test-Path $webview2Exe) {
  $pyInstallerArgs += @('--add-data', ("{0};vendor\\webview2" -f $webview2Exe))
}
$pyInstallerArgs += @('app\\desktop\\desktop_shell.py')
& $venvPython -m PyInstaller @pyInstallerArgs

$builtExe = Join-Path $distPath "OpenAgentWorkbench.exe"
Write-Host ("Built: {0}" -f $builtExe)

# Keep `dist-desktop` clean: remove any previous timestamped copies.
Get-ChildItem -Path $distPath -Filter "OpenAgentWorkbench-*.exe" -ErrorAction SilentlyContinue | ForEach-Object {
  try { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue } catch {}
}

# Optional: create a timestamped copy under `dist-desktop/_stamped` to bypass Windows Explorer icon cache.
if ($Stamp) {
  $stampDir = Join-Path $distPath "_stamped"
  New-Item -ItemType Directory -Force -Path $stampDir | Out-Null
  # Keep the stamped folder clean (users get confused when multiple EXEs accumulate).
  Get-ChildItem -Path $stampDir -Filter "OpenAgentWorkbench-*.exe" -ErrorAction SilentlyContinue | ForEach-Object {
    try { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue } catch {}
  }
  $stampValue = Get-Date -Format "yyyyMMdd-HHmmss"
  $stampedExe = Join-Path $stampDir ("OpenAgentWorkbench-{0}.exe" -f $stampValue)
  try {
    Copy-Item -Force $builtExe $stampedExe
    Write-Host ("Stamped: {0}" -f $stampedExe)
  } catch {}
}

# Quick smoke test: start backend mode and ping /api/health.
try {
  # Pick a truly free loopback port (avoid flaky random-port collisions).
  $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
  $listener.Start()
  $port = $listener.LocalEndpoint.Port
  $listener.Stop()
  $p = Start-Process -FilePath $builtExe -ArgumentList @('--backend', '--port', "$port") -PassThru -WindowStyle Hidden
  $ok = $false
  # Onefile extraction can be slow on first run; allow a generous startup window.
  for ($i = 0; $i -lt 240; $i++) {
    try {
      $r = Invoke-WebRequest -Uri ("http://127.0.0.1:{0}/api/health" -f $port) -UseBasicParsing -TimeoutSec 1
      if ($r -and $r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { $ok = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 250
  }
  if (-not $ok) {
    Write-Warning ("backend smoke test failed: http://127.0.0.1:{0}/api/health" -f $port)
    $workbenchLog = Join-Path $env:LOCALAPPDATA 'OpenAgentWorkbench\\data\\logs\\workbench.log'
    if (Test-Path $workbenchLog) {
      Write-Warning "---- workbench.log (tail 120 lines) ----"
      Get-Content $workbenchLog -Tail 120 | ForEach-Object { Write-Warning $_ }
    }
    throw "backend smoke test failed"
  }
} finally {
  # Always attempt to stop the smoke test process; `HasExited` can be unreliable under some hosts.
  try { if ($p) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } } catch {}
}
