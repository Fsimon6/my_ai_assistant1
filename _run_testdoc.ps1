$ErrorActionPreference = "Continue"
$venv = "C:\Users\Administrator\Desktop\my_ai_assistant\backend\.venv\Scripts\python.exe"
$root = "C:\Users\Administrator\Desktop\my_ai_assistant"
$env:PYTHONPATH = $root

# 清除陈旧 pyc，确保加载磁盘最新代码
Get-ChildItem "$root\backend" -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "cleared pyc"

& $venv "$root\_test_getdoc.py"
