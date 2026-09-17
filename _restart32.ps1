$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
if ($listener) { Write-Host "kill listener $listener"; taskkill /PID $listener /F }
Get-CimInstance Win32_Process -Filter "Name='python.exe' AND CommandLine LIKE '%uvicorn%'" | ForEach-Object { Write-Host "kill uvicorn $($_.ProcessId)"; taskkill /PID $_.ProcessId /F }
Start-Sleep -Seconds 2
$env:PYTHONPATH = "C:\Users\Administrator\Desktop\my_ai_assistant"
Set-Location C:\Users\Administrator\Desktop\my_ai_assistant
Start-Process -FilePath ".\backend\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","backend.main:app","--host","127.0.0.1","--port","8000","--reload" -WorkingDirectory "C:\Users\Administrator\Desktop\my_ai_assistant" -RedirectStandardOutput "backend_run.log" -RedirectStandardError "backend_err.log" -NoNewWindow
Start-Sleep -Seconds 8
try { (Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 5).StatusCode } catch { Write-Host "FAIL $($_.Exception.Message)" }
Write-Host "=== err tail ==="; Get-Content backend_err.log -Tail 6
