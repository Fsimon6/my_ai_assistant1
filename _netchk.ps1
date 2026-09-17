Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(8000,8011,8012,8013) } | ForEach-Object { ('PORT=' + $_.LocalPort + ' PID=' + $_.OwningProcess) }
