$ErrorActionPreference = "Continue"
Write-Host "=== internet (baidu) ==="
try { $r = Invoke-WebRequest -Uri 'https://www.baidu.com' -TimeoutSec 6 -ErrorAction Stop; Write-Host ("baidu reachable: " + $r.StatusCode) } catch { Write-Host ("baidu NOT reachable: " + $_.Exception.Message) }

Write-Host "=== ollama installed? ==="
$ol = Get-Command ollama -ErrorAction SilentlyContinue
if ($ol) { Write-Host ("ollama FOUND: " + $ol.Source) } else { Write-Host "ollama NOT installed" }

Write-Host "=== sentence-transformers + torch in venv ==="
$venv = "C:\Users\Administrator\Desktop\my_ai_assistant\backend\.venv\Scripts\python.exe"
& $venv -c "import importlib.util as u; print('sentence-transformers:', 'OK' if u.find_spec('sentence_transformers') else 'MISSING'); print('torch:', 'OK' if u.find_spec('torch') else 'MISSING')"

Write-Host "=== local embedding model cache ==="
$cands = @(
  "$env:USERPROFILE\.cache\torch\sentence_transformers",
  "$env:USERPROFILE\.cache\huggingface\hub",
  "C:\Users\Administrator\Desktop\my_ai_assistant\models"
)
foreach ($c in $cands) { if (Test-Path $c) { Write-Host ("EXISTS: " + $c) } else { Write-Host ("absent: " + $c) } }
