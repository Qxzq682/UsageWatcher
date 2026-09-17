@echo off
title UsageWatcher 卸载向导
setlocal
set "ROOT=%~dp0"
call "%ROOT%tools\_python.bat"

if not defined PYEXE (
    echo [x] 没有找到 Python。
    pause
    exit /b 1
)

rem 卸载向导：停程序 → 删自启 → 删快捷方式 → 删数据目录。
rem 每步先预览并确认；数据一律送回收站，后悔可还原。
rem 加 --yes 可跳过逐项确认："%~f0" --yes
"%PYEXE%" "%ROOT%tools\uninstall.py" %*

ping -n 3 127.0.0.1 >nul 2>&1
