$ErrorActionPreference = "Continue"
$venv = "C:\Users\Administrator\Desktop\my_ai_assistant\backend\.venv\Scripts\python.exe"
$root = "C:\Users\Administrator\Desktop\my_ai_assistant"

# 停止占用 8000 的进程，释放 Chroma SQLite 锁
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listeners) {
    Stop-Process -Id $l.OwningProcess -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1
Write-Host "stopped 8000 (if any)"

& $venv "$root\_inspect_chroma.py"
