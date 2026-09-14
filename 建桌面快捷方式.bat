@echo off
title 建桌面快捷方式
setlocal
set "ROOT=%~dp0"
call "%ROOT%tools\_python.bat"

if not defined PYEXE (
    echo [x] 没有找到 Python。请先双击 tools\install_deps.bat。
    pause
    exit /b 1
)

echo ================================================================
echo  在桌面创建快捷方式
echo ================================================================
echo.
"%PYEXE%" "%ROOT%tools\make_shortcut.py"
echo.
echo 提示：本脚本必须在本窗口（沙箱外）运行才有权限写桌面。
pause
