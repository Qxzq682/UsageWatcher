@echo off
title 构建发行包（构建 + 冒烟 + 全量/快更 zip）
setlocal
set "ROOT=%~dp0"
call "%ROOT%tools\_python.bat"

if not defined PYEXE (
    echo [x] 没有找到 Python。
    pause
    exit /b 1
)

rem 发行流水线：自动构建（全新 buildN/distN）-> 打包冒烟 -> 生成
rem 全量 zip 与 lite 快更 zip -> 实机验证"新 exe + 上一版依赖"兼容性。
rem 产物在 release\ 目录，直接分发 zip 即可。
"%PYEXE%" "%ROOT%tools\release.py"

echo.
pause
