$src='C:\Users\Administrator\Desktop\my_ai_assistant'
Set-Location $src
$env:PYTHONPATH=$src
& "$src\.venv\Scripts\Activate.ps1"
Get-ChildItem backend -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
uvicorn backend.main:app --port 8005
