$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'The local Python environment is missing.' }
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PSScriptRoot 'data\playwright'
$env:PLACEMENT_HOST = '127.0.0.1'
$env:PLACEMENT_PORT = '8770'
$env:PLACEMENT_SCHEDULER = '1'
New-Item -ItemType Directory -Path (Join-Path $PSScriptRoot 'data') -Force | Out-Null
$listener = Get-NetTCPConnection -LocalPort 8770 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $keyFile = Join-Path $PSScriptRoot 'data\admin-token.txt'
    if (-not (Test-Path -LiteralPath $keyFile)) { throw 'Port 8770 is occupied by another application.' }
    try {
        $key = (Get-Content -LiteralPath $keyFile -Raw).Trim()
        Invoke-RestMethod -Uri 'http://127.0.0.1:8770/api/scheduler/status' -Headers @{'X-Placement-Key'=$key} -TimeoutSec 3 | Out-Null
    } catch { throw 'Port 8770 is occupied by another application.' }
}
if (-not $listener) {
    $process = Start-Process -FilePath $python -ArgumentList '-u','app.py','serve' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'data\server.log') -RedirectStandardError (Join-Path $PSScriptRoot 'data\server-error.log') -PassThru
    $process.Id | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'data\server.pid')
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($process.HasExited) { throw 'The server stopped. Check data\server-error.log.' }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8770/' -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $ready) { throw 'Server startup timed out. Check data\server-error.log.' }
}
Write-Host 'Placement OS is running at http://127.0.0.1:8770'
Write-Host 'Admin key: data\admin-token.txt (use this in the dashboard and extension)'
