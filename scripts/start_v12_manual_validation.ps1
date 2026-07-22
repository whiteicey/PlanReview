param(
    [int]$Port = 8877,
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$storage = Join-Path $repo "artifacts\v1_2_manual_validation\storage"
$runtime = Join-Path $repo "artifacts\v1_2_manual_validation\runtime"
New-Item -ItemType Directory -Force -Path $storage, $runtime, (Join-Path $storage "cases") | Out-Null

$env:REVIEW_STORAGE_ROOT = $storage
$env:PYTHONPATH = $repo
$gitSha = (& git -C $repo rev-parse HEAD).Trim()
$pidPath = Join-Path $runtime "service.pid"
$stdoutPath = Join-Path $runtime "service.stdout.log"
$stderrPath = Join-Path $runtime "service.stderr.log"
$summaryPath = Join-Path $runtime "service.environment.json"

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    $existing = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($existing) {
        throw "V1.2 manual validation service is already running with PID $existingPid"
    }
    Remove-Item -LiteralPath $pidPath -Force
}

$pythonCandidates = @()
if ($PythonExecutable) {
    $pythonCandidates += $PythonExecutable
}
if ($env:PLANREVIEW_PYTHON) {
    $pythonCandidates += $env:PLANREVIEW_PYTHON
}
$pythonCandidates += @(
    (Join-Path $repo ".venv\Scripts\python.exe"),
    "python"
)
$selectedPython = $null
foreach ($candidate in $pythonCandidates) {
    try {
        if ($candidate -ne "python" -and -not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        & $candidate -c "import uvicorn" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $selectedPython = $candidate
            break
        }
    } catch {
        continue
    }
}
if (-not $selectedPython) {
    throw "No Python interpreter with uvicorn is available. Pass -PythonExecutable or set PLANREVIEW_PYTHON."
}

$arguments = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$Port", "--app-dir", $repo)
$process = Start-Process -FilePath $selectedPython -ArgumentList $arguments -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
Set-Content -LiteralPath $pidPath -Value $process.Id -NoNewline
$flags = @{}
foreach ($ruleId in @("REFERENCE-001","SUMMARY_DETAIL-001","CROSS_SOURCE_PARAM-001","UNIT_MAGNITUDE-001","SCHEDULE-001","EQUIPMENT_REDUNDANCY-001")) {
    $flagName = "REVIEW_RULE_$($ruleId.Replace('-', '_'))_ENABLED"
    $flagValue = [Environment]::GetEnvironmentVariable($flagName)
    $flags[$ruleId] = $flagValue -and $flagValue.Trim().ToLowerInvariant() -in @("1", "true", "yes", "on", "enabled")
}
@{
    commit = $gitSha
    branch = (& git -C $repo branch --show-current).Trim()
    service_url = "http://127.0.0.1:$Port"
    host = "127.0.0.1"
    port = $Port
    pid = $process.Id
    python_executable = $selectedPython
    storage_root = $storage
    database_path = (Join-Path $storage "review.db")
    upload_root = (Join-Path $storage "cases")
    rule_flags = $flags
    secrets_included = $false
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
Write-Output "V1.2 manual validation service started: http://127.0.0.1:$Port (PID $($process.Id))"
