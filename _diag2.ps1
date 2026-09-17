$ErrorActionPreference = "Continue"
Write-Host "=== health probe ==="
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 3 -ErrorAction Stop
    Write-Host ("RESPONSE " + $r.StatusCode)
} catch {
    Write-Host ("NO RESPONSE: " + $_.Exception.Message)
}

Write-Host "=== WMI lookup PID 20788 ==="
$p = Get-CimInstance Win32_Process -Filter "ProcessId=20788" -ErrorAction SilentlyContinue
if ($p) {
    $owner = $p.GetOwner()
    Write-Host ("FOUND: Name=" + $p.Name + " Session=" + $p.SessionId + " Owner=" + $owner.Domain + "\" + $owner.User)
    Write-Host ("CmdLine=" + $p.CommandLine)
} else {
    Write-Host "WMI: 20788 NOT FOUND"
}

Write-Host "=== all python.exe with cmdline ==="
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ForEach-Object {
    $o = $_.GetOwner()
    Write-Host ("PID=" + $_.ProcessId + " Session=" + $_.SessionId + " Owner=" + $o.Domain + "\" + $o.User + " Cmd=" + ($_.CommandLine))
}
