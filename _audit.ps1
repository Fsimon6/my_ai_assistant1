$base='C:\Users\Administrator\Desktop\my_ai_assistant'
$copy='C:\Users\Administrator\Desktop\my_ai_assistant - 副本'
$proj='C:\Users\Administrator\Desktop\project'
$files=@(
 'frontend/src/views/characters/Characters.vue',
 'frontend/src/views/chat/Chat.vue',
 'frontend/src/composables/useStreamingChat.ts',
 'frontend/src/views/knowledge/KnowledgeBase.vue',
 'frontend/src/stores/rag.ts',
 'frontend/src/services/api.ts',
 'frontend/src/components/chat/FileUploader.vue',
 'backend/api/v1/rag.py',
 'backend/api/v1/characters.py',
 'backend/services/llm_service.py',
 'backend/services/rag_service.py',
 'backend/services/document_service.py',
 'backend/services/table_representation.py'
)
foreach($f in $files){
  $c=Join-Path $base $f
  $cp=Join-Path $copy $f
  $p=Join-Path $proj $f
  $mc='-'; $mc2='-'; $mp='-'; $hc='-'; $hcp='-'; $hp='-'
  if(Test-Path $c){$mc=(Get-Item $c).LastWriteTime.ToString('MM/dd HH:mm'); $hc=(Get-FileHash $c -Algorithm MD5).Hash.Substring(0,8)}
  if(Test-Path $cp){$mc2=(Get-Item $cp).LastWriteTime.ToString('MM/dd HH:mm'); $hcp=(Get-FileHash $cp -Algorithm MD5).Hash.Substring(0,8)}
  if(Test-Path $p){$mp=(Get-Item $p).LastWriteTime.ToString('MM/dd HH:mm'); $hp=(Get-FileHash $p -Algorithm MD5).Hash.Substring(0,8)}
  Write-Host ('{0} | CUR {1} {2} | COPY {3} {4} | PROJ {5} {6}' -f $f,$mc,$hc,$mc2,$hcp,$mp,$hp)
}
$d=Get-Item (Join-Path $base 'frontend/dist/index.html') -ErrorAction SilentlyContinue
if($d){Write-Host ('DIST index.html: ' + $d.LastWriteTime.ToString('MM/dd HH:mm'))}
$d2=Get-Item (Join-Path $base 'frontend/dist/assets') -ErrorAction SilentlyContinue
if($d2){Write-Host ('DIST assets dir: ' + $d2.LastWriteTime.ToString('MM/dd HH:mm'))}
