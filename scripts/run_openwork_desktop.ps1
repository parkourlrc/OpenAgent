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
  }
}

if (!(Get-Command pnpm -ErrorAction SilentlyContinue)) {
  throw "pnpm not found. Try: corepack enable"
}

function Require-Cmd([string]$name, [string]$hint) {
  if (Get-Command $name -ErrorAction SilentlyContinue) { return }
  Write-Host ("Missing dependency: {0}" -f $name)
  Write-Host $hint
  exit 2
}

# Tauri desktop build prerequisites on Windows:
# - Rust toolchain (cargo/rustc)
# - MSVC build tools (cl.exe) for native crates
Require-Cmd -name "cargo" -hint "Install Rust: https://rustup.rs/  (then reopen PowerShell)"
Require-Cmd -name "rustc" -hint "Install Rust: https://rustup.rs/  (then reopen PowerShell)"

if (!(Get-Command cl.exe -ErrorAction SilentlyContinue)) {
  Write-Host "Missing dependency: cl.exe (MSVC C++ build tools)"
  Write-Host "Install Visual Studio Build Tools with 'Desktop development with C++' workload, then reopen PowerShell."
  Write-Host "https://visualstudio.microsoft.com/visual-cpp-build-tools/"
  exit 2
}

Set-Location $openworkDir

if (!(Test-Path (Join-Path $openworkDir "node_modules"))) {
  pnpm install
}

Write-Host "Ensuring tauri-cli..."
if (!(Get-Command tauri -ErrorAction SilentlyContinue)) {
  cargo install tauri-cli
}

if (!(Get-Command opencode -ErrorAction SilentlyContinue)) {
  Write-Host "Note: 'opencode' not found on PATH."
  Write-Host "OpenWork Host mode uses OpenCode CLI (`opencode serve`)."
  Write-Host "You can still run the desktop app UI, but Host mode will fail until opencode is installed."
}

Write-Host "Starting OpenWork desktop (Tauri)..."
pnpm dev

