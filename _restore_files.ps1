$ErrorActionPreference='Stop'
$src='C:\Users\Administrator\Desktop\my_ai_assistant'
$H='C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History'
Copy-Item "$H\-7ab8bda0\wjHV.vue" "$src\frontend\src\views\characters\Characters.vue" -Force
Copy-Item "$H\b5589c3\5td1.vue" "$src\frontend\src\views\knowledge\KnowledgeBase.vue" -Force
Copy-Item "$H\23bcfb54\bzcJ.vue" "$src\frontend\src\views\chat\Chat.vue" -Force
Copy-Item "$H\-1d34c781\Fl47.ts" "$src\frontend\src\composables\useStreamingChat.ts" -Force
Copy-Item "$H\-4365aa73\vs9e.py" "$src\backend\api\v1\rag.py" -Force
Write-Host "RESTORE_DONE"
foreach($f in @(
 "$src\frontend\src\views\characters\Characters.vue",
 "$src\frontend\src\views\knowledge\KnowledgeBase.vue",
 "$src\frontend\src\views\chat\Chat.vue",
 "$src\frontend\src\composables\useStreamingChat.ts",
 "$src\backend\api\v1\rag.py")){
  Write-Host ('SHA {0}  {1}' -f (Get-FileHash $f -Algorithm SHA256).Hash, $f)
}
