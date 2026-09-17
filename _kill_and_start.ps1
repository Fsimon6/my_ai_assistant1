$ErrorActionPreference = "Continue"
$root = "C:\Users\Administrator\Desktop\my_ai_assistant"
$py = "C:\Program Files\Python310\python.exe"
$env:PYTHONPATH = $root

Get-ChildItem "$root\backend" -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listeners) {
    Write-Host ("taskkill /F /T /PID " + $l.OwningProcess)
    taskkill /F /T /PID $l.OwningProcess 2>&1 | Out-Null
}
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    $cli = $_.CommandLine
    ($cli -like "*my_ai_assistant*") -or ($cli -like "*uvicorn*") -or ($cli -like "*backend.main*") -or ($cli -like "*spawn_main*")
} | ForEach-Object { taskkill /F /T /PID $_.Id 2>&1 | Out-Null }

Start-Sleep -Seconds 3

$still = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($still) {
    Write-Host ("STILL LISTENING by " + ($still.OwningProcess -join ','))
    foreach ($s in $still) { taskkill /F /T /PID $s.OwningProcess 2>&1 | Out-Null }
    Start-Sleep -Seconds 2
} else {
    Write-Host "port 8000 free"
}

$proc = Start-Process -FilePath $py -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory $root -RedirectStandardOutput "$root\backend_run_8000.log" -RedirectStandardError "$root\backend_err_8000.log" -PassThru -WindowStyle Hidden
Write-Host ("started new uvicorn PID=" + $proc.Id)

$ready = $false
for ($i = 0; $i -lt 45; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $ready) { Write-Host "8000 NOT READY"; Get-Content "$root\backend_err_8000.log" -Tail 30; exit 1 }
Write-Host "8000 READY (fresh process)"
exit 0
