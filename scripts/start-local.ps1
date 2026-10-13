param([switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache\uv'
$env:npm_config_cache = Join-Path $projectRoot '.cache\npm'
$env:PYTHONPATH = "$projectRoot\backend;$projectRoot"
$env:PYTHONUNBUFFERED = '1'
$runtime = Join-Path $projectRoot 'runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$pidFile = Join-Path $runtime 'processes.json'
if (Test-Path -LiteralPath $pidFile) {
    $existing = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    foreach ($entry in @($existing.processes)) {
        $found = Get-Process -Id $entry.id -ErrorAction SilentlyContinue
        if ($found -and $found.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.startTicks) {
            throw 'Recorded services still exist. Run scripts/stop-local.ps1 first.'
        }
    }
}
foreach ($port in @(8000, 5188)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. No existing process was stopped."
    }
}
if (-not $SkipInstall) {
    uv sync --frozen
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
    Push-Location frontend
    try { npm ci; if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' } }
    finally { Pop-Location }
}
& "$projectRoot\.venv\Scripts\python.exe" "$projectRoot\scripts\prepare_local_database.py"
if ($LASTEXITCODE -ne 0) { throw 'Local database preparation failed. No API or frontend process was started.' }
$owned = @()
function Register-Owned($process) {
    @{ id=$process.Id; startTicks=$process.StartTime.ToUniversalTime().Ticks.ToString() }
}
function Wait-Ready([string]$url) {
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        try { $response=Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2; if ($response.StatusCode -eq 200) { return } }
        catch { Start-Sleep -Milliseconds 500 }
    }
    throw "Service did not become ready: $url. See runtime logs."
}
try {
    $api = Start-Process -FilePath "$projectRoot\.venv\Scripts\python.exe" -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput "$runtime\api.log" -RedirectStandardError "$runtime\api-error.log"
    $owned += Register-Owned $api
    Wait-Ready 'http://127.0.0.1:8000/api/v1/health'
    # Windows venv launchers can create a Python child that owns the listening socket.
    foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($api.Id)")) {
        if ($child.CommandLine -like '*uvicorn app.main:app*') { $owned += Register-Owned (Get-Process -Id $child.ProcessId) }
    }
    $vite = '"' + (Join-Path $projectRoot 'frontend\node_modules\vite\bin\vite.js') + '"'
    $web = Start-Process -FilePath (Get-Command node).Source -ArgumentList $vite,'--host','127.0.0.1','--port','5188','--strictPort' -WorkingDirectory "$projectRoot\frontend" -WindowStyle Hidden -PassThru -RedirectStandardOutput "$runtime\web.log" -RedirectStandardError "$runtime\web-error.log"
    $owned += Register-Owned $web
    Wait-Ready 'http://127.0.0.1:5188'
    @{processes=$owned; root=$projectRoot; started=(Get-Date).ToString('o')} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidFile -Encoding UTF8
    Write-Output 'LedgerLens ready: http://127.0.0.1:5188  API: http://127.0.0.1:8000/docs'
} catch {
    [array]::Reverse($owned)
    foreach ($entry in $owned) {
        $found=Get-Process -Id $entry.id -ErrorAction SilentlyContinue
        if ($found -and $found.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.startTicks) { Stop-Process -Id $entry.id -ErrorAction SilentlyContinue }
    }
    throw
}
