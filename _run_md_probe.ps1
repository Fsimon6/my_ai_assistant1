$ErrorActionPreference = "Continue"
$root = "C:\Users\Administrator\Desktop\my_ai_assistant"
$py = "C:\Program Files\Python310\python.exe"
$env:PYTHONPATH = $root

# 重启 8000
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listeners) { taskkill /F /T /PID $l.OwningProcess 2>&1 | Out-Null }
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    $cli = $_.CommandLine
    ($cli -like "*my_ai_assistant*") -or ($cli -like "*uvicorn*") -or ($cli -like "*backend.main*") -or ($cli -like "*spawn_main*")
} | ForEach-Object { taskkill /F /T /PID $_.Id 2>&1 | Out-Null }
Start-Sleep -Seconds 3

$proc = Start-Process -FilePath $py -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory $root -RedirectStandardOutput "$root\backend_run_8000.log" -RedirectStandardError "$root\backend_err_8000.log" -PassThru -WindowStyle Hidden
for ($i = 0; $i -lt 45; $i++) {
    try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction SilentlyContinue; if ($r.StatusCode -eq 200) { break } } catch {}
    Start-Sleep -Seconds 1
}
Write-Host ("8000 READY (PID " + $proc.Id + ")")

$tmp = New-Item -ItemType Directory -Force -Path "$root\_probe_tmp" | Select-Object -ExpandProperty FullName
Set-Content -Path (Join-Path $tmp "probe.md") -Value "# 探针`n`n这是 markdown 探针内容。" -Encoding utf8

& $py "$root\_probe_md.py"
Start-Sleep -Seconds 1
Write-Host "=== backend_err_8000.log tail ==="
Get-Content "$root\backend_err_8000.log" -Tail 25
