$vpy = '.\backend\.venv\Scripts\python.exe'
Write-Host "=== venv import backend resolves to ==="
& $vpy -c "import backend; print('VENV backend __file__:', backend.__file__)" 2>&1
Write-Host "=== venv site-packages backend* entries ==="
Get-ChildItem 'backend\.venv\Lib\site-packages' -Filter 'backend*' 2>&1 | Select-Object Name, FullName, Mode
Write-Host "=== venv site-packages *.pth contents ==="
Get-ChildItem 'backend\.venv\Lib\site-packages\*.pth' -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "$($_.Name):"; Get-Content $_.FullName }
Write-Host "=== venv site-packages *.egg-link contents ==="
Get-ChildItem 'backend\.venv\Lib\site-packages\*.egg-link' -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "$($_.Name):"; Get-Content $_.FullName }
Write-Host "=== is there a project setup/pyproject? ==="
Get-ChildItem 'C:\Users\Administrator\Desktop\my_ai_assistant' -Filter 'setup*' 2>&1 | Select-Object Name
Get-ChildItem 'C:\Users\Administrator\Desktop\my_ai_assistant' -Filter 'pyproject*' 2>&1 | Select-Object Name
Remove-Item _diag33f.ps1 -Force -ErrorAction SilentlyContinue
