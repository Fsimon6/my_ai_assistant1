$ErrorActionPreference = "Continue"
$root = "C:\Users\Administrator\Desktop\my_ai_assistant"
$venv = "$root\backend\.venv\Scripts\python.exe"
$env:PYTHONPATH = $root

Get-ChildItem "$root\backend" -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 获取真实监听 PID
$ns = netstat -ano | Select-String ':8000\s+.*LISTENING'
$pids = @()
foreach ($line in $ns) {
    $parts = -split $line
    $pids += $parts[-1]
}
$pids = $pids | Where-Object { $_ -match '^\d+$' } | Sort-Object -Unique
Write-Host ("listening PIDs on 8000: " + ($pids -join ','))

foreach ($pid in $pids) {
    Write-Host ("taskkill /F /T /PID $pid")
    taskkill /F /T /PID $pid 2>&1 | Out-Null
    Write-Host ("wmic terminate $pid")
    wmic process where "ProcessId=$pid" call terminate 2>&1 | Out-Null
}
Start-Sleep -Seconds 3

$ns2 = netstat -ano | Select-String ':8000\s+.*LISTENING'
if ($ns2) { Write-Host ("STILL LISTENING: " + ($ns2 -join ' | ')) } else { Write-Host "port 8000 free" }

$proc = Start-Process -FilePath $venv -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory $root -RedirectStandardOutput "$root\backend_run_8000.log" -RedirectStandardError "$root\backend_err_8000.log" -PassThru -WindowStyle Hidden
Write-Host ("started venv uvicorn PID=" + $proc.Id)

$ready = $false
for ($i = 0; $i -lt 50; $i++) {
    try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction SilentlyContinue; if ($r.StatusCode -eq 200) { $ready = $true; break } } catch {}
    Start-Sleep -Seconds 1
}
if (-not $ready) { Write-Host "8000 NOT READY"; Get-Content "$root\backend_err_8000.log" -Tail 30; exit 1 }
Write-Host "8000 READY (fresh venv)"

# 校验没有绑定冲突日志
$grep = Get-Content "$root\backend_err_8000.log" -Tail 40 | Select-String -Pattern "Address already in use|Error|Traceback" | Select-Object -First 10
if ($grep) { Write-Host "POSSIBLE STARTUP ISSUE:"; $grep | ForEach-Object { Write-Host ("  " + $_) } }

# 跑回归
& $venv "$root\_regress.py"
