$ErrorActionPreference = "Continue"
$nd = "C:\Users\Administrator\nltk_data"
$tg = Join-Path $nd "taggers"
New-Item -ItemType Directory -Force -Path $tg | Out-Null
foreach ($d in @("averaged_perceptron_tagger_eng", "averaged_perceptron_tagger")) {
    $src = Join-Path $nd $d
    if (Test-Path $src) {
        Move-Item -Path $src -Destination (Join-Path $tg $d) -Force
        Write-Host ("moved " + $d)
    }
}
Write-Host "taggers:"; Get-ChildItem $tg | Select-Object Name
