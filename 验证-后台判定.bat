@echo off
title 验证 · 后台活跃判定
setlocal
set "ROOT=%~dp0"
call "%ROOT%tools\_python.bat"

if not defined PYEXE (
    echo [x] 没有找到 Python。请先双击 tools\install_deps.bat。
    pause
    exit /b 1
)

echo ================================================================
echo  后台活跃判定验证
echo ================================================================
echo.
echo  [A] 受控实验：程序自己造两个对照进程（一个狂写磁盘、一个只挂机），
echo      看阈值能不能把它们区分开。这一步是自动的，先跑它。
echo.
echo  [B] 真实观察：把你关心的软件挂到后台，看它落在「活跃」还是「挂着」。
echo.
set /p CHOICE=请选择 A 或 B（默认 A）：

if /i "%CHOICE%"=="B" (
    "%PYEXE%" "%ROOT%tools\bg_probe.py" --rounds 12
) else (
    "%PYEXE%" "%ROOT%tools\bg_probe.py" --selftest --rounds 5
)

echo.
pause
