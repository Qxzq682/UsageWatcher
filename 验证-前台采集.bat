@echo off
title 验证 · 前台采集
setlocal
set "ROOT=%~dp0"
call "%ROOT%tools\_python.bat"

if not defined PYEXE (
    echo [x] 没有找到 Python。请先双击 tools\install_deps.bat。
    pause
    exit /b 1
)

echo ================================================================
echo  前台采集验证
echo.
echo  接下来请：Alt-Tab 切换窗口，观察下面是否**立刻**打印出新的前台程序。
echo  检查三点：
echo    1. 切窗口时几乎是瞬时打印（事件驱动，毫秒级）
echo    2. 把窗口最小化，应显示「非前台」
echo    3. 如果某个程序显示「读不到路径」，那是系统拒绝访问，属正常
echo.
echo  按 Ctrl+C 可以随时退出。
echo ================================================================
echo.
"%PYEXE%" "%ROOT%tools\smoke_collect.py"
pause
