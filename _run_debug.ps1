$ErrorActionPreference = "Continue"
$root = "C:\Users\Administrator\Desktop\my_ai_assistant"
$py = "C:\Program Files\Python310\python.exe"
$env:PYTHONPATH = $root

# 清 pyc
Get-ChildItem "$root\backend" -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 子弹式清理：所有与本项目相关的 python 进程 + 8000 监听 + 任何 spawn 孤儿
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listeners) { Stop-Process -Id $l.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object {
    $cli = $_.CommandLine
    ($cli -like "*my_ai_assistant*") -or ($cli -like "*uvicorn*") -or ($cli -like "*backend.main*") -or ($cli -like "*spawn_main*") -or ($cli -like "*multiprocessing*")
} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

# 确认端口已释放
$still = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($still) { Write-Host "WARN: 8000 still listening by $($still.OwningProcess)" }

$proc = Start-Process -FilePath $py -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory $root -RedirectStandardOutput "$root\backend_run_8000.log" -RedirectStandardError "$root\backend_err_8000.log" -PassThru -WindowStyle Hidden

$ready = $false
for ($i = 0; $i -lt 45; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $ready) { Write-Host "8000 NOT READY"; Get-Content "$root\backend_err_8000.log" -Tail 30; exit 1 }
Write-Host "8000 READY"

# 匿名访问 debug 路由（无鉴权？该路由未加 Depends，可直接访问）
$r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/rag/_debug_sys" -TimeoutSec 10
Write-Host $r.Content
