$root = 'C:\Users\Administrator\Desktop\my_ai_assistant'
# free ports if occupied
foreach ($port in @(5173, 8000)) {
  try {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
      Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
  } catch {}
}
Start-Sleep -Seconds 1
Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm run dev -- --port 5173 --host 127.0.0.1" -RedirectStandardOutput "$root\frontend\fe_e2e.log" -RedirectStandardError "$root\frontend\fe_e2e_err.log" -WorkingDirectory "$root\frontend"
Start-Process -FilePath "$root\backend\.venv\Scripts\python.exe" -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port 8000" -RedirectStandardOutput "$root\backend_e2e.log" -RedirectStandardError "$root\backend_e2e_err.log" -WorkingDirectory "$root"
