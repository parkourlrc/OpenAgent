$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Stop-ByProcessId([int]$processId) {
  try {
    Stop-Process -Id $processId -Force -ErrorAction Stop
    Write-Output ("stopped pid {0}" -f $processId)
  } catch {
    Write-Output ("skip pid {0}: {1}" -f $processId, $_.Exception.Message)
  }
}

# Stop packaged desktop app instances (tray/background).
Get-Process -ErrorAction SilentlyContinue |
  Where-Object { $_.ProcessName -like "OpenAgentWorkbench*" } |
  ForEach-Object { Stop-ByProcessId $_.Id }

# Stop dev/local uvicorn servers for this project.
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {
    $_.CommandLine -and
    $_.CommandLine -match "uvicorn\\s+app\\.main:app" -and
    $_.CommandLine -match "openagent_workbench"
  } |
  ForEach-Object { Stop-ByProcessId ([int]$_.ProcessId) }

# If port 8787 is still held, kill the owning process (best-effort).
$conn = Get-NetTCPConnection -State Listen -LocalPort 8787 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn -and $conn.OwningProcess) {
  Stop-ByProcessId ([int]$conn.OwningProcess)
}
