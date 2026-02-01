$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Import-DotEnv([string]$Path) {
  if (!(Test-Path $Path)) { return }
  Get-Content $Path | ForEach-Object {
    $line = $_.Trim()
    if ($line.Length -eq 0) { return }
    if ($line.StartsWith("#")) { return }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }
    $key = $line.Substring(0, $eq).Trim()
    $val = $line.Substring($eq + 1).Trim()
    if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
      $val = $val.Substring(1, $val.Length - 2)
    }
    if ($key.Length -gt 0) {
      Set-Item -Path ("Env:{0}" -f $key) -Value $val
    }
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Load .env (optional) so OPENAI_* and UI_ADMIN_TOKEN can be configured once.
Import-DotEnv (Join-Path $repoRoot ".env")

$orchestratorDir = Join-Path $repoRoot "services\orchestrator"
Set-Location $orchestratorDir

$python = Join-Path $orchestratorDir ".venv\Scripts\python.exe"
if (!(Test-Path $python)) { $python = "python" }

# Default local data dirs under repo by default
if (!$env:DATA_DIR) { $env:DATA_DIR = (Join-Path $repoRoot "data") }
if (!$env:DB_PATH) { $env:DB_PATH = (Join-Path $env:DATA_DIR "workbench.db") }
if (!$env:WORKSPACES_DIR) { $env:WORKSPACES_DIR = (Join-Path $env:DATA_DIR "workspaces") }
if (!$env:ARTIFACTS_DIR) { $env:ARTIFACTS_DIR = (Join-Path $env:DATA_DIR "artifacts") }
if (!$env:LOGS_DIR) { $env:LOGS_DIR = (Join-Path $env:DATA_DIR "logs") }
if (!$env:SKILLS_DIR) { $env:SKILLS_DIR = (Join-Path $repoRoot "skills") }

New-Item -ItemType Directory -Force -Path $env:DATA_DIR, $env:WORKSPACES_DIR, $env:ARTIFACTS_DIR, $env:LOGS_DIR | Out-Null

# Provider config (OpenAI-compatible)
if (!$env:OPENAI_BASE_URL) { $env:OPENAI_BASE_URL = "https://0-0.pro/v1" }
if ($env:OPENAI_BASE_URL -eq "http://litellm:4000/v1") { $env:OPENAI_BASE_URL = "https://0-0.pro/v1" }
if (!$env:OPENAI_API_KEY) { $env:OPENAI_API_KEY = "CHANGE_ME" }
if (!$env:UI_ADMIN_TOKEN) { $env:UI_ADMIN_TOKEN = "admin" }

Write-Host ("Backend: http://localhost:8787 (token: {0})" -f $env:UI_ADMIN_TOKEN)
& $python -m uvicorn app.main:app --host 0.0.0.0 --port 8787
