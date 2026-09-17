# 杀掉所有 uvicorn 进程（reloader + worker）
Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%uvicorn%'" | ForEach-Object {
    Write-Host "kill uvicorn $($_.ProcessId)"
    taskkill /PID $_.ProcessId /F 2>&1
}
# 反复确保 8000 释放
for ($i=0; $i -lt 6; $i++) {
    $p = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
    if ($p) { Write-Host "kill listener $p"; taskkill /PID $p /F 2>&1; Start-Sleep -Seconds 1 }
}
Start-Sleep -Seconds 2
$env:PYTHONPATH = "C:\Users\Administrator\Desktop\my_ai_assistant"
Set-Location C:\Users\Administrator\Desktop\my_ai_assistant
Start-Process -FilePath ".\backend\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","backend.main:app","--host","127.0.0.1","--port","8000","--reload" -WorkingDirectory "C:\Users\Administrator\Desktop\my_ai_assistant" -RedirectStandardOutput "backend_run.log" -RedirectStandardError "backend_err.log" -NoNewWindow
Start-Sleep -Seconds 10
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
Write-Host "listeners on 8000: $($listeners -join ',')"
try { (Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 5).StatusCode } catch { Write-Host "FAIL $($_.Exception.Message)" }
Write-Host "=== err tail ==="; Get-Content backend_err.log -Tail 4
# 清理临时脚本
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_restart32.ps1 -Force -ErrorAction SilentlyContinue
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_restart32b.ps1 -Force -ErrorAction SilentlyContinue
