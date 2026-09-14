@echo off
title 启动软件使用时长监测
setlocal
set "ROOT=%~dp0"
call "%ROOT%tools\_python.bat"

if not defined PYEXE (
    echo [x] 没有找到 Python。请先双击 tools\install_deps.bat 安装依赖。
    pause
    exit /b 1
)

echo 使用 Python：%PYEXE%
echo 正在后台启动监测程序（不会有窗口，图标在右下角托盘）...

rem 用 pythonw 启动：不弹控制台窗口，安静常驻（这正是"低存在感"的要求）
start "" "%PYWEXE%" "%ROOT%src\app.py"

ping -n 3 127.0.0.1 >nul 2>&1
echo.
echo [√] 已启动。操作方式：
echo     · 右键托盘图标 → 打开统计窗口 / 暂停统计 / 退出
echo     · 统计窗口也可以直接双击 启动统计窗口.bat
echo.
echo 数据目录：%APPDATA%\UsageWatcher
echo （本程序不联网、不上传任何数据；可用 验证-自检全部.bat 自行验证）
ping -n 7 127.0.0.1 >nul 2>&1
