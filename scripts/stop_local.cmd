@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "ROOT=%~dp0"
set "PID_DIR=%ROOT%.runtime\pids"

echo ========================================
echo   AI 图片批量生成系统 - Windows 停止脚本
echo ========================================
echo.

call :KillService 8000 后端 backend
call :KillService 3000 前端 frontend
call :KillService 8090 IOPaint iopaint

echo.
echo 所有相关端口进程已尝试关闭。
echo.
pause
exit /b 0

:KillService
set "PORT=%~1"
set "NAME=%~2"
set "PID_NAME=%~3"
set "PID_FILE=%PID_DIR%\%PID_NAME%.pid"

REM 优先使用 PID 文件
if exist "%PID_FILE%" (
  for /f %%p in (%PID_FILE%) do (
    echo [INFO] 关闭 %NAME% (PID=%%p)
    taskkill /F /PID %%p >nul 2>nul
    if %errorlevel% equ 0 (
      del "%PID_FILE%" 2>nul
      goto :eof
    )
  )
  REM PID 文件存在但进程已不存在，删除 PID 文件
  del "%PID_FILE%" 2>nul
)

REM 回退到端口查找
set "FOUND=0"
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :%PORT% ^| findstr LISTENING') do (
  set "FOUND=1"
  echo [INFO] 关闭 %NAME% (端口 %PORT%)，PID=%%a
  taskkill /F /PID %%a >nul 2>nul
)
if "%FOUND%"=="0" (
  echo [INFO] %NAME% (端口 %PORT%) 未运行
)
goto :eof
