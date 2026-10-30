@echo off
setlocal
cd /d "%~dp0"
title MediLedger Pharmacy Management

REM Check that Docker is installed and the daemon is running.
where docker >nul 2>&1
if errorlevel 1 (
    echo Docker was not found. Install Docker Desktop and try again.
    pause
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo Docker is not running. Start Docker Desktop and try again.
    pause
    exit /b 1
)

REM Create the environment file on first launch and require secret setup.
if not exist ".env" (
    copy ".env.example" ".env" >nul
    if errorlevel 1 (
        echo Could not create .env from .env.example.
        pause
        exit /b 1
    )
    echo Created .env. Edit it and replace the placeholder passwords and secret,
    echo then run this launcher again.
    pause
    exit /b 1
)

REM Start the services and wait until the Flask health endpoint responds.
echo Building and starting MediLedger...
docker compose up --build -d
if errorlevel 1 goto startup_failed

echo Waiting for the web application...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline = (Get-Date).AddMinutes(2); do { try { $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 3; if ($response.StatusCode -eq 200) { exit 0 } } catch { }; Start-Sleep -Seconds 2 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 goto startup_failed

REM Open the app after the health check succeeds.
start "" "http://127.0.0.1:8000"
echo MediLedger is running at http://127.0.0.1:8000
echo To stop it, run: docker compose down
pause
exit /b 0

:startup_failed
echo MediLedger did not start successfully. Recent container logs:
docker compose logs --tail 50
pause
exit /b 1
