$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function _try([scriptblock]$b) {
  try { & $b } catch {}
}

$installDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Remove shortcuts
_try {
  $startMenuDir = Join-Path $env:APPDATA "Microsoft\\Windows\\Start Menu\\Programs\\OpenAgentWorkbench"
  if (Test-Path $startMenuDir) { Remove-Item $startMenuDir -Recurse -Force -ErrorAction SilentlyContinue }
}
_try {
  $desktopLnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "OpenAgentWorkbench.lnk"
  if (Test-Path $desktopLnk) { Remove-Item $desktopLnk -Force -ErrorAction SilentlyContinue }
}

# Uninstall registry entry
_try {
  Remove-Item "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenAgentWorkbench" -Recurse -Force -ErrorAction SilentlyContinue
}

# Remove app files
_try {
  if (Test-Path $installDir) { Remove-Item $installDir -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host "Uninstalled."

