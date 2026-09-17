# 彻底杀掉所有 uvicorn / backend.main 相关进程（含系统 Python310 与 venv，以及 reloader）
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*backend.main*'
} | ForEach-Object { taskkill /PID $_.ProcessId /F 2>&1 | Out-Null }
# 按端口循环杀掉持有 8000 的进程（含 fork 出来的 worker）
for ($i=0; $i -lt 12; $i++) {
    $p = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
    if ($p) { taskkill /PID $p /F 2>&1 | Out-Null; Start-Sleep -Seconds 1 } else { break }
}
Start-Sleep -Seconds 2
# 二次确认无残留 uvicorn
Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*backend.main*'
} | ForEach-Object { taskkill /PID $_.ProcessId /F 2>&1 | Out-Null }
Start-Sleep -Seconds 1
# 仅用 venv 启动
$env:PYTHONPATH = "C:\Users\Administrator\Desktop\my_ai_assistant"
Set-Location C:\Users\Administrator\Desktop\my_ai_assistant
Start-Process -FilePath ".\backend\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","backend.main:app","--host","127.0.0.1","--port","8000","--reload" -WorkingDirectory "C:\Users\Administrator\Desktop\my_ai_assistant" -RedirectStandardOutput "backend_run.log" -RedirectStandardError "backend_err.log" -NoNewWindow
Start-Sleep -Seconds 10
try { (Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 5).StatusCode } catch { Write-Host "FAIL $($_.Exception.Message)" }
# 确认监听进程是 venv（而非系统 Python310）
$lp = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
Write-Host "listener pid=$lp"
if ($lp) { Get-CimInstance Win32_Process -Filter "ProcessId=$lp" | Select-Object ProcessId, CommandLine | Format-List }
Write-Host "=== running CRUD verify ==="
& ".\backend\.venv\Scripts\python.exe" "_verify_crud33.py"
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_verify_crud33.py -Force -ErrorAction SilentlyContinue
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_restart33b.ps1 -Force -ErrorAction SilentlyContinue
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_diag33.ps1 -Force -ErrorAction SilentlyContinue
