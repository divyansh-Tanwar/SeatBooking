$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $project
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = '55432'
$env:PYTHONUTF8 = '1'
$env:EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
$env:LOCAL_POLLING = '1'
$pg = Join-Path $project 'artifacts\postgres\pgsql\bin\pg_ctl.exe'
$python = Join-Path $project '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pg) -or !(Test-Path -LiteralPath $python)) {
    throw 'This local launcher uses the project-local runtime prepared during development. Use README Docker setup on a fresh machine.'
}
& $pg -D "$project\artifacts\pgdata" status
if ($LASTEXITCODE -ne 0) { & $pg -D "$project\artifacts\pgdata" -l "$project\artifacts\postgres.log" -w start }
& $python manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
& $python manage.py seed_demo
$web = Start-Process -FilePath $python -ArgumentList '-m daphne -b 127.0.0.1 -p 8000 config.asgi:application' -WorkingDirectory $project -WindowStyle Hidden -PassThru -RedirectStandardOutput "$project\artifacts\web.log" -RedirectStandardError "$project\artifacts\web-error.log"
$jobs = Start-Process -FilePath $python -ArgumentList 'manage.py process_jobs' -WorkingDirectory $project -WindowStyle Hidden -PassThru -RedirectStandardOutput "$project\artifacts\jobs.log" -RedirectStandardError "$project\artifacts\jobs-error.log"
@{web=$web.Id; jobs=$jobs.Id} | ConvertTo-Json | Set-Content -LiteralPath "$project\artifacts\local-processes.json"
Write-Output 'SeatLock: http://127.0.0.1:8000 (PostgreSQL + polling; durable background jobs run locally)'
