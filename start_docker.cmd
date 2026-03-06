@echo off
setlocal
cd /d "%~dp0"

if not exist ".env" (
  if exist ".env.example" (
    copy ".env.example" ".env" >nul
    echo [WARN] .env not found. Created from .env.example. Fill API keys and rerun.
  )
)

if not exist "data" mkdir "data"
if not exist "models" mkdir "models"

docker compose up -d --build %*
if errorlevel 1 exit /b %errorlevel%

echo.
echo ==================== DOCKER STARTED ====================
echo Frontend : http://localhost:3000
echo Backend  : http://localhost:8000
echo Stop all : stop_docker.cmd
echo ========================================================
