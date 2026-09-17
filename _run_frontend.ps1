$ErrorActionPreference='Stop'
$src='C:\Users\Administrator\Desktop\my_ai_assistant'
Set-Location "$src\frontend"
npm run dev *>> "$src\logs\frontend_restore.log"
