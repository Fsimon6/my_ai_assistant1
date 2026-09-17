$ErrorActionPreference = "Continue"
$src = "C:\Users\Administrator\_punkt_tab.zip"
$nd = "C:\Users\Administrator\nltk_data"
New-Item -ItemType Directory -Force -Path $nd | Out-Null
Expand-Archive -Path $src -DestinationPath $nd -Force
Write-Host "extracted"
# 验证关键文件存在
$check = Join-Path $nd "tokenizers/punkt_tab/english/__init__.py"
if (Test-Path $check) { Write-Host "punkt_tab present at $check" } else {
    Write-Host "punkt_tab NOT found; listing:"; Get-ChildItem $nd -Recurse -Depth 3 | Select-Object -First 20 FullName
}
