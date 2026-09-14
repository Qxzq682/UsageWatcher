@echo off
title 停止软件使用时长监测
setlocal
set "ROOT=%~dp0"
call "%ROOT%tools\_python.bat"

if not defined PYEXE (
    echo [x] 没有找到 Python。
    pause
    exit /b 1
)

rem 交给 Python 脚本处理：它会写一个「停止标志文件」，守护进程发现后
rem 自己闭合当前片段并写库再退出 —— 不会丢掉你正在用的那一段。
rem 注意：命令必须写成单行。cmd 会把多行 python -c 按行拆开执行（踩过）。
"%PYEXE%" "%ROOT%tools\request_stop.py"

ping -n 3 127.0.0.1 >nul 2>&1
