$ErrorActionPreference = 'Continue'
$base = 'http://127.0.0.1:8000'
$ROOT = 'C:\Users\Administrator\Desktop\my_ai_assistant'
$user = 'envtest'
$pw = 'EnvTest123'

function GetToken {
    $lb = @{username = $user; password = $pw } | ConvertTo-Json
    try {
        $r = Invoke-RestMethod -Uri "$base/api/v1/auth/login" -Method Post -ContentType 'application/json' -Body $lb -ErrorAction Stop
        return $r.data.access_token
    }
    catch {
        $rb = @{username = $user; password = $pw; email = 'envtest@test.local' } | ConvertTo-Json
        try { Invoke-RestMethod -Uri "$base/api/v1/auth/register" -Method Post -ContentType 'application/json' -Body $rb -ErrorAction Stop | Out-Null }
        catch { Write-Host ("REG_ERR " + $_.Exception.Message) }
        $r = Invoke-RestMethod -Uri "$base/api/v1/auth/login" -Method Post -ContentType 'application/json' -Body $lb -ErrorAction Stop
        return $r.data.access_token
    }
}
$tk = GetToken
Write-Host ("TOKEN_LEN=" + $tk.Length)

$script:docIds = @()

function Upload($relpath, $label) {
    $path = Join-Path $ROOT $relpath
    $tmp = Join-Path $ROOT ("_up_" + $label + ".json")
    $a = @('-s', '-m', '120', '-X', 'POST', "$base/api/v1/rag/upload", '-H', "Authorization: Bearer $tk", '-F', "file=@$path")
    $code = & curl.exe @a -o $tmp -w '%{http_code}'
    $body = Get-Content $tmp -Raw
    Write-Host ("=== UPLOAD $label : HTTP=$code ===")
    Write-Host $body
    try {
        $jo = $body | ConvertFrom-Json -ErrorAction SilentlyContinue
        if ($jo -and $jo.document_id) { $script:docIds += $jo.document_id }
    }
    catch {}
    return @{ code = $code; body = $body }
}

# ---------- 主测试：真实 XLSX（10 列订单表）----------
$x = Upload '_test_order.xlsx' 'XLSX'
$xo = $x.body | ConvertFrom-Json -ErrorAction SilentlyContinue
$docId = if ($xo -and $xo.document_id) { $xo.document_id } else { $null }
$totalChunks = if ($xo -and $xo.total_chunks) { $xo.total_chunks } else { $null }
Write-Host ("DOC_ID=$docId TOTAL_CHUNKS=$totalChunks")

if ($docId) {
    # 预览：Unified Table Representation
    try {
        $ts = Invoke-RestMethod -Uri "$base/api/v1/rag/documents/$docId/table-structure" -Method Get -Headers @{ Authorization = "Bearer $tk" } -ErrorAction Stop
        $ts | ConvertTo-Json -Depth 12 | Out-File (Join-Path $ROOT '_ts.json')
        foreach ($sh in $ts.structure.workbook.sheets) {
            foreach ($t in $sh.tables) {
                Write-Host ("TABLE sheet=$($sh.sheet_name) cols=$($t.col_count) rows=$($t.row_count) range=$($t.range)")
                $cols = $t.columns | ForEach-Object { $_.col_letter + '=' + $_.technical_name }
                Write-Host ("COLUMNS_ALL: " + ($cols -join ', '))
                Write-Host ("FIRST_COL=$($t.columns[0].col_letter)/$($t.columns[0].technical_name) LAST_COL=$($t.columns[-1].col_letter)/$($t.columns[-1].technical_name)")
            }
        }
    }
    catch { Write-Host ("TS_ERR " + $_.Exception.Message) }

    # RAG 查询（基于真实表头字段）
    $q = '这个表格一共有多少列？表头都有哪些字段？'
    $qb = @{ query = $q; stream = $false; context_count = 3 } | ConvertTo-Json
    try {
        $qr = Invoke-RestMethod -Uri "$base/api/v1/rag/query" -Method Post -ContentType 'application/json' -Body $qb -Headers @{ Authorization = "Bearer $tk" } -TimeoutSec 120 -ErrorAction Stop
        Write-Host ("RAG_QUERY=$q")
        Write-Host ("RAG_ANSWER=" + $qr.response)
    }
    catch { Write-Host ("RAG_ERR " + $_.Exception.Message) }
}

# ---------- 其它表格格式：CSV / TSV ----------
Upload '_test.csv' 'CSV'
Upload '_test.tsv' 'TSV'

# ---------- 旧格式：TXT / MD / DOCX ----------
Upload '_test.txt' 'TXT'
Upload '_test.md' 'MD'
Upload '_test.docx' 'DOCX'

# ---------- XLS：校验白名单放行（占位文件，真实解析需真实夹具）----------
$xlsRes = Upload '_test.xls' 'XLS'
$xlscode = [int]$xlsRes.code
$xlsbody = $xlsRes.body
if ($xlsbody -match '不支持的文件类型') {
    Write-Host ("XLS_GATE=REJECTED (whitelist bug!)")
}
else {
    Write-Host ("XLS_GATE=ALLOWED (not rejected by extension whitelist; processing result: HTTP=$xlscode)")
}

# ---------- Chroma 文档列表（完整性 / 用户隔离）----------
try {
    $docs = Invoke-RestMethod -Uri "$base/api/v1/rag/documents" -Method Get -Headers @{ Authorization = "Bearer $tk" } -ErrorAction Stop
    $docs | ConvertTo-Json -Depth 6 | Out-File (Join-Path $ROOT '_docs.json')
    Write-Host ("DOCS_COUNT=" + $docs.documents.Count)
    $our = $docs.documents | Where-Object { $_.document_id -eq $docId }
    Write-Host ("OUR_DOC_META=" + ($our | ConvertTo-Json -Compress))
}
catch { Write-Host ("DOCS_ERR " + $_.Exception.Message) }

# ---------- 清理：删除测试文档（Chroma）----------
if ($script:docIds.Count -gt 0) {
    $db = @{ document_ids = $script:docIds } | ConvertTo-Json
    try {
        $dr = Invoke-RestMethod -Uri "$base/api/v1/rag/documents" -Method Delete -ContentType 'application/json' -Body $db -Headers @{ Authorization = "Bearer $tk" } -ErrorAction Stop
        Write-Host ("CLEANED_DOCS deleted_count=" + $dr.deleted_count)
    }
    catch { Write-Host ("CLEAN_ERR " + $_.Exception.Message) }
    # 清理 table_originals 下本次测试产生的原始/representation 文件
    foreach ($id in $script:docIds) {
        Remove-Item (Join-Path $ROOT "data/table_originals/$id.xlsx") -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $ROOT "data/table_originals/$id.json") -ErrorAction SilentlyContinue
    }
    Write-Host "CLEANED_TABLE_ORIGINALS"
}

Write-Host "E2E_DONE"
