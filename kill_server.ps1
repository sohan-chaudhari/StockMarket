
$port = 8000
$process = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
if ($process) {
    $procId = $process.OwningProcess
    Write-Host "Killing process on port $port with PID $procId"
    Stop-Process -Id $procId -Force
} else {
    Write-Host "No process found on port $port"
}
