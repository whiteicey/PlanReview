$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $repo "artifacts\v1_2_manual_validation\runtime"
$pidPath = Join-Path $runtime "service.pid"
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Output "V1.2 manual validation service is not running"
    exit 0
}
$servicePid = [int](Get-Content -LiteralPath $pidPath -Raw)
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $servicePid" -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Output "Stale V1.2 manual validation PID file removed"
    exit 0
}
$commandLine = [string]$process.CommandLine
if ($commandLine -notmatch "uvicorn" -or $commandLine -notmatch "--port\s+8877" -or $commandLine -notmatch [regex]::Escape($repo)) {
    throw "Refusing to stop PID $servicePid because it does not match the V1.2/8877 service command"
}
Stop-Process -Id $servicePid -Force
Remove-Item -LiteralPath $pidPath -Force
Write-Output "V1.2 manual validation service stopped (PID $servicePid)"
