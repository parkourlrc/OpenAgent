param(
  [string]$InstallDir = "",
  [switch]$Launch
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function _try([scriptblock]$b) {
  try { & $b } catch {}
}

$payloadDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcExe = Join-Path $payloadDir "OpenAgentWorkbench.exe"
if (!(Test-Path $srcExe)) {
  throw "Missing payload exe: $srcExe"
}

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
  $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\\OpenAgentWorkbench"
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$dstExe = Join-Path $InstallDir "OpenAgentWorkbench.exe"
Copy-Item -Force $srcExe $dstExe

# Copy bundled WebView2 bootstrapper (optional) and attempt silent install (best-effort).
$wv2Src = Join-Path $payloadDir "MicrosoftEdgeWebView2Setup.exe"
if (Test-Path $wv2Src) {
  $wv2Dir = Join-Path $InstallDir "vendor\\webview2"
  New-Item -ItemType Directory -Force -Path $wv2Dir | Out-Null
  $wv2Dst = Join-Path $wv2Dir "MicrosoftEdgeWebView2Setup.exe"
  Copy-Item -Force $wv2Src $wv2Dst
  _try { Start-Process -FilePath $wv2Dst -ArgumentList @('/silent','/install') -Wait -WindowStyle Hidden }
}

# Copy uninstaller script
$srcUninstall = Join-Path $payloadDir "uninstall.ps1"
if (Test-Path $srcUninstall) {
  Copy-Item -Force $srcUninstall (Join-Path $InstallDir "uninstall.ps1")
}

# Start menu + desktop shortcuts
$startMenuDir = Join-Path $env:APPDATA "Microsoft\\Windows\\Start Menu\\Programs\\OpenAgentWorkbench"
New-Item -ItemType Directory -Force -Path $startMenuDir | Out-Null
$desktopDir = [Environment]::GetFolderPath("Desktop")

_try {
  $ws = New-Object -ComObject WScript.Shell
  $lnk1 = $ws.CreateShortcut((Join-Path $startMenuDir "OpenAgentWorkbench.lnk"))
  $lnk1.TargetPath = $dstExe
  $lnk1.WorkingDirectory = $InstallDir
  $lnk1.IconLocation = $dstExe
  $lnk1.Save()

  $lnk2 = $ws.CreateShortcut((Join-Path $desktopDir "OpenAgentWorkbench.lnk"))
  $lnk2.TargetPath = $dstExe
  $lnk2.WorkingDirectory = $InstallDir
  $lnk2.IconLocation = $dstExe
  $lnk2.Save()
}

# Register uninstall entry (HKCU only; no admin required)
_try {
  $key = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\OpenAgentWorkbench"
  New-Item -Path $key -Force | Out-Null
  Set-ItemProperty -Path $key -Name "DisplayName" -Value "OpenAgentWorkbench" -Type String
  Set-ItemProperty -Path $key -Name "Publisher" -Value "OpenAgent" -Type String
  Set-ItemProperty -Path $key -Name "InstallLocation" -Value $InstallDir -Type String
  Set-ItemProperty -Path $key -Name "DisplayIcon" -Value $dstExe -Type String
  $uninstallPs1 = Join-Path $InstallDir "uninstall.ps1"
  if (Test-Path $uninstallPs1) {
    $cmd = "powershell.exe -ExecutionPolicy Bypass -NoProfile -File `"$uninstallPs1`""
    Set-ItemProperty -Path $key -Name "UninstallString" -Value $cmd -Type String
    Set-ItemProperty -Path $key -Name "QuietUninstallString" -Value $cmd -Type String
  }
}

if ($Launch) {
  _try { Start-Process -FilePath $dstExe -WorkingDirectory $InstallDir }
}

Write-Host ("Installed to: {0}" -f $InstallDir)

