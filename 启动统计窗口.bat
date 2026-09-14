@echo off
title 软件使用时长监测 · 统计窗口
setlocal
set "ROOT=%~dp0"
call "%ROOT%tools\_python.bat"

if not defined PYEXE (
    echo [x] 没有找到 Python。请先双击 tools\install_deps.bat 安装依赖。
    pause
    exit /b 1
)

rem 统计窗口是一个独立进程：用完关掉，它占的内存就还回去了
"%PYEXE%" "%ROOT%src\app.py" --gui
