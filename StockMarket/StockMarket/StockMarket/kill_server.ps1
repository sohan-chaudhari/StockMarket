
$port = 8000
$process = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
if ($process) {
    $pid = $process.OwningProcess
    Write-Host "Killing process on port $port with PID $pid"
    Stop-Process -Id $pid -Force
} else {
    Write-Host "No process found on port $port"
}
