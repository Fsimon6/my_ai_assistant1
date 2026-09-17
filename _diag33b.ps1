Write-Host "=== Python310 backend path ==="
& "C:\Program Files\Python310\python.exe" -c "import backend; print(backend.__file__)" 2>&1
Write-Host "=== venv backend path ==="
& ".\backend\.venv\Scripts\python.exe" -c "import backend; print(backend.__file__)" 2>&1
Write-Host "=== direct cascade test (venv) ==="
& ".\backend\.venv\Scripts\python.exe" "_direct33.py" 2>&1
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_direct33.py -Force -ErrorAction SilentlyContinue
Remove-Item C:\Users\Administrator\Desktop\my_ai_assistant\_diag33b.ps1 -Force -ErrorAction SilentlyContinue
