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

rem —— BUG-29 曾尝试改用真解释器（versions 目录下的 pythonw.exe）省掉壳的
rem ~5MB，实测作废（09-18）：真解释器不加载 venv 的 site-packages，连 psutil
rem 都 ModuleNotFoundError；用 PYTHONPATH 补也不行——PYTHONPATH 目录不是
rem site 目录，.pth 不执行，pywin32（win32api）照挂。pythonw 静默无窗，表现
rem 为启动.bat 双击后毫无反应。故 PYWEXE 固定用 venv 壳：壳拉起真身时以
rem __PYVENV_LAUNCHER__ 让真身按 venv 解析依赖，链路完整、实测运行多日。
rem 代价：任务管理器多一个 ~5MB 的 pythonw（壳），可接受。

exit /b 0
