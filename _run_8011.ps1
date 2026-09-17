$ErrorActionPreference = "Continue"
$root = "C:\Users\Administrator\Desktop\my_ai_assistant"
$venv = "$root\backend\.venv\Scripts\python.exe"
$env:PYTHONPATH = $root
$port = "8011"

Get-ChildItem "$root\backend" -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$proc = Start-Process -FilePath $venv -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port $port" -WorkingDirectory $root -RedirectStandardOutput "$root\backend_run_$port.log" -RedirectStandardError "$root\backend_err_$port.log" -PassThru -WindowStyle Hidden
for ($i = 0; $i -lt 45; $i++) {
    try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2 -ErrorAction SilentlyContinue; if ($r.StatusCode -eq 200) { break } } catch {}
    Start-Sleep -Seconds 1
}
Write-Host ("8011 READY (PID " + $proc.Id + ")")

$env:REG_PORT = $port
& $venv "$root\_regress.py"
Write-Host "8011 left running"
