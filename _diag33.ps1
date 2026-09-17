$pid = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
Write-Host "listener pid=$pid"
if ($pid) {
  Get-CimInstance Win32_Process -Filter "ProcessId=$pid" | Select-Object ProcessId, CreationDate, CommandLine | Format-List
}
Write-Host "=== all python procs ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, CreationDate, CommandLine | Format-List
Write-Host "=== err tail ==="
Get-Content C:\Users\Administrator\Desktop\my_ai_assistant\backend_err.log -Tail 20
