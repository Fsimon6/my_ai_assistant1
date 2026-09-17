$p = Get-CimInstance Win32_Process -Filter "ProcessId=22464" -ErrorAction SilentlyContinue
if ($p) {
  Write-Host "=== 22464 CommandLine ==="; Write-Host $p.CommandLine
  Write-Host "=== 22464 ParentProcessId ==="; Write-Host $p.ParentProcessId
  $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue
  if ($pp) { Write-Host "=== PARENT CommandLine ==="; Write-Host $pp.CommandLine }
} else { Write-Host "22464 not found" }
Write-Host "=== ALL uvicorn/backend/multiprocessing procs ==="
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*backend*' -or $_.CommandLine -like '*multiprocessing*' } | ForEach-Object { Write-Host "$($_.ProcessId) PP=$($_.ParentProcessId) EXE=$($_.ExecutablePath): $($_.CommandLine)" }
Remove-Item _diag33e.ps1 -Force -ErrorAction SilentlyContinue
