# 彻底杀光所有 uvicorn / backend 相关进程（含 reloader 与 worker）
$p = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
if ($p) { taskkill /PID $p /F 2>&1 | Out-Null }
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*uvicorn*' -or $_.CommandLine -like '*backend.main*' -or $_.CommandLine -like '*multiprocessing*' } | ForEach-Object { taskkill /PID $_.ProcessId /F 2>&1 | Out-Null }
Start-Sleep -Seconds 2
# 用 venv 单进程（无 --reload）直接监听 8000，确保加载当前磁盘代码
$env:PYTHONPATH = 'C:\Users\Administrator\Desktop\my_ai_assistant'
Set-Location C:\Users\Administrator\Desktop\my_ai_assistant
Start-Process -FilePath '.\backend\.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory 'C:\Users\Administrator\Desktop\my_ai_assistant' -RedirectStandardOutput 'backend_run.log' -RedirectStandardError 'backend_err.log' -NoNewWindow
Start-Sleep -Seconds 8
$p = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess
if ($p) { $pi = Get-CimInstance Win32_Process -Filter "ProcessId=$p"; Write-Host "LISTENER PID=$p EXE=$($pi.ExecutablePath)" } else { Write-Host "No listener on 8000" }
try { (Invoke-WebRequest -Uri http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 5).StatusCode } catch { Write-Host "HEALTH FAIL $($_.Exception.Message)" }
Remove-Item _restart33e.ps1 -Force -ErrorAction SilentlyContinue
