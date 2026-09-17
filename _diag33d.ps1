$p = Get-CimInstance Win32_Process -Filter "ProcessId=10332" -ErrorAction SilentlyContinue
if ($p) {
  Write-Host "=== 10332 CommandLine ==="
  Write-Host $p.CommandLine
  Write-Host "=== 10332 ParentProcessId ==="
  Write-Host $p.ParentProcessId
  $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue
  if ($pp) { Write-Host "=== PARENT CommandLine ==="; Write-Host $pp.CommandLine }
} else {
  Write-Host "10332 not found"
}
Write-Host "=== ALL processes mentioning backend/uvicorn ==="
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*backend*' } | ForEach-Object { Write-Host "$($_.ProcessId): $($_.CommandLine)" }
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_diag33d.ps1 -Force -ErrorAction SilentlyContinue
