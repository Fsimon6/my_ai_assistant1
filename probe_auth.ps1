Start-Sleep -Seconds 6
$base = "http://127.0.0.1:8000"
function G($p){ try { $r=Invoke-WebRequest -Uri ($base+$p) -UseBasicParsing -TimeoutSec 10; return ([int]$r.StatusCode).ToString()+" "+$r.Content.Substring(0,[Math]::Min(250,$r.Content.Length)) } catch { $x=$_.Exception.Response; if($x){$rd=[System.IO.StreamReader]::new($x.GetResponseStream()); return ([int]$x.StatusCode).ToString()+" "+$rd.ReadToEnd().Substring(0,[Math]::Min(250,$rd.ReadToEnd().Length))} else {return "ERR "+$_.Exception.Message} } }

Write-Host ("HEALTH -> " + (G "/health"))
# register
$body = '{"email":"phase5@example.com","username":"phase5user","password":"pass123456","full_name":"Phase5"}'
try { $r=Invoke-WebRequest -Uri ($base+"/api/v1/auth/register") -Method POST -ContentType 'application/json' -Body $body -UseBasicParsing -TimeoutSec 10; Write-Host ("REGISTER -> "+[int]$r.StatusCode+" "+$r.Content.Substring(0,[Math]::Min(300,$r.Content.Length))) } catch { $x=$_.Exception.Response; if($x){$rd=[System.IO.StreamReader]::new($x.GetResponseStream()); Write-Host ("REGISTER -> "+[int]$x.StatusCode+" "+$rd.ReadToEnd().Substring(0,[Math]::Min(300,$rd.ReadToEnd().Length)))} else {Write-Host ("REGISTER -> ERR "+$_.Exception.Message)} }
# login (JSON username/password)
$lbody = '{"username":"phase5user","password":"pass123456"}'
$token=""
try { $r=Invoke-WebRequest -Uri ($base+"/api/v1/auth/login") -Method POST -ContentType 'application/json' -Body $lbody -UseBasicParsing -TimeoutSec 10; Write-Host ("LOGIN -> "+[int]$r.StatusCode+" "+$r.Content.Substring(0,[Math]::Min(300,$r.Content.Length])); $tok=$r.Content } catch { $x=$_.Exception.Response; if($x){$rd=[System.IO.StreamReader]::new($x.GetResponseStream()); Write-Host ("LOGIN -> "+[int]$x.StatusCode+" "+$rd.ReadToEnd().Substring(0,[Math]::Min(300,$rd.ReadToEnd().Length)))} else {Write-Host ("LOGIN -> ERR "+$_.Exception.Message)} }
# parse token
try { $j=$tok | ConvertFrom-Json; $token=$j.data.access_token; Write-Host ("TOKEN_LEN -> "+$token.Length) } catch { Write-Host "TOKEN_PARSE_FAIL" }
# /me with token
if ($token) { try { $r=Invoke-WebRequest -Uri ($base+"/api/v1/auth/me") -Headers @{"Authorization"="Bearer $token"} -UseBasicParsing -TimeoutSec 10; Write-Host ("ME(token) -> "+[int]$r.StatusCode+" "+$r.Content.Substring(0,[Math]::Min(200,$r.Content.Length])) } catch { $x=$_.Exception.Response; if($x){$rd=[System.IO.StreamReader]::new($x.GetResponseStream()); Write-Host ("ME(token) -> "+[int]$x.StatusCode+" "+$rd.ReadToEnd().Substring(0,[Math]::Min(200,$rd.ReadToEnd().Length)))} else {Write-Host ("ME(token) -> ERR")} }
# /me without token
Write-Host ("ME(no token) -> " + (G "/api/v1/auth/me"))
# invalid token
try { $r=Invoke-WebRequest -Uri ($base+"/api/v1/auth/me") -Headers @{"Authorization"="Bearer invalid.token.here"} -UseBasicParsing -TimeoutSec 10; Write-Host ("ME(bad token) -> "+[int]$r.StatusCode) } catch { $x=$_.Exception.Response; if($x){Write-Host ("ME(bad token) -> "+[int]$x.StatusCode)} else {Write-Host "ME(bad token) ERR"} }
