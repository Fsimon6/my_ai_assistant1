$ErrorActionPreference = "Continue"
$root = "C:\Users\Administrator\Desktop\my_ai_assistant"
$venv = "$root\backend\.venv\Scripts\python.exe"
$env:PYTHONPATH = $root

Write-Host "=== settings loaded from .env? ==="
& $venv -c "from backend.config.settings import settings; print('LLM_PROVIDER =', settings.LLM_PROVIDER); print('LLM_BASE_URL  =', settings.LLM_BASE_URL); print('EMBEDDING_MODEL=', settings.EMBEDDING_MODEL); print('API_KEY set  =', bool(settings.API_KEY))"

Write-Host "=== dashscope connectivity ==="
try {
    $r = Invoke-WebRequest -Uri 'https://dashscope.aliyuncs.com/compatible-mode/v1/models' -TimeoutSec 6 -ErrorAction Stop
    Write-Host ("dashscope reachable: " + $r.StatusCode)
} catch {
    Write-Host ("dashscope NOT reachable: " + $_.Exception.Message)
}
