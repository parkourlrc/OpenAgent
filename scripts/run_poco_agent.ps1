$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pocoRoot = Join-Path $repoRoot "tmp_refs\\poco-agent"
$expectedOrigin = "https://github.com/poco-ai/poco-agent"

#
# NOTE: Poco runs multiple services and installs a fair amount of deps.
# To avoid filling up C: (often small on dev machines), redirect caches/temp to D: (repo drive).
#
$cacheRoot = Join-Path $repoRoot ".local_cache\\poco-agent"
$tmpDir = Join-Path $cacheRoot "tmp"
$pipCache = Join-Path $cacheRoot "pip-cache"
$uvCache = Join-Path $cacheRoot "uv-cache"
$uvPythonDir = Join-Path $cacheRoot "uv-python"
$pnpmStore = Join-Path $cacheRoot "pnpm-store"
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
New-Item -ItemType Directory -Force -Path $pipCache | Out-Null
New-Item -ItemType Directory -Force -Path $uvCache | Out-Null
New-Item -ItemType Directory -Force -Path $uvPythonDir | Out-Null
New-Item -ItemType Directory -Force -Path $pnpmStore | Out-Null

$env:TEMP = $tmpDir
$env:TMP = $tmpDir
$env:PIP_CACHE_DIR = $pipCache
$env:UV_CACHE_DIR = $uvCache
$env:UV_PYTHON_INSTALL_DIR = $uvPythonDir
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Minimal defaults so Poco backend/manager can boot locally.
# They require S3 settings at import time (even if you won't use attachments/workspace export).
if (-not $env:S3_ENDPOINT) { $env:S3_ENDPOINT = "http://127.0.0.1:9000" }
if (-not $env:S3_ACCESS_KEY) { $env:S3_ACCESS_KEY = "poco" }
if (-not $env:S3_SECRET_KEY) { $env:S3_SECRET_KEY = "poco" }
if (-not $env:S3_BUCKET) { $env:S3_BUCKET = "poco-local" }
if (-not $env:S3_REGION) { $env:S3_REGION = "us-east-1" }
if (-not $env:S3_FORCE_PATH_STYLE) { $env:S3_FORCE_PATH_STYLE = "true" }
if (-not $env:S3_PRESIGN_EXPIRES) { $env:S3_PRESIGN_EXPIRES = "300" }

$backendPort = 8010
$managerPort = 8011
$executorPort = 8082
$frontendPort = 3002

$backendUrl = "http://127.0.0.1:$backendPort"
$managerUrl = "http://127.0.0.1:$managerPort"
$executorUrl = "http://127.0.0.1:$executorPort"
$frontendApiBase = $backendUrl
# Use JSON to avoid pydantic list parsing ambiguity.
$corsOriginsJson = ('["http://localhost:{0}","http://127.0.0.1:{0}"]' -f $frontendPort)
$env:CORS_ORIGINS = $corsOriginsJson
$env:BACKEND_URL = $backendUrl
$env:EXECUTOR_URL = $executorUrl
$env:CALLBACK_BASE_URL = $managerUrl
$env:EXECUTOR_MANAGER_URL = $managerUrl
$env:NO_PROXY = "127.0.0.1,localhost"
$env:no_proxy = $env:NO_PROXY

# Local dev: Executor Manager can run without Docker by dispatching directly to a running Executor service.
if (-not $env:EXECUTOR_MODE) { $env:EXECUTOR_MODE = "external" }

# Poco uses uv-managed Python 3.12 environments. A global PYTHONPATH pointing at Python 3.11 stdlib
# will break imports (e.g., "SRE module mismatch"). Clear it for this process and all child services.
if ($env:PYTHONPATH -and $env:PYTHONPATH.Trim()) {
  Write-Host ("Clearing PYTHONPATH for Poco run (was: {0})" -f $env:PYTHONPATH)
}
$env:PYTHONPATH = ""

$script:UvExe = $null

function Resolve-UvExe {
  if ($script:UvExe) { return $script:UvExe }
  $cmd = Get-Command uv -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) {
    $script:UvExe = $cmd.Source
    return $script:UvExe
  }

  # Common pip --user location on Windows: %APPDATA%\Python\PythonXY\Scripts\uv.exe
  try {
    $base = Join-Path $env:APPDATA "Python"
    if (Test-Path $base) {
      $candidates = Get-ChildItem -Directory -ErrorAction SilentlyContinue $base |
        Where-Object { $_.Name -match '^Python\d+$' } |
        Sort-Object Name -Descending
      foreach ($d in $candidates) {
        $p = Join-Path $d.FullName "Scripts\\uv.exe"
        if (Test-Path $p) {
          $script:UvExe = $p
          return $script:UvExe
        }
      }
    }
  } catch {}

  try {
    $path = (python -c "from uv._find_uv import find_uv_bin; print(find_uv_bin())" 2>$null)
    $path = ($path | Select-Object -First 1)
    if ($path -and (Test-Path $path)) {
      $script:UvExe = $path
      return $script:UvExe
    }
  } catch {}
  return $null
}

function Invoke-Uv([string[]]$uvArgs) {
  $uv = Resolve-UvExe
  if (!$uv) { throw "uv is not available (missing uv.exe). Try re-running the script to reinstall uv." }
  & $uv @uvArgs
  if ($LASTEXITCODE -ne 0) {
    throw ("uv failed (exit={0}): uv {1}" -f $LASTEXITCODE, ($uvArgs -join " "))
  }
}

function Assert-RepoOrigin([string]$repoDir, [string]$expected) {
  if (!(Test-Path (Join-Path $repoDir ".git"))) {
    throw "Path exists but is not a git repo: $repoDir. Delete it and re-run."
  }
  $origin = ""
  try { $origin = (git -C $repoDir remote get-url origin 2>$null) } catch {}
  if (!$origin) { throw "Missing git remote 'origin' in: $repoDir" }
  $norm = ($origin.Trim().ToLower() -replace "\\.git$","")
  $need = ($expected.Trim().ToLower() -replace "\\.git$","")
  if ($norm -notlike "*github.com/poco-ai/poco-agent*") {
    throw "Unexpected origin for Poco repo: $origin`nExpected: $expected`nRepo: $repoDir"
  }
}

if (!(Test-Path $pocoRoot)) {
  if (!(Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git not found. Install Git for Windows first."
  }
  New-Item -ItemType Directory -Force -Path (Split-Path $pocoRoot -Parent) | Out-Null
  git clone $expectedOrigin $pocoRoot
} else {
  Assert-RepoOrigin -repoDir $pocoRoot -expected $expectedOrigin
  try {
    git -C $pocoRoot fetch --all --tags | Out-Null
    git -C $pocoRoot pull --ff-only | Out-Null
  } catch {
    Write-Host ("Warning: failed to update Poco repo: {0}" -f $_.Exception.Message)
    Write-Host "You can delete tmp_refs\\poco-agent and re-run to re-clone."
  }
}

function Ensure-Uv {
  if (Resolve-UvExe) { return }
  if (!(Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python not found (need Python 3.11+ to bootstrap uv)."
  }
  python -m pip install --user --upgrade uv --no-cache-dir
  if (!(Resolve-UvExe)) { throw "uv install failed. (uv.exe not found after install)" }
  # Ensure child PowerShell windows can find uv.exe.
  $uvDir = Split-Path (Resolve-UvExe) -Parent
  if ($uvDir -and ($env:PATH -notlike "*$uvDir*")) {
    $env:PATH = "$uvDir;$env:PATH"
  }
}

function Sync-PythonService([string]$serviceDir) {
  if (!(Test-Path $serviceDir)) { throw "missing dir: $serviceDir" }
  Push-Location $serviceDir
  try {
    Invoke-Uv @("sync")
  } finally {
    Pop-Location
  }
}

function Run-UvInDir([string]$serviceDir, [string[]]$uvArgs) {
  if (!(Test-Path $serviceDir)) { throw "missing dir: $serviceDir" }
  Push-Location $serviceDir
  try {
    Invoke-Uv $uvArgs
  } finally {
    Pop-Location
  }
}

function Ensure-FrontendDeps([string]$frontendDir) {
  if (!(Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm not found. Try: corepack enable"
  }
  if (!(Test-Path $frontendDir)) { throw "missing dir: $frontendDir" }
  if (Test-Path (Join-Path $frontendDir "node_modules")) { return }
  Push-Location $frontendDir
  try {
    pnpm install --store-dir $pnpmStore
  } finally {
    Pop-Location
  }
}

Ensure-Uv

# Poco requires Python 3.12+. Let uv manage the runtime automatically.
Invoke-Uv @("python", "install", "3.12", "--install-dir", $uvPythonDir) | Out-Null

$stateDir = Join-Path $pocoRoot ".local_run"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$pidPath = Join-Path $stateDir "pids.json"
$logsDir = Join-Path $stateDir "logs"
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
$workspaceDir = Join-Path $stateDir "workspace"
New-Item -ItemType Directory -Force -Path $workspaceDir | Out-Null
$dbFile = Join-Path $stateDir "opencowork.db"
$dbUrl = "sqlite:///{0}" -f (($dbFile) -replace "\\\\","/")

$backendDir = Join-Path $pocoRoot "backend"
$executorDir = Join-Path $pocoRoot "executor"
$managerDir = Join-Path $pocoRoot "executor_manager"
$frontendDir = Join-Path $pocoRoot "frontend"

function Test-HttpOk([string]$url) {
  try {
    $req = [System.Net.HttpWebRequest]::Create($url)
    $req.Method = "GET"
    $req.Timeout = 2000
    $req.AllowAutoRedirect = $false
    $resp = $req.GetResponse()
    try {
      $code = [int]$resp.StatusCode
      return ($code -ge 200 -and $code -lt 500)
    } finally {
      $resp.Close()
    }
  } catch [System.Net.WebException] {
    $resp = $_.Exception.Response
    if ($resp) {
      try {
        $code = [int]$resp.StatusCode
        return ($code -ge 200 -and $code -lt 500)
      } finally {
        $resp.Close()
      }
    }
    return $false
  } catch {
    return $false
  }
}

try {
  $lockPath = Join-Path $frontendDir ".next\\dev\\lock"
  if (Test-Path $lockPath) {
    [System.IO.File]::Delete($lockPath)
  }
} catch {}

Write-Host "Installing Python deps (uv sync)..."
Sync-PythonService $backendDir
Sync-PythonService $executorDir
Sync-PythonService $managerDir

Write-Host "Initializing backend DB schema (SQLAlchemy create_all)..."
$env:DATABASE_URL = $dbUrl
Run-UvInDir $backendDir @(
  "run", "python", "-c",
  "from app.core.database import engine; from app.models import Base; Base.metadata.create_all(bind=engine)"
)

Write-Host "Installing frontend deps (pnpm install)..."
Ensure-FrontendDeps $frontendDir

Write-Host "Starting Poco services..."

$procs = @()
$started = @()
$backendOut = Join-Path $logsDir "backend.out.log"
$backendErr = Join-Path $logsDir "backend.err.log"
if (Test-HttpOk ("{0}/api/v1/health" -f $backendUrl)) {
  Write-Host ("Backend already running on :{0}" -f $backendPort)
} else {
  $p = Start-Process -PassThru -FilePath powershell -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-Command", "cd '$backendDir'; `$env:PYTHONPATH=''; `$env:DATABASE_URL='$dbUrl'; & '$($script:UvExe)' run python -m uvicorn app.main:app --host 127.0.0.1 --port $backendPort"
  ) -WindowStyle Hidden -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
  $procs += $p
  $started += @{ name = "backend"; pid = $p.Id }
}

$executorOut = Join-Path $logsDir "executor.out.log"
$executorErr = Join-Path $logsDir "executor.err.log"
if (Test-HttpOk ("{0}/health" -f $executorUrl)) {
  Write-Host ("Executor already running on :{0}" -f $executorPort)
} else {
  $p = Start-Process -PassThru -FilePath powershell -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-Command", "cd '$executorDir'; `$env:PYTHONPATH=''; `$env:WORKSPACE_PATH='$workspaceDir'; if (-not `$env:DEFAULT_MODEL) { `$env:DEFAULT_MODEL='claude-sonnet-4-20250514' }; & '$($script:UvExe)' run python -m uvicorn app.main:app --host 127.0.0.1 --port $executorPort"
  ) -WindowStyle Hidden -RedirectStandardOutput $executorOut -RedirectStandardError $executorErr
  $procs += $p
  $started += @{ name = "executor"; pid = $p.Id }
}

$managerOut = Join-Path $logsDir "executor_manager.out.log"
$managerErr = Join-Path $logsDir "executor_manager.err.log"
if (Test-HttpOk ("{0}/api/v1/health" -f $managerUrl)) {
  Write-Host ("Executor Manager already running on :{0}" -f $managerPort)
} else {
  $p = Start-Process -PassThru -FilePath powershell -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-Command", "cd '$managerDir'; `$env:PYTHONPATH=''; & '$($script:UvExe)' run python -m uvicorn app.main:app --host 127.0.0.1 --port $managerPort"
  ) -WindowStyle Hidden -RedirectStandardOutput $managerOut -RedirectStandardError $managerErr
  $procs += $p
  $started += @{ name = "executor_manager"; pid = $p.Id }
}

$frontendOut = Join-Path $logsDir "frontend.out.log"
$frontendErr = Join-Path $logsDir "frontend.err.log"
if (Test-HttpOk ("http://127.0.0.1:{0}" -f $frontendPort)) {
  Write-Host ("Frontend already running on :{0}" -f $frontendPort)
} else {
  $p = Start-Process -PassThru -FilePath powershell -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-Command", "cd '$frontendDir'; `$env:PORT='$frontendPort'; `$env:NEXT_PUBLIC_API_URL='$backendUrl'; pnpm dev"
  ) -WindowStyle Hidden -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr
  $procs += $p
  $started += @{ name = "frontend"; pid = $p.Id }
}

$payload = @{
  started_at = (Get-Date).ToString("s")
  pids = $procs | ForEach-Object { $_.Id }
  services = $started
} | ConvertTo-Json -Depth 5

$payload | Set-Content -Encoding utf8 $pidPath

Write-Host "Poco is starting:"
Write-Host ("  Frontend: http://localhost:{0}" -f $frontendPort)
Write-Host ("  Backend:  {0} (health: /api/v1/health)" -f $backendUrl)
Write-Host ("  Manager:  {0} (health: /api/v1/health)" -f $managerUrl)
Write-Host ("  Executor: {0} (health: /health)" -f $executorUrl)
try { Start-Process ("http://localhost:{0}" -f $frontendPort) | Out-Null } catch {}
Write-Host "Stop with: scripts\\stop_poco_agent.ps1"
Write-Host ("Logs: {0}" -f $logsDir)
