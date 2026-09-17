$ErrorActionPreference = "Continue"
$u = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt_tab.zip"
$dst = "C:\Users\Administrator\_punkt_tab.zip"
try {
    Invoke-WebRequest -Uri $u -OutFile $dst -TimeoutSec 40
    Write-Host "DOWNLOAD OK -> $dst"
} catch {
    Write-Host ("DOWNLOAD FAIL: " + $_.Exception.Message)
}
