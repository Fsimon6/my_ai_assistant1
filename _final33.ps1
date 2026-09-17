$env:PYTHONPATH = 'C:\Users\Administrator\Desktop\my_ai_assistant'
Set-Location C:\Users\Administrator\Desktop\my_ai_assistant
# 关掉 8005 测试服务器
$p5 = Get-NetTCPConnection -LocalPort 8005 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
if ($p5) { taskkill /PID $p5 /F 2>&1 | Out-Null }
# 杀光所有 uvicorn（含 Python310 reloader/worker）
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn*' } | ForEach-Object { taskkill /PID $_.ProcessId /F 2>&1 | Out-Null }
Start-Sleep -Seconds 1
# 用 venv 起全新后端到 8000（加载当前磁盘代码）
Start-Process -FilePath '.\backend\.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000','--reload' -WorkingDirectory 'C:\Users\Administrator\Desktop\my_ai_assistant' -RedirectStandardOutput 'backend_run.log' -RedirectStandardError 'backend_err.log' -NoNewWindow
Start-Sleep -Seconds 10
$p = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
if ($p) { $pi = Get-CimInstance Win32_Process -Filter "ProcessId=$p"; Write-Host "LISTENER PID=$p EXE=$($pi.ExecutablePath)" } else { Write-Host "No listener on 8000" }
try { (Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 5).StatusCode } catch { Write-Host "HEALTH FAIL $($_.Exception.Message)" }
Remove-Item _verify_crud33c.py, backend_run_8005.log, backend_err_8005.log -Force -ErrorAction SilentlyContinue
Remove-Item _final33.ps1 -Force -ErrorAction SilentlyContinue
