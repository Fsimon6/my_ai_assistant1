$src = 'C:\Program Files\Python310\Lib\site-packages'
$dst = 'C:\Users\Administrator\Desktop\my_ai_assistant\.venv\Lib\site-packages'
# excel-parser 及其依赖(系统 Python310 已装, .venv 离线无法 pip 安装)
$prefixes = @('excel_parser', 'openpyxl', 'et_xmlfile', 'xlrd', 'lxml', 'tiktoken', 'xxhash', 'regex', 'requests')
foreach ($p in $prefixes) {
    $srcDirs = Get-ChildItem $src -Directory -Filter "$p*"
    foreach ($d in $srcDirs) {
        $target = Join-Path $dst $d.Name
        if (Test-Path $target) {
            Write-Host ("SKIP (exists): " + $d.Name)
        } else {
            robocopy $d.FullName $target /E /IS /IT /NFL /NDL /NJH /NJS
            Write-Host ("COPIED: " + $d.Name)
        }
    }
}
Write-Host 'DONE'
