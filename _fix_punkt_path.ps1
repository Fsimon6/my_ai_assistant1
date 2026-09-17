$ErrorActionPreference = "Continue"
$nd = "C:\Users\Administrator\nltk_data"
$tok = Join-Path $nd "tokenizers"
New-Item -ItemType Directory -Force -Path $tok | Out-Null
if (Test-Path (Join-Path $nd "punkt_tab")) {
    Move-Item -Path (Join-Path $nd "punkt_tab") -Destination (Join-Path $tok "punkt_tab") -Force
    Write-Host "moved to tokenizers/punkt_tab"
}
$check = Join-Path $tok "punkt_tab/english/__init__.py"
if (Test-Path $check) { Write-Host "OK: $check" } else { Write-Host "still missing english" }
