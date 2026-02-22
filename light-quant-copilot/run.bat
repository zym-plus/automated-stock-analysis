@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

:: ============================================================
::  量化副驾 · Windows 一键启动器
::  兼容中文路径 / 空格路径
:: ============================================================

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo.
echo ==============================
echo   量化副驾 . 一键启动器
echo ==============================
echo.

:: ── 1. 检测 Python ─────────────────────────────────────────
set "PYTHON_CMD="

where python >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
    where py >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py"
)

if not defined PYTHON_CMD (
    echo [错误] 未找到 Python。
    echo.
    echo 请前往官网下载 Python 3.11 或更高版本：
    echo     https://www.python.org/downloads/
    echo.
    echo 安装时请务必勾选  "Add Python to PATH"  选项。
    echo.
    pause
    exit /b 1
)

:: ── 2. 检查版本 ────────────────────────────────────────────
set "PYMAJOR="
set "PYMINOR="
for /f "tokens=1,2" %%a in ('"%PYTHON_CMD%" -c "import sys; print(sys.version_info.major, sys.version_info.minor)" 2^>nul') do (
    set "PYMAJOR=%%a"
    set "PYMINOR=%%b"
)

if not defined PYMAJOR (
    echo [错误] 无法读取 Python 版本，请确认 Python 安装完整。
    pause
    exit /b 1
)

:: 版本 >= 3.11 检查（先比主版本，再比次版本）
if !PYMAJOR! LSS 3 goto :version_error
if !PYMAJOR! GTR 3 goto :version_ok
if !PYMINOR! LSS 11 goto :version_error
goto :version_ok

:version_error
echo [错误] 当前 Python 版本为 !PYMAJOR!.!PYMINOR!，需要 3.11 或更高版本。
echo.
echo 请前往官网下载最新版本：
echo     https://www.python.org/downloads/
echo.
echo 安装时请勾选 "Add Python to PATH" 并卸载旧版本。
echo.
pause
exit /b 1

:version_ok
echo [OK] Python !PYMAJOR!.!PYMINOR! 检测通过

:: ── 3. 创建虚拟环境 ────────────────────────────────────────
set "VENV=%ROOT%.venv"
set "VPYTHON=%VENV%\Scripts\python.exe"
set "VPIP=%VENV%\Scripts\pip.exe"
set "VSTREAMLIT=%VENV%\Scripts\streamlit.exe"

if not exist "%VENV%\Scripts\activate.bat" (
    echo 正在创建虚拟环境...
    "%PYTHON_CMD%" -m venv "%VENV%"
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败。
        echo.
        echo 可能原因：磁盘空间不足，或路径权限不足。
        echo 请尝试以管理员身份运行本脚本。
        pause
        exit /b 1
    )
    echo [OK] 虚拟环境已创建
)

:: ── 4. 安装依赖 ────────────────────────────────────────────
echo 正在检查并安装依赖（首次启动可能需要几分钟，请耐心等待）...
"%VPIP%" install -r "%ROOT%requirements.txt" -q
if errorlevel 1 (
    echo [错误] 依赖安装失败。
    echo.
    echo 请检查网络连接，或尝试以下操作后重新运行：
    echo   1. 确认能访问 https://pypi.org
    echo   2. 如在公司网络，可能需要配置代理
    pause
    exit /b 1
)
echo [OK] 依赖已就绪

:: ── 5. 初始化数据库（幂等，可重复运行）─────────────────────
"%VPYTHON%" -c "import sys; sys.path.insert(0, r'%ROOT%'); from app.logic.db import init_db; init_db()"
if errorlevel 1 (
    echo [警告] 数据库初始化遇到问题，但不影响首次使用。
)
echo [OK] 数据库已就绪

:: ── 6. 启动 ──────────────────────────────────────────────
echo.
echo ==============================
echo   正在启动量化副驾...
echo ==============================
echo.
echo   访问地址：http://localhost:8501
echo.
echo   浏览器将自动打开，如未自动打开请手动复制上方地址。
echo   关闭此窗口即可停止服务。
echo.
echo ==============================
echo.

set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
"%VSTREAMLIT%" run "%ROOT%app\ui.py"

if errorlevel 1 (
    echo.
    echo [错误] Streamlit 启动失败。
    echo.
    echo 请截图此界面后联系支持，或尝试手动运行：
    echo     streamlit run app\ui.py
    echo.
)

pause
endlocal
