# One-time Postgres bootstrap for Garmin Coach when the "postgres" master
# password is unknown.
#
# WHAT IT DOES (and why it's safe):
#   1. Backs up pg_hba.conf  (Postgres's "who is allowed to connect" file).
#   2. Temporarily switches local logins to "trust" (no password required).
#   3. Restarts Postgres so that takes effect.
#   4. Connects as "postgres" (now password-less) and runs setup_postgres.sql,
#      which creates the "coach" login + "garmin_coach" database.
#   5. In a finally{} block that runs NO MATTER WHAT, restores the original
#      pg_hba.conf and restarts again -- so security is back on within seconds.
#
# HOW TO RUN:  open PowerShell *as Administrator*, then:
#   powershell -ExecutionPolicy Bypass -File "C:\Users\papad\Desktop\garmin-coach\scripts\reset_pg_and_setup.ps1"

$ErrorActionPreference = 'Stop'

# --- Make sure we're running as Administrator. Controlling the Postgres
#     Windows service requires it. If we're not elevated, relaunch ourselves
#     in a new elevated window (you'll get a UAC prompt to approve). ---
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Not running as Administrator - relaunching (approve the UAC prompt)..." -ForegroundColor Yellow
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList @(
        '-ExecutionPolicy', 'Bypass', '-NoExit', '-File', "`"$PSCommandPath`""
    )
    exit
}

$pgBin    = 'C:\Program Files\PostgreSQL\18\bin'
$dataDir  = 'C:\Program Files\PostgreSQL\18\data'
$hba      = Join-Path $dataDir 'pg_hba.conf'
$service  = 'postgresql-x64-18'
$setupSql = 'C:\Users\papad\Desktop\garmin-coach\scripts\setup_postgres.sql'

if (-not (Test-Path $hba))      { throw "pg_hba.conf not found at $hba" }
if (-not (Test-Path $setupSql)) { throw "setup_postgres.sql not found at $setupSql" }

$backup = "$hba.coachbak"
Copy-Item $hba $backup -Force
Write-Host "[1/5] Backed up pg_hba.conf -> $backup" -ForegroundColor Cyan

try {
    # 2. Temporarily allow password-less local logins.
    $orig = Get-Content $hba -Raw
    $temp = $orig -replace 'scram-sha-256', 'trust' -replace '\bmd5\b', 'trust'
    Set-Content -Path $hba -Value $temp -Encoding ascii -NoNewline
    Write-Host "[2/5] Temporarily set local auth to 'trust'." -ForegroundColor Cyan

    # 3. Restart so the change takes effect.
    Restart-Service $service
    Start-Sleep -Seconds 3
    Write-Host "[3/5] Postgres restarted." -ForegroundColor Cyan

    # 4. Create the coach role + garmin_coach database (no password needed now).
    & "$pgBin\psql.exe" -U postgres -h 127.0.0.1 -f $setupSql
    Write-Host "[4/5] Ran setup_postgres.sql (created role + database)." -ForegroundColor Green
}
finally {
    # 5. ALWAYS restore the secure auth config, even on error.
    Copy-Item $backup $hba -Force
    Restart-Service $service
    Start-Sleep -Seconds 3
    Write-Host "[5/5] Restored secure pg_hba.conf and restarted. Security is back ON." -ForegroundColor Green
}

Write-Host ""
Write-Host "DONE. The app login 'coach' and database 'garmin_coach' now exist." -ForegroundColor Green
