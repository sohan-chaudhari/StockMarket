
Write-Host "Killing all uvicorn/python processes..."
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object { 
    $cmd = $_.CommandLine
    if ($cmd -match "uvicorn" -or $cmd -match "backend.main") {
        Write-Host "Killing Python PID $($_.Id) - $cmd"
        Stop-Process -Id $_.Id -Force
    }
}

Write-Host "Killing all node processes (Frontend)..."
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force

Write-Host "Cleaning up port 8000..."
$process8000 = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue
if ($process8000) {
    Stop-Process -Id $process8000.OwningProcess -Force
}

Write-Host "Done."
