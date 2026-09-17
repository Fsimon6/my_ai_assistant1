$p='C:\Users\Administrator\Desktop\my_ai_assistant\.venv\Scripts\python.exe'
$i=Get-Item $p -Force
Write-Host ('python LinkType='+$i.LinkType)
Write-Host ('python Target='+$i.Target)
$u='C:\Users\Administrator\Desktop\my_ai_assistant\.venv\Scripts\uvicorn.exe'
$uu=Get-Item $u -Force
Write-Host ('uvicorn LinkType='+$uu.LinkType)
Write-Host ('uvicorn Target='+$uu.Target)
