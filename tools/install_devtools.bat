@echo off
setlocal

rem ============================================================
rem  安装「开发期」工具：代码安全扫描 + 依赖漏洞扫描
rem  这些不是运行程序所必需的，只在你想自己检查代码安全时用
rem ============================================================

rem 【改这里】换 Python 环境就改这一行
rem set "PY=C://path//to//your//python.exe"
if not exist "%PY%" set "PY=python"

echo 使用 Python: %PY%
echo.
echo === 安装 bandit（代码静态安全检查）与 pip-audit（依赖漏洞检查）===
"%PY%" -m pip install --disable-pip-version-check bandit pip-audit
if errorlevel 1 (
    echo.
    echo [x] 安装失败。可尝试国内镜像：
    echo     "%PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple bandit pip-audit
    pause
    exit /b 1
)

echo.
echo === 验证 ===
"%PY%" -m bandit --version
"%PY%" -m pip_audit --version

echo.
echo [√] 完成。之后可以这样用（在项目根目录执行）：
echo     "%PY%" -m bandit -r src -ll
echo     "%PY%" -m pip_audit -r requirements.txt
pause
