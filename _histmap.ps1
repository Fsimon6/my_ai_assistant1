$h='C:\Users\Administrator\AppData\Roaming\CodeBuddy CN\User\History'
Get-ChildItem $h -Directory | ForEach-Object {
  $d=$_.FullName
  $f=Get-ChildItem $d -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if($f){
    $lines=@()
    try { $lines=Get-Content $f.FullName -TotalCount 4 -ErrorAction SilentlyContinue } catch {}
    $head=($lines -join ' | ').Replace("`n",' ')
    if($head.Length -gt 130){ $head=$head.Substring(0,130) }
    Write-Host ('{0} | {1} | {2} | {3}' -f $_.Name, $f.LastWriteTime.ToString('MM/dd HH:mm'), $f.Extension, $head)
  }
}
