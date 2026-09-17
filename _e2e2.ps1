$base='http://127.0.0.1:8000'
$l=@{username='envtest';password='EnvTest123'}
$r=Invoke-RestMethod -Uri "$base/api/v1/auth/login" -Method Post -ContentType 'application/json' -Body ($l|ConvertTo-Json)
$tk=$r.data.access_token
Write-Host ("TOKEN_LEN="+$tk.Length)
$xlsx='C:\Users\Administrator\Desktop\my_ai_assistant\data\table_originals\1f1e9dc28e3f4d4faf9e140d55277b48.xlsx'
$args = @('-s','-m','120','-X','POST',"$base/api/v1/rag/upload",'-H',"Authorization: Bearer $tk",'-F',"file=@$xlsx")
$out = & curl.exe @args
Write-Host ("UPLOAD_BODY=$out")
