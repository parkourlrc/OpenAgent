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

if (!(Test-Path (Join-Path $repoRoot ".env"))) {
  Copy-Item (Join-Path $repoRoot ".env.example") (Join-Path $repoRoot ".env")
}

Import-DotEnv (Join-Path $repoRoot ".env")

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

$uakRoot = "D:\\桌面\\research_copilot\\全新AI架构"
if (Test-Path $uakRoot) {
  # NOTE: use a wheel install (not editable) to avoid .pth path-encoding issues on Windows.
  & $venvPython -m pip install $uakRoot -i https://pypi.org/simple --timeout 120
}

& $venvPython -m app.desktop.desktop_shell
