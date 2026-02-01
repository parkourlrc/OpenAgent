$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$openworkDir = Join-Path $repoRoot "tmp_refs\\openwork"
$expectedOrigin = "https://github.com/different-ai/openwork"

function Assert-RepoOrigin([string]$repoDir, [string]$expected) {
  if (!(Test-Path (Join-Path $repoDir ".git"))) {
    throw "Path exists but is not a git repo: $repoDir. Delete it and re-run."
  }
  $origin = ""
  try { $origin = (git -C $repoDir remote get-url origin 2>$null) } catch {}
  if (!$origin) { throw "Missing git remote 'origin' in: $repoDir" }
  $norm = ($origin.Trim().ToLower() -replace "\\.git$","")
  $need = ($expected.Trim().ToLower() -replace "\\.git$","")
  if ($norm -notlike "*github.com/different-ai/openwork*") {
    throw "Unexpected origin for OpenWork repo: $origin`nExpected: $expected`nRepo: $repoDir"
  }
}

if (!(Test-Path $openworkDir)) {
  if (!(Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git not found. Install Git for Windows first."
  }
  New-Item -ItemType Directory -Force -Path (Split-Path $openworkDir -Parent) | Out-Null
  git clone $expectedOrigin $openworkDir
} else {
  Assert-RepoOrigin -repoDir $openworkDir -expected $expectedOrigin
  try {
    git -C $openworkDir fetch --all --tags | Out-Null
    git -C $openworkDir pull --ff-only | Out-Null
  } catch {
    Write-Host ("Warning: failed to update OpenWork repo: {0}" -f $_.Exception.Message)
    Write-Host "You can delete tmp_refs\\openwork and re-run to re-clone."
  }
}

if (!(Get-Command pnpm -ErrorAction SilentlyContinue)) {
  throw "pnpm not found. Try: corepack enable"
}

Set-Location $openworkDir

if (!(Test-Path (Join-Path $openworkDir "node_modules"))) {
  pnpm install
}

Write-Host "OpenWork UI-only dev server..."
Write-Host "Tip: Web(UI-only) mode has NO Tauri runtime. Actions like folder picker / local Host mode will show:"
Write-Host "  'This action requires the Tauri app runtime.'"
Write-Host "For the real desktop experience, use: scripts\\run_openwork_desktop.ps1"
Write-Host "Also: OpenWork needs an OpenCode server (opencode) for full functionality; UI-only is mainly for look & feel."

try { Start-Process "http://localhost:5173" | Out-Null } catch {}
pnpm dev:ui
