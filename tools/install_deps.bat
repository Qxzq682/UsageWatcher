@echo off
setlocal

rem ============================================================
rem  安装本程序需要的第三方依赖（只需运行一次）
rem  双击本文件即可，不需要手敲任何命令
rem ============================================================

rem 【改这里】如果你换了自己的 Python 环境，把下面这一行改成你的 python.exe 路径
rem set "PY=C://path//to//your//python.exe"

if not exist "%PY%" (
    echo [!] 没有找到上面的 Python，尝试用系统 PATH 里的 python ...
    set "PY=python"
)

echo.
echo 使用 Python: %PY%
echo.
"%PY%" --version
if errorlevel 1 (
    echo.
    echo [x] 找不到 Python，请编辑本文件顶部的 PY 变量为你的 python.exe 完整路径。
    pause
    exit /b 1
)

echo.
echo === 正在安装 psutil==7.2.2 ===
"%PY%" -m pip install --disable-pip-version-check "psutil==7.2.2"
if errorlevel 1 (
    echo.
    echo [x] 安装失败。可能是网络问题，可尝试加国内镜像重跑：
    echo     "%PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "psutil==7.2.2"
    pause
    exit /b 1
)

echo.
echo === 正在安装 pycaw==20251023 ===
"%PY%" -m pip install --disable-pip-version-check "pycaw==20251023"
if errorlevel 1 (
    echo.
    echo [x] 安装失败。可能是网络问题，可尝试加国内镜像重跑：
    echo     "%PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "pycaw==20251023"
    pause
    exit /b 1
)

echo.
echo === 正在安装 comtypes==1.4.16 ===
"%PY%" -m pip install --disable-pip-version-check "comtypes==1.4.16"
if errorlevel 1 (
    echo.
    echo [x] 安装失败。可能是网络问题，可尝试加国内镜像重跑：
    echo     "%PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "comtypes==1.4.16"
    pause
    exit /b 1
)

echo.
echo === 验证 ===
"%PY%" -c "import psutil, comtypes, pycaw, sys; print('psutil', psutil.__version__, '| comtypes', comtypes.__version__, '| pycaw OK |', sys.version.split()[0])"

echo.
echo [√] 依赖安装完成。接下来双击“启动.bat”即可开始监测。
pause
