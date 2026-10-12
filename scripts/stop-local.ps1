$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $projectRoot 'runtime\processes.json'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output 'No recorded local processes.'; exit }
$recorded = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
if ($recorded.root -ne $projectRoot) { throw 'Process record belongs to another checkout.' }
$entries = @($recorded.processes)
[array]::Reverse($entries)
foreach ($entry in $entries) {
    $found = Get-Process -Id $entry.id -ErrorAction SilentlyContinue
    if ($found -and $found.StartTime.ToUniversalTime().Ticks.ToString() -eq $entry.startTicks) {
        Stop-Process -Id $entry.id
    }
}
Remove-Item -LiteralPath $pidFile
Write-Output 'Recorded LedgerLens services stopped.'
