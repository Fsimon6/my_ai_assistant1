$h='C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History'
$targets=@('Characters.vue','KnowledgeBase.vue','Chat.vue','api/v1/rag.py','api/v1/characters.py','useStreamingChat.ts','rag_service.py','document_service.py','table_representation.py','main.py')
Get-ChildItem $h -Directory | ForEach-Object {
  $f=Get-ChildItem $_.FullName -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if(-not $f){return}
  $hit=$false; $name=''
  if($f.Extension -eq '.json'){
    $txt=Get-Content $f.FullName -Raw -ErrorAction SilentlyContinue
    foreach($t in $targets){ if($txt -match [regex]::Escape($t)){ $hit=$true; $name=$t; break } }
  } else {
    $txt=(Get-Content $f.FullName -TotalCount 8 -ErrorAction SilentlyContinue) -join "`n"
    if($txt -match "prefix='/api/v1/rag'"){ $hit=$true; $name='api/v1/rag.py' }
    elseif($txt -match "prefix='/api/v1/characters'"){ $hit=$true; $name='api/v1/characters.py' }
    elseif($txt -match '系统默认模型|文心一言'){ $hit=$true; $name='Characters.vue' }
    elseif($txt -match 'KnowledgeBase|previewDocument|loadDocuments'){ $hit=$true; $name='KnowledgeBase.vue' }
    elseif($txt -match 'useStreamingChat|query-with-history'){ $hit=$true; $name='Chat.vue' }
    elseif($txt -match 'TABLE_EXTENSIONS|table_representation'){ $hit=$true; $name='table_representation.py' }
    elseif($txt -match 'process_and_store_document|rag_service'){ $hit=$true; $name='rag_service.py' }
    elseif($txt -match 'ALLOWED_EXT|save_uploaded_file'){ $hit=$true; $name='document_service.py' }
    elseif($txt -match 'FastAPI|create_app|include_router'){ $hit=$true; $name='main.py' }
  }
  if($hit){ Write-Host ('{0} | {1} | {2} | {3}' -f $_.Name, $f.LastWriteTime.ToString('MM/dd HH:mm'), $f.Extension, $name) }
}
