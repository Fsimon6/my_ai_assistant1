$ErrorActionPreference='Stop'
$src='C:\Users\Administrator\Desktop\my_ai_assistant'
$ts=Get-Date -Format 'yyyyMMdd_HHmmss'
$bak="C:\Users\Administrator\Desktop\my_ai_assistant_pre_restore_$ts"
if(Test-Path $bak){ Write-Host "BACKUP_EXISTS:$bak"; exit 1 }
New-Item -ItemType Directory -Path $bak | Out-Null

# frontend/src (exclude node_modules)
robocopy "$src\frontend\src" "$bak\frontend\src" /E /XD node_modules /NFL /NDL /NJH /NJS
# backend source (exclude venv, pycache, data, large runtime)
robocopy "$src\backend" "$bak\backend" /E /XD .venv __pycache__ node_modules data /NFL /NDL /NJH /NJS
# root config files
$rootKeep=@('.env','package.json','requirements.txt','pyproject.toml','README.md','vite.config.ts','tsconfig.json','tsconfig.node.json','index.html','.gitignore','nginx.conf')
foreach($f in $rootKeep){ if(Test-Path "$src\$f"){ Copy-Item "$src\$f" "$bak\$f" -Force } }

Write-Host "BACKUP_DONE:$bak"

# SHA256 of current mixed-state files + chosen history snapshots
$files=@(
 "$src\frontend\src\views\characters\Characters.vue",
 "$src\frontend\src\views\knowledge\KnowledgeBase.vue",
 "$src\frontend\src\views\chat\Chat.vue",
 "$src\frontend\src\composables\useStreamingChat.ts",
 "$src\backend\api\v1\rag.py",
 "$src\backend\api\v1\characters.py",
 "$src\backend\services\rag_service.py",
 "$src\backend\services\table_representation.py",
 "$src\backend\services\document_service.py",
 "$src\backend\main.py",
 "C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History\-7ab8bda0\wjHV.vue",
 "C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History\b5589c3\5td1.vue",
 "C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History\23bcfb54\bzcJ.vue",
 "C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History\-1d34c781\Fl47.ts",
 "C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History\-4365aa73\vs9e.py",
 "C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History\-4365aa73\uIQD.py"
)
foreach($f in $files){ if(Test-Path $f){ $h=(Get-FileHash $f -Algorithm SHA256).Hash; Write-Host ('SHA {0}  {1}' -f $h, $f) } else { Write-Host ('MISSING {0}' -f $f) } }
