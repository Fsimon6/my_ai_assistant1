$ErrorActionPreference = "Continue"
$root = "C:\Users\Administrator\Desktop\my_ai_assistant"
$py = "C:\Program Files\Python310\python.exe"
$env:PYTHONPATH = $root

# 清 pyc
Get-ChildItem "$root\backend" -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "cleared pyc"

# 释放 8000
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listeners) { Stop-Process -Id $l.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*uvicorn*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# 直接用系统 python 启动（其全局 site-packages 已含 fastapi/chroma/langchain/unstructured/docx2txt/markdown）
$proc = Start-Process -FilePath $py -ArgumentList "-m uvicorn backend.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory $root -RedirectStandardOutput "$root\backend_run_8000.log" -RedirectStandardError "$root\backend_err_8000.log" -PassThru -WindowStyle Hidden

$ready = $false
for ($i = 0; $i -lt 45; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    Write-Host "8000 NOT READY"
    Get-Content "$root\backend_err_8000.log" -Tail 30
    exit 1
}
Write-Host "8000 READY (system python, all deps global)"

try {
    & $py "$root\_regress.py"
    $code = $LASTEXITCODE
} finally {
    Write-Host "8000 left running for user"
}
exit $code
