$ErrorActionPreference = "Continue"
Write-Host ("LLM_PROVIDER   = " + $env:LLM_PROVIDER)
Write-Host ("LLM_BASE_URL   = " + $env:LLM_BASE_URL)
Write-Host ("LLM_MODEL      = " + $env:LLM_MODEL)
Write-Host ("API_KEY set?   = " + (if ($env:API_KEY) { "YES(len $($env:API_KEY.Length))" } else { "NO" }))
Write-Host ("EMBEDDING_MODEL = " + $env:EMBEDDING_MODEL)
Write-Host ("EMBEDDING_PROVIDER = " + $env:EMBEDDING_PROVIDER)
Write-Host ("--- connectivity test (api.openai.com) ---")
try {
    $r = Invoke-WebRequest -Uri 'https://api.openai.com/v1/models' -TimeoutSec 6 -ErrorAction Stop
    Write-Host ("openai reachable, status=" + $r.StatusCode)
} catch {
    Write-Host ("openai NOT reachable: " + $_.Exception.Message)
}
