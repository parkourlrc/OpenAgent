$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pocoRoot = Join-Path $repoRoot "tmp_refs\\poco-agent"
$stateDir = Join-Path $pocoRoot ".local_run"
$pidPath = Join-Path $stateDir "pids.json"
$ports = @(8010, 8011, 8082, 3002, 8001, 8080, 3000)

function Stop-ByProcessId([int]$processId) {
  try {
    Stop-Process -Id $processId -Force -ErrorAction Stop
    Write-Output ("stopped pid {0}" -f $processId)
  } catch {
    Write-Output ("skip pid {0}: {1}" -f $processId, $_.Exception.Message)
  }
}

if (Test-Path $pidPath) {
  try {
    $data = Get-Content -Raw -Encoding utf8 $pidPath | ConvertFrom-Json
    foreach ($raw in @($data.pids)) {
      $procId = $raw -as [int]
      if ($procId) {
        Stop-ByProcessId $procId
      }
    }
  } catch {
    Write-Output ("failed reading pids.json: {0}" -f $_.Exception.Message)
  }
}

function Get-ListenerProcessIds([int]$port) {
  $ids = New-Object System.Collections.Generic.List[int]
  try {
    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
      $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
      foreach ($c in $conns) {
        if ($c.OwningProcess) { $ids.Add([int]$c.OwningProcess) }
      }
      return $ids.ToArray()
    }
  } catch {}

  try {
    $lines = netstat -ano | Select-String -Pattern (":$port\\s") -SimpleMatch
    foreach ($line in $lines) {
      $parts = ($line.ToString() -split "\\s+") | Where-Object { $_ }
      if ($parts.Length -ge 5 -and $parts[3] -eq "LISTENING") {
        $ids.Add([int]$parts[-1])
      }
    }
  } catch {}

  return ($ids | Select-Object -Unique)
}

foreach ($port in $ports) {
  foreach ($procId in (Get-ListenerProcessIds -port $port)) {
    try {
      $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction Stop
      $cmd = $proc.CommandLine
      if ($cmd -and ($cmd -match "uvicorn\\s+app\\.main:app" -or $cmd -match "\\bnext\\b" -or $cmd -match "\\bpnpm\\b" -or $cmd -match "poco-agent")) {
        Stop-ByProcessId $procId
      }
    } catch {}
  }
}

# Fallback: kill any processes started from the Poco repo (best-effort).
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match "tmp_refs\\\\poco-agent" } |
  ForEach-Object { Stop-ByProcessId ([int]$_.ProcessId) }
