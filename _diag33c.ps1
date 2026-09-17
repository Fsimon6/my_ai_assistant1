# 关键：清空 PYTHONPATH，模拟 supervisor 的真实启动环境
$env:PYTHONPATH = ""
Set-Location C:\Users\Administrator
Write-Host "=== py310 import backend (neutral cwd, NO PYTHONPATH) ==="
& "C:\Program Files\Python310\python.exe" -c "import backend; print('RESOLVE:', backend.__file__)" 2>&1
Write-Host "=== pip show backend ==="
& "C:\Program Files\Python310\python.exe" -m pip show backend 2>&1
Write-Host "=== site-packages listing for backend* ==="
Get-ChildItem "C:\Program Files\Python310\Lib\site-packages" -Filter "backend*" 2>&1 | Select-Object Name, FullName
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_diag33c.ps1 -Force -ErrorAction SilentlyContinue
