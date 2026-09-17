# 在 8005 用 venv 起一个全新 uvicorn，隔离 8000 上可能存在的陈旧 worker
$env:PYTHONPATH = "C:\Users\Administrator\Desktop\my_ai_assistant"
Set-Location C:\Users\Administrator\Desktop\my_ai_assistant
# 确保 8005 空闲
$p5 = Get-NetTCPConnection -LocalPort 8005 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
if ($p5) { taskkill /PID $p5 /F 2>&1 | Out-Null; Start-Sleep -Seconds 1 }
Start-Process -FilePath ".\backend\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","backend.main:app","--host","127.0.0.1","--port","8005","--reload" -WorkingDirectory "C:\Users\Administrator\Desktop\my_ai_assistant" -RedirectStandardOutput "backend_run_8005.log" -RedirectStandardError "backend_err_8005.log" -NoNewWindow
Start-Sleep -Seconds 10
try { (Invoke-WebRequest -Uri http://127.0.0.1:8005/health -UseBasicParsing -TimeoutSec 5).StatusCode } catch { Write-Host "FAIL $($_.Exception.Message)" }
Write-Host "=== running CRUD verify on 8005 ==="
& ".\backend\.venv\Scripts\python.exe" "_verify_crud33b.py"
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_verify_crud33b.py -Force -ErrorAction SilentlyContinue
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_restart33c.ps1 -Force -ErrorAction SilentlyContinue
