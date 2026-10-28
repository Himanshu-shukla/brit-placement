$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'data\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Host 'No saved server process.'; exit }
$serverPid = [int](Get-Content -LiteralPath $pidFile)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid"
$expectedPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($process -and $process.ExecutablePath -eq $expectedPython -and $process.CommandLine -match 'app\.py\s+serve') {
    & taskkill.exe /PID $serverPid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the server process.' }
    Write-Host 'Placement OS stopped.'
} elseif ($process) {
    throw 'The saved PID belongs to another process; it was left running.'
} else {
    Write-Host 'Placement OS is already stopped.'
}
