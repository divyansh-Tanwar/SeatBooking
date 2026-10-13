$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $project 'artifacts\local-processes.json'
if (Test-Path -LiteralPath $pidFile) {
    $saved = Get-Content -LiteralPath $pidFile | ConvertFrom-Json
    foreach ($taskPid in @($saved.web, $saved.jobs)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $taskPid"
        if ($process -and $process.ExecutablePath -eq "$project\.venv\Scripts\python.exe" -and
            ($process.CommandLine -match 'config.asgi:application|manage.py process_jobs')) {
            Stop-Process -Id $taskPid
        }
    }
}
& "$project\artifacts\postgres\pgsql\bin\pg_ctl.exe" -D "$project\artifacts\pgdata" -w stop
