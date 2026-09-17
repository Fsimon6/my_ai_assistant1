Start-Sleep -Seconds 6
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/auth/register" -Method POST -ContentType 'application/json' -Body '{"email":"rr1@example.com","username":"rruser123","password":"pass123456","full_name":"RR"}' -UseBasicParsing -TimeoutSec 10
    Write-Host ("REG -> " + [int]$r.StatusCode + " " + $r.Content.Substring(0, [Math]::Min(300, $r.Content.Length)))
} catch {
    $resp = $_.Exception.Response
    if ($resp) {
        $reader = [System.IO.StreamReader]::new($resp.GetResponseStream())
        Write-Host ("REG -> " + [int]$resp.StatusCode + " " + $reader.ReadToEnd().Substring(0, [Math]::Min(300, $reader.ReadToEnd().Length)))
    } else {
        Write-Host ("REG -> ERR " + $_.Exception.Message)
    }
}
