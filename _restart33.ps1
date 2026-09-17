# 彻底杀掉所有 uvicorn（reloader + worker）
Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%uvicorn%'" | ForEach-Object { taskkill /PID $_.ProcessId /F 2>&1 | Out-Null }
# 反复确保 8000 释放（防止 reloader 复活 worker）
for ($i=0; $i -lt 8; $i++) {
    $p = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
    if ($p) { taskkill /PID $p /F 2>&1 | Out-Null; Start-Sleep -Seconds 1 } else { break }
}
Start-Sleep -Seconds 2
$env:PYTHONPATH = "C:\Users\Administrator\Desktop\my_ai_assistant"
Set-Location C:\Users\Administrator\Desktop\my_ai_assistant
Start-Process -FilePath ".\backend\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","backend.main:app","--host","127.0.0.1","--port","8000","--reload" -WorkingDirectory "C:\Users\Administrator\Desktop\my_ai_assistant" -RedirectStandardOutput "backend_run.log" -RedirectStandardError "backend_err.log" -NoNewWindow
Start-Sleep -Seconds 10
try { (Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 5).StatusCode } catch { Write-Host "FAIL $($_.Exception.Message)" }
Write-Host "=== running CRUD verify ==="
& ".\backend\.venv\Scripts\python.exe" "_verify_crud33.py"
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_verify_crud33.py -Force -ErrorAction SilentlyContinue
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_restart33.ps1 -Force -ErrorAction SilentlyContinue
