$ErrorActionPreference='Stop'
$src='C:\Users\Administrator\Desktop\my_ai_assistant'
$H='C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History'
Copy-Item "$H\d28ff09\XJAA.py" "$src\backend\services\rag_service.py" -Force
Copy-Item "$H\-350a9042\9prr.py" "$src\backend\services\vector_service.py" -Force
Write-Host "RESTORE_BACK2_DONE"
foreach($f in @("$src\backend\services\rag_service.py","$src\backend\services\vector_service.py")){
  Write-Host ('SHA {0}  {1}' -f (Get-FileHash $f -Algorithm SHA256).Hash, $f)
}
