@echo off
rem ============================================================
rem  共用的 Python 定位脚本（被其它 .bat 用 call 调用）
rem
rem  调用后设置三个变量：
rem    PYEXE  = python.exe   （有控制台窗口，适合看输出）
rem    PYWEXE = pythonw.exe  （无控制台窗口，适合后台常驻）
rem    PYTHONIOENCODING=gbk  （让 Python 输出对齐中文控制台的代码页）
rem
rem  查找顺序：项目自带 .venv → 本机已知的 venv → 系统 PATH
rem ============================================================

rem Python 默认可能按 UTF-8 输出，而中文 Windows 控制台是 GBK(936)，
rem 结果中文全变乱码。统一强制成 GBK 就对齐了（实测踩过）。
set "PYTHONIOENCODING=gbk"

set "_ROOT=%~dp0.."
set "PYEXE="
set "PYWEXE="

if exist "%_ROOT%\.venv\Scripts\python.exe" set "PYEXE=%_ROOT%\.venv\Scripts\python.exe"
if not defined PYEXE if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PYEXE=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not defined PYEXE for %%P in (python.exe) do set "PYEXE=%%~$PATH:P"

if defined PYEXE set "PYWEXE=%PYEXE:python.exe=pythonw.exe%"
if not exist "%PYWEXE%" set "PYWEXE=%PYEXE%"

rem —— venv 的 Scripts\pythonw.exe 是启动器壳：拉起真解释器后自己常驻不退出，
rem 白占 ~5MB 内存，任务管理器里会出现两个 pythonw（BUG-29）。
rem 用 sys.base_prefix 找到真解释器目录（venv 时是 versions\<ver>\），优先用它。
if defined PYWEXE if exist "%PYWEXE%" (
    for /f "delims=" %%B in ('"%PYEXE%" -c "import sys;print(sys.base_prefix)" 2^>nul') do (
        if exist "%%B\pythonw.exe" set "PYWEXE=%%B\pythonw.exe"
    )
)

exit /b 0
