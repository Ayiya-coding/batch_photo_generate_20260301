@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

REM Get project root (parent of scripts directory)
set "ROOT=%~dp0.."
cd /d "%ROOT%"

set "RUNTIME_DIR=%ROOT%.runtime"
set "LOG_DIR=%RUNTIME_DIR%\logs"
set "PID_DIR=%RUNTIME_DIR%\pids"

echo ========================================
echo   AI 图片批量生成系统 - Windows 一键启动
echo ========================================
echo.

where py >nul 2>nul
if %errorlevel% neq 0 (
  where python >nul 2>nul
  if %errorlevel% neq 0 (
    echo [ERROR] 未检测到 Python（py/python）。
    echo 请先安装 Python 3.10+，并勾选 "Add Python to PATH"。
    pause
    exit /b 1
  )
  set "PY_CMD=python"
) else (
  set "PY_CMD=py -3"
)

where npm >nul 2>nul
if %errorlevel% neq 0 (
  echo [ERROR] 未检测到 npm。
  echo 请先安装 Node.js 18+（建议 LTS）。
  pause
  exit /b 1
)

if not exist ".env" (
  if exist ".env.example" (
    copy /Y ".env.example" ".env" >nul
    echo [WARN] 未找到 .env，已从 .env.example 自动创建。
  ) else (
    echo [ERROR] 缺少 .env 和 .env.example。
    pause
    exit /b 1
  )
)

if exist "scripts\apply_access_keys.py" (
  if exist "可行性分析\AccessKey.txt" (
    echo [INFO] 检测到 可行性分析\AccessKey.txt，正在自动写入 .env ...
    %PY_CMD% "scripts\apply_access_keys.py" --access-key-file "可行性分析\AccessKey.txt" --env-file ".env" --env-example ".env.example" --quiet
  )
)

echo [1/5] 准备 Python 虚拟环境...
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv 2>nul
  if %errorlevel% neq 0 (
    python -m venv .venv
    if %errorlevel% neq 0 (
      echo [ERROR] 创建虚拟环境失败。
      pause
      exit /b 1
    )
  )
)

echo [2/5] 安装后端依赖（首次会较慢）...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\pip.exe" install -r "backend\requirements.txt"
if %errorlevel% neq 0 (
  echo [ERROR] 后端依赖安装失败。
  pause
  exit /b 1
)

echo [3/5] 安装前端依赖（首次会较慢）...
if not exist "frontend\node_modules" (
  pushd "frontend"
  call npm ci
  set "NPM_RC=!errorlevel!"
  popd
  if not "!NPM_RC!"=="0" (
    echo [ERROR] 前端依赖安装失败。
    pause
    exit /b 1
  )
)

echo [4/5] 初始化数据库...
set "DEBUG=false"
".venv\Scripts\python.exe" "scripts\init_db.py"
if %errorlevel% neq 0 (
  echo [ERROR] 数据库初始化失败，请检查 .env 配置。
  pause
  exit /b 1
)

REM 创建运行时目录
mkdir "%LOG_DIR%" 2>nul
mkdir "%PID_DIR%" 2>nul

echo [5/6] 启动后端服务...
call :StartBackend
call :WaitForHTTP "后端" "http://127.0.0.1:8000/health" 60
if %errorlevel% neq 0 (
  call :ShowLogTail "%LOG_DIR%\backend.log" "后端"
  exit /b 1
)

echo [6/6] 启动前端服务...
call :StartFrontend
call :WaitForHTTP "前端" "http://127.0.0.1:3000" 60
if %errorlevel% neq 0 (
  call :ShowLogTail "%LOG_DIR%\frontend.log" "前端"
  exit /b 1
)

echo.
echo ========================================
echo 启动完成：
echo   前端: http://localhost:3000
echo   后端: http://localhost:8000
echo   日志: %LOG_DIR%
echo 停止服务：双击 stop_local.cmd
echo ========================================
echo.
pause
exit /b 0

REM ============================================
REM 辅助函数
REM ============================================

:StartBackend
REM 检查端口是否已占用
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
  echo [INFO] 后端端口 8000 已在监听，跳过启动。
  goto :eof
)

echo [INFO] 启动后端服务 (端口 8000)...
start "AI Backend :8000" cmd /k "cd /d \"%ROOT%backend\" && set DEBUG=false && (\"%ROOT%.venv\Scripts\python.exe\" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 2>&1 | powershell -NoProfile -Command \"$input ^| Tee-Object -FilePath '%LOG_DIR%\backend.log'\")"

REM 等待进程启动并捕获 PID
timeout /t 3 /nobreak >nul
for /f "tokens=2 delims==" %%a in ('wmic process where "commandline like '%%uvicorn app.main:app%%' and commandline like '%%8000%%'" get processid /format:list 2^>nul ^| findstr "ProcessId"') do (
  echo %%a > "%PID_DIR%\backend.pid"
)
goto :eof

:StartFrontend
REM 检查端口是否已占用
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :3000 ^| findstr LISTENING') do (
  echo [INFO] 前端端口 3000 已在监听，跳过启动。
  goto :eof
)

echo [INFO] 启动前端服务 (端口 3000)...
start "AI Frontend :3000" cmd /k "cd /d \"%ROOT%frontend\" && (npm run dev -- --host 0.0.0.0 --port 3000 2>&1 | powershell -NoProfile -Command \"$input ^| Tee-Object -FilePath '%LOG_DIR%\frontend.log'\")"

REM 等待进程启动并捕获 PID
timeout /t 3 /nobreak >nul
for /f "tokens=2 delims==" %%a in ('wmic process where "commandline like '%%npm run dev%%' and commandline like '%%3000%%'" get processid /format:list 2^>nul ^| findstr "ProcessId"') do (
  echo %%a > "%PID_DIR%\frontend.pid"
)
goto :eof

:WaitForHTTP
REM 参数: %1=服务名称, %2=URL, %3=超时秒数
set "SVC_NAME=%~1"
set "SVC_URL=%~2"
set "TIMEOUT_SEC=%~3"
set /a "ATTEMPTS=%TIMEOUT_SEC%"

echo [INFO] 等待 %SVC_NAME% 启动 (%SVC_URL%)...

:WaitLoop
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri '%SVC_URL%' -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop; if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if %errorlevel% equ 0 (
  echo [INFO] %SVC_NAME% 已就绪: %SVC_URL%
  exit /b 0
)

set /a "ATTEMPTS-=1"
if %ATTEMPTS% leq 0 (
  echo [ERROR] %SVC_NAME% 健康检查超时 (%TIMEOUT_SEC%秒)
  exit /b 1
)

timeout /t 1 /nobreak >nul
goto WaitLoop

:ShowLogTail
set "LOG_FILE=%~1"
set "SVC_NAME=%~2"
echo.
echo [ERROR] %SVC_NAME% 启动失败，最近日志：
echo ========================================
powershell -NoProfile -Command "if (Test-Path '%LOG_FILE%') { Get-Content '%LOG_FILE%' -Tail 30 } else { Write-Host '日志文件不存在' }"
echo ========================================
pause
goto :eof
