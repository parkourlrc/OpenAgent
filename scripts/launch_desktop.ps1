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
    if ($key.Length -gt 0 -and [string]::IsNullOrEmpty((Get-Item -Path ("Env:{0}" -f $key) -ErrorAction SilentlyContinue).Value)) {
      Set-Item -Path ("Env:{0}" -f $key) -Value $val
    }
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Import-DotEnv (Join-Path $repoRoot ".env")

$orchestratorDir = Join-Path $repoRoot "services\\orchestrator"
$pythonw = Join-Path $orchestratorDir ".venv\\Scripts\\pythonw.exe"
if (!(Test-Path $pythonw)) {
  throw "Missing venv pythonw.exe. Run scripts\\run_desktop.ps1 once to install dependencies."
}

Start-Process -FilePath $pythonw -WorkingDirectory $orchestratorDir -ArgumentList @('-m','app.desktop.desktop_shell') | Out-Null

