$base='http://127.0.0.1:8005'
Write-Host "=== OPENAPI /documents methods ==="
$r=Invoke-RestMethod -Uri "$base/openapi.json" -TimeoutSec 8
$r.paths.PSObject.Properties | Where-Object { $_.Name -like '*rag/documents*' -or $_.Name -like '*rag/query*' -or $_.Name -like '*rag/upload*' } | ForEach-Object { $_.Name + ' => ' + ($_.Value.PSObject.Properties.Name -join ',') }

$u='restore_verify_' + (Get-Date -Format 'HHmmss'); $pw='restore123'
$reg=@{username=$u;email=($u+'@example.com');password=$pw}|ConvertTo-Json
try { Invoke-RestMethod -Uri "$base/api/v1/auth/register" -Method Post -ContentType 'application/json' -Body $reg | Out-Null; Write-Host ('REG OK user='+$u) } catch { Write-Host ('REG FAIL '+$_.ErrorDetails.Message) }
$login=@{username=$u;password=$pw}|ConvertTo-Json
$lr=Invoke-RestMethod -Uri "$base/api/v1/auth/login" -Method Post -ContentType 'application/json' -Body $login
$token=if($lr.data){$lr.data.access_token}else{$lr.access_token}
Write-Host ('LOGIN token_len='+$token.Length)
$h=@{Authorization=('Bearer '+$token)}

$d=Invoke-RestMethod -Uri "$base/api/v1/rag/documents" -Headers $h; Write-Host ('DOCS total='+$d.total)
$ci=Invoke-RestMethod -Uri "$base/api/v1/rag/collection-info" -Headers $h; Write-Host ('COLLECTION success='+$ci.success)

# copy xlsx to ASCII temp name to avoid curl arg encoding issues
$srcXlsx='C:\Users\Administrator\Desktop\my_ai_assistant\直邮一店 8.20号订单.xlsx'
$tmpXlsx="$env:TEMP\restore_test.xlsx"
Copy-Item $srcXlsx $tmpXlsx -Force
$upJson=curl.exe -s -F "file=@$tmpXlsx" -H "Authorization: Bearer $token" "$base/api/v1/rag/upload"
Write-Host ('UPLOAD_RAW='+$upJson)
try { $up=$upJson | ConvertFrom-Json; Write-Host ('UPLOAD success='+$up.success+' doc_id='+$up.document_id+' total_chunks='+$up.total_chunks) } catch { Write-Host 'UPLOAD_JSON_PARSE_FAIL'; return }
$docId=$up.document_id

$ts=Invoke-RestMethod -Uri "$base/api/v1/rag/documents/$docId/table-structure" -Headers $h
$json=$ts.structure | ConvertTo-Json -Depth 10
Write-Host ('TABLE_STRUCT success='+$ts.success+' HAS_OrderSKUList='+($json.Contains('OrderSKUList')))
if($ts.structure.sheets){ Write-Host ('SHEETS='+$ts.structure.sheets.Count); if($ts.structure.sheets[0].columns){ Write-Host ('COLUMNS_COUNT='+$ts.structure.sheets[0].columns.Count) } }

$prev=Invoke-RestMethod -Uri "$base/api/v1/rag/documents/$docId" -Headers $h; Write-Host ('PREVIEW success='+$prev.success+' total='+$prev.total)

$del=@{document_ids=@($docId)}|ConvertTo-Json
$dd=Invoke-RestMethod -Uri "$base/api/v1/rag/documents" -Method Delete -ContentType 'application/json' -Headers $h -Body $del; Write-Host ('DELETE success='+$dd.success+' deleted_count='+$dd.deleted_count)
