param(
  [switch]$SkipDesktopBuild,
  [string]$OutDir = "",
  [switch]$KeepStage
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$distDesktop = Join-Path $repoRoot "dist-desktop"
$desktopExe = Join-Path $distDesktop "OpenAgentWorkbench.exe"

if (-not $SkipDesktopBuild) {
  & (Join-Path $PSScriptRoot "build_desktop.ps1") -Stamp
}

if (!(Test-Path $desktopExe)) {
  throw "Desktop exe not found: $desktopExe"
}

if ([string]::IsNullOrWhiteSpace($OutDir)) {
  $OutDir = Join-Path $repoRoot "dist-installer"
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$buildId = (Get-Date -Format "yyyyMMdd-HHmmss")
$stageRoot = Join-Path $env:WINDIR ("Temp\\owb-installer-{0}" -f $buildId)
New-Item -ItemType Directory -Force -Path $stageRoot | Out-Null

# IMPORTANT: IExpress SED parsing is ANSI/ASCII-centric. Use an ASCII-only staging path, then copy the
# final setup EXE to the repo output folder (which may contain non-ASCII characters).
$payloadDir = Join-Path $stageRoot ("payload-{0}" -f $buildId)
if (Test-Path $payloadDir) { Remove-Item $payloadDir -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $payloadDir | Out-Null

# Copy desktop exe
Copy-Item -Force $desktopExe (Join-Path $payloadDir "OpenAgentWorkbench.exe")

# Bundle WebView2 bootstrapper (best-effort); installer will run it silently.
$wv2FromBuild = Join-Path $repoRoot "build-desktop\\vendor\\webview2\\MicrosoftEdgeWebView2Setup.exe"
$wv2To = Join-Path $payloadDir "MicrosoftEdgeWebView2Setup.exe"
if (Test-Path $wv2FromBuild) {
  Copy-Item -Force $wv2FromBuild $wv2To
} else {
  try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
  try {
    Invoke-WebRequest -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $wv2To -UseBasicParsing | Out-Null
  } catch {
    Write-Warning ("Failed to download WebView2 bootstrapper: {0}" -f $_.Exception.Message)
  }
}

# Installer scripts
$installerDir = Join-Path $repoRoot "scripts\\installer"
$installPs1 = Join-Path $installerDir "install.ps1"
$uninstallPs1 = Join-Path $installerDir "uninstall.ps1"
$setupCmd = Join-Path $installerDir "setup.cmd"
if (!(Test-Path $installPs1) -or !(Test-Path $uninstallPs1) -or !(Test-Path $setupCmd)) {
  throw "Missing installer scripts under $installerDir"
}
Copy-Item -Force $installPs1 (Join-Path $payloadDir "install.ps1")
Copy-Item -Force $uninstallPs1 (Join-Path $payloadDir "uninstall.ps1")
Copy-Item -Force $setupCmd (Join-Path $payloadDir "setup.cmd")

# Build IExpress SED
$sedPath = Join-Path $stageRoot ("installer-{0}.sed" -f $buildId)
$setupNameStage = Join-Path $stageRoot ("OpenAgentWorkbench-Setup-{0}.exe" -f $buildId)
$setupName = Join-Path $OutDir ("OpenAgentWorkbench-Setup-{0}.exe" -f $buildId)

# IExpress requires CRLF line endings.
$crlf = "`r`n"
$sed = @()
$sed += "[Version]"
$sed += "Class=IEXPRESS"
$sed += "SEDVersion=3"
$sed += ""
$sed += "[Options]"
$sed += "PackagePurpose=InstallApp"
$sed += "ShowInstallProgramWindow=0"
$sed += "HideExtractAnimation=1"
$sed += "UseLongFileName=1"
$sed += "InsideCompressed=0"
$sed += "CAB_FixedSize=0"
$sed += "CAB_ResvCodeSigning=0"
$sed += "RebootMode=N"
$sed += "InstallPrompt="
$sed += "DisplayLicense="
$sed += "FinishMessage="
$sed += "TargetName=$setupNameStage"
$sed += "FriendlyName=OpenAgent Workbench"
$sed += "AppLaunched=setup.cmd"
$sed += "PostInstallCmd="
$sed += "AdminQuietInstCmd="
$sed += "UserQuietInstCmd="
$sed += "SourceFiles=SourceFiles"
$sed += ""
$sed += "[SourceFiles]"
$sed += "SourceFiles0=$payloadDir"
$sed += ""
$sed += "[SourceFiles0]"
$sed += "OpenAgentWorkbench.exe="
$sed += "install.ps1="
$sed += "uninstall.ps1="
$sed += "setup.cmd="
if (Test-Path $wv2To) {
  $sed += "MicrosoftEdgeWebView2Setup.exe="
}
$sed += ""
$sedText = ($sed -join $crlf) + $crlf
$sedText | Set-Content -Encoding ASCII -NoNewline $sedPath

Write-Host "Building installer via IExpress..."
& "C:\\Windows\\System32\\iexpress.exe" /N /Q $sedPath | Out-Null

Copy-Item -Force $setupNameStage $setupName
Write-Host ("Installer built: {0}" -f $setupName)

if (-not $KeepStage) {
  try { Remove-Item $stageRoot -Recurse -Force -ErrorAction SilentlyContinue } catch {}
}
