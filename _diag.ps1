$root = "C:\Users\Administrator\Desktop\my_ai_assistant"
$site = "$root\backend\.venv\Lib\site-packages"

Write-Host "=== venv site-packages 物理存在性 ==="
"unstructured", "docx2txt", "markdown" | ForEach-Object {
    $p = Join-Path $site $_
    if (Test-Path $p) { Write-Host "EXISTS: $p" } else { Write-Host "MISSING: $p" }
}

Write-Host "`n=== 监听 8000 的进程 ==="
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($l in $listeners) {
    $proc = Get-Process -Id $l.OwningProcess -ErrorAction SilentlyContinue
    Write-Host ("PID={0} Name={1} Path={2}" -f $l.OwningProcess, $proc.Name, $proc.Path)
    $cli = (Get-CimInstance Win32_Process -Filter "ProcessId=$($l.OwningProcess)" -ErrorAction SilentlyContinue).CommandLine
    Write-Host ("  CMD={0}" -f $cli)
}

Write-Host "`n=== 所有 python 进程 ==="
Get-Process -Name python -ErrorAction SilentlyContinue | ForEach-Object {
    $cli = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
    Write-Host ("PID={0} Path={1}" -f $_.Id, $_.Path)
    Write-Host ("  CMD={0}" -f $cli)
}
