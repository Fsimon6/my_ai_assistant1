$ErrorActionPreference = "Continue"
$base = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages"
$nd = "C:\Users\Administrator\nltk_data"
New-Item -ItemType Directory -Force -Path (Join-Path $nd "taggers") | Out-Null

$items = @(
    "taggers/averaged_perceptron_tagger_eng.zip",
    "taggers/averaged_perceptron_tagger.zip"
)
foreach ($it in $items) {
    $name = Split-Path $it -Leaf
    $zip = "C:\Users\Administrator\_" + $name
    try {
        Invoke-WebRequest -Uri ($base + "/" + $it) -OutFile $zip -TimeoutSec 60
        Expand-Archive -Path $zip -DestinationPath $nd -Force
        Write-Host ("OK: " + $name)
    } catch {
        Write-Host ("FAIL: " + $name + " -> " + $_.Exception.Message)
    }
}
Write-Host "taggers dir:"; Get-ChildItem (Join-Path $nd "taggers") | Select-Object Name
