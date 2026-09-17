$base='http://127.0.0.1:8000'
# 1) 注册测试用户(已存在则忽略)
$b=@{username='envtest';email='envtest@example.com';password='EnvTest123';full_name='Env Test'}
try { Invoke-RestMethod -Uri "$base/api/v1/auth/register" -Method Post -ContentType 'application/json' -Body ($b|ConvertTo-Json) | Out-Null } catch { Write-Host ("REGISTER_NOTE="+ $_.Exception.Message) }
# 2) 登录取 token
$l=@{username='envtest';password='EnvTest123'}
$r=Invoke-RestMethod -Uri "$base/api/v1/auth/login" -Method Post -ContentType 'application/json' -Body ($l|ConvertTo-Json)
Write-Host ("LOGIN_RESP=" + ($r|ConvertTo-Json -Depth 5))
$tk = $r.data.access_token
if (-not $tk) { $tk = $r.data.token }
if (-not $tk) { $tk = $r.data }
Write-Host ("TOKEN_LEN=" + $tk.Length)
# 3) 上传真实 xlsx
$f='C:\Users\Administrator\Desktop\my_ai_assistant\data\table_originals\1f1e9dc28e3f4d4faf9e140d55277b48.xlsx'
$fileBin=[System.IO.File]::ReadAllBytes($f)
$body=New-Object System.Net.Http.MultipartFormDataContent
$fileContent=New-Object System.Net.Http.ByteArrayContent($fileBin)
$fileContent.Headers.ContentType=[System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
$body.Add($fileContent,'file',[System.IO.Path]::GetFileName($f))
$cli=New-Object System.Net.Http.HttpClient
$cli.DefaultRequestHeaders.Authorization=[System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer',$tk)
$resp=$cli.PostAsync("$base/api/v1/rag/upload",$body).Result
$content=$resp.Content.ReadAsStringAsync().Result
Write-Host ("UPLOAD_STATUS="+$resp.StatusCode)
Write-Host ("UPLOAD_BODY="+$content)
