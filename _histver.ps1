$h='C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History'
$folders=@('-2f434ac','-7ab8bda0','b5589c3','23bcfb54','-1d34c781','-4365aa73','6d312e5f')
foreach($fd in $folders){
  $p=Join-Path $h $fd
  if(-not (Test-Path $p)){ continue }
  Write-Host "=== $fd ==="
  Get-ChildItem $p -File | Sort-Object LastWriteTime | ForEach-Object {
    $c=(Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue)
    $marker='?'
    if($c -match '系统默认模型'){ $marker='NEW-char' }
    elseif($c -match '文心一言'){ $marker='OLD-char' }
    if($c -match '功能开发中'){ $marker='OLD-kb' }
    elseif($c -match 'ragApi\.getDocuments|getDocumentChunks|previewDocument\('){ $marker='NEW-kb' }
    if($c -match 'query-with-history'){ $marker='NEW-chat' }
    elseif($c -match 'ragApi\.queryDocument\(message, false\)'){ $marker='OLD-chat' }
    if($c -match 'buildBody'){ $marker='NEW-usc' }
    if($c -match 'get_document_chunks'){ $marker='NEW-rag' }
    if(($c -match "allowed_types = \['.pdf', '.txt', '.docx', '.md'\]") -and ($c -notmatch 'ALLOWED_EXT')){ $marker='OLD-rag' }
    if($c -match 'speak/stream|StreamingResponse'){ $marker += '+stream' }
    Write-Host ('  {0} | {1} | {2}' -f $_.Name, $_.LastWriteTime.ToString('MM/dd HH:mm'), $marker)
  }
}
