$ErrorActionPreference = "Continue"
$root = "C:\Users\Administrator\Desktop\my_ai_assistant"
$venv = "$root\backend\.venv\Scripts\python.exe"
$env:PYTHONPATH = $root

Get-ChildItem "$root\backend" -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$ns = netstat -ano | Select-String ':8000\s+.*LISTENING'
$plist = @()
foreach ($line in $ns) { $parts = -split $line; $plist += $parts[-1] }
$plist = $plist | Where-Object { $_ -match '^\d+$' } | Sort-Object -Unique
Write-Host ("listening PIDs on 8000: " + ($plist -join ','))

foreach ($p in $plist) {
    Write-Host ("taskkill /F /T /PID $p")
    taskkill /F /T /PID $p 2>&1 | Out-Null
    Write-Host ("wmic terminate $p")
    wmic process where "ProcessId=$p" call terminate 2>&1 | Out-Null
}
Start-Sleep -Seconds 3

$ns2 = netstat -ano | Select-String ':8000\s+.*LISTENING'
if ($ns2) {
    Write-Host ("STILL LISTENING: " + ($ns2 -join ' | '))
    Write-Host "CANNOT FREE 8000 FROM THIS SESSION; user must restart their backend server."
    exit 2
}
Write-Host "port 8000 free"

$proc = Start-Process -FilePath $venv -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory $root -RedirectStandardOutput "$root\backend_run_8000.log" -RedirectStandardError "$root\backend_err_8000.log" -PassThru -WindowStyle Hidden
Write-Host ("started venv uvicorn PID=" + $proc.Id)

$ready = $false
for ($i = 0; $i -lt 50; $i++) {
    try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction SilentlyContinue; if ($r.StatusCode -eq 200) { $ready = $true; break } } catch {}
    Start-Sleep -Seconds 1
}
if (-not $ready) { Write-Host "8000 NOT READY"; Get-Content "$root\backend_err_8000.log" -Tail 30; exit 1 }
Write-Host "8000 READY (fresh venv)"

& $venv "$root\_regress.py"
