"""共用 Win32 封装（ctypes + pywin32）。

全部为**只读查询**，唯一的例外是需要主动设置自身优先级/压缩自身工作集。
安全约束（R7）：任何 OpenProcess 只请求 PROCESS_QUERY_LIMITED_INFORMATION，
绝不请求 PROCESS_VM_READ / PROCESS_ALL_ACCESS —— 既够用，又不会触发反作弊/杀软误判。

隐私约束：本模块**不提供**任何读取窗口标题、剪贴板、输入内容的能力（有意为之）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import sys
from typing import Optional

import win32api
import win32gui
import win32process

# ---------------------------------------------------------------- 基础常量

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
TOKEN_QUERY = 0x0008
TOKEN_INTEGRITY_LEVEL = 25
TOKEN_USER = 1

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_psapi = ctypes.WinDLL("psapi", use_last_error=True)
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

_kernel32.GetCurrentProcess.restype = w.HANDLE
_kernel32.GetCurrentProcess.argtypes = []
_kernel32.OpenProcess.restype = w.HANDLE
_kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
_kernel32.CloseHandle.argtypes = [w.HANDLE]
_kernel32.OpenMutexW.restype = w.HANDLE
_kernel32.OpenMutexW.argtypes = [w.DWORD, w.BOOL, ctypes.c_wchar_p]
_kernel32.QueryFullProcessImageNameW.argtypes = [
    w.HANDLE,
    w.DWORD,
    ctypes.c_wchar_p,
    ctypes.POINTER(w.DWORD),
]
_kernel32.SetPriorityClass.argtypes = [w.HANDLE, w.DWORD]
_kernel32.GetTickCount.restype = w.DWORD
_kernel32.GetTickCount.argtypes = []
_kernel32.LocalFree.argtypes = [w.HLOCAL]

# 显式声明类型：不声明时 ctypes 默认按 32 位 int 传参，
# 会把 64 位句柄截断 → 调用静默失败（实测踩过）
_user32.GetForegroundWindow.restype = w.HWND
_user32.GetForegroundWindow.argtypes = []
_user32.IsIconic.argtypes = [w.HWND]
_user32.EnumWindows.argtypes = [ctypes.c_void_p, w.LPARAM]
_user32.GetLastInputInfo.argtypes = [ctypes.c_void_p]

_psapi.EmptyWorkingSet.argtypes = [w.HANDLE]

_advapi32.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
_advapi32.GetTokenInformation.argtypes = [
    w.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    w.DWORD,
    ctypes.POINTER(w.DWORD),
]
_advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
_advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
_advapi32.GetSidSubAuthority.restype = ctypes.POINTER(ctypes.c_ulong)
_advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, w.DWORD]

# ---------------------------------------------------------------- 前台窗口


def foreground_hwnd() -> int:
    return int(_user32.GetForegroundWindow() or 0)


def pid_of_window(hwnd: int) -> int:
    """取窗口所属进程 PID。注意 GetWindowThreadProcessId 在 win32process，不在 win32gui。"""
    if not hwnd:
        return 0
    try:
        _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
        return int(pid or 0)
    except Exception:
        return 0


def is_minimized(hwnd: int) -> bool:
    """最小化的窗口仍可能被 GetForegroundWindow 返回，必须单独判掉。"""
    if not hwnd:
        return True
    return bool(win32gui.IsIconic(hwnd))


def top_level_window_pids(visible_only: bool = True) -> set:
    """枚举顶层窗口的进程 PID，用于构造后台采样候选集合。"""
    found: set = set()

    def _cb(hwnd, _lparam):
        try:
            if visible_only and not win32gui.IsWindowVisible(hwnd):
                return True
            pid = pid_of_window(hwnd)
            if pid:
                found.add(pid)
        except Exception:
            # 按窗口逐个跳过：某一个窗口查不到不影响其它窗口
            pass      # nosec B110
        return True

    # 回调必须保活到 EnumWindows 返回，否则 ctypes 释放后可能崩
    callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)(_cb)
    try:
        _user32.EnumWindows(callback, 0)
    except Exception:
        # 失败时退化为「候选集合为空」→ 只是不采后台时长，前台统计不受影响。
        # 这是可接受的降级，且不会污染任何数据（R8 允许"可解释的降级"）。
        pass      # nosec B110
    return found


# ---------------------------------------------------------------- 进程信息


def process_image_path(pid: int) -> Optional[str]:
    """取进程可执行文件全路径。

    只请求 PROCESS_QUERY_LIMITED_INFORMATION —— 官方文档与实测都确认：
    这样**不需要管理员权限**也能读到以管理员身份运行的进程的路径。
    返回 None 表示系统拒绝访问（→ 该程序归入「不可监测」，属正常结果不是 bug）。
    """
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        size = w.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value or None
    finally:
        _kernel32.CloseHandle(handle)


def file_version_info(path: str) -> dict:
    """读取 exe 的版本资源（只读文件元数据，不读取任何内容型数据）。"""
    out = {"display_name": "", "version": "", "publisher": ""}
    try:
        info = win32api.GetFileVersionInfo(path, "\\")
        lang, codepage = win32api.GetFileVersionInfo(
            path, "\\VarFileInfo\\Translation"
        )[0]
        prefix = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\"
        for key, field in (
            ("FileDescription", "display_name"),
            ("FileVersion", "version"),
            ("CompanyName", "publisher"),
        ):
            try:
                out[field] = str(win32api.GetFileVersionInfo(path, prefix + key) or "")
            except Exception:
                # 某些 exe 本来就没有这个版本字段（很正常），留空即可
                pass      # nosec B110
    except Exception:
        # 整个文件没有版本资源也很常见（绿色软件、自编译的），留空即可
        pass      # nosec B110
    return out


def self_integrity_rid() -> Optional[int]:
    """本进程令牌的完整性级别 RID：Medium(0x2000)=普通用户，High(0x3000)=已提权。

    只用于自证「本程序没有提权」（audit 第 6 项），因此只查自己，不做通用版。
    """
    token = w.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        return None
    try:
        size = w.DWORD(0)
        _advapi32.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetTokenInformation(
            token, TOKEN_INTEGRITY_LEVEL, buf, size, ctypes.byref(size)
        ):
            return None
        # TOKEN_MANDATORY_LABEL 的结构就是一个 SID 指针
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        if not sid:
            return None
        count_ptr = _advapi32.GetSidSubAuthorityCount(sid)
        if not count_ptr:
            return None
        last = ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_ubyte)).contents.value - 1
        value_ptr = _advapi32.GetSidSubAuthority(sid, last)
        if not value_ptr:
            return None
        return int(ctypes.cast(value_ptr, ctypes.POINTER(ctypes.c_ulong)).contents.value)
    finally:
        _kernel32.CloseHandle(token)


def current_user_sid() -> str:
    """当前用户的 SID 字符串（如 S-1-5-21-...）。

    用途：命名互斥体加用户后缀，避免多用户环境下互相抢锁或被别人抢占。
    """
    token = w.HANDLE()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        return ""
    try:
        size = w.DWORD(0)
        _advapi32.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
        if not size.value:
            return ""
        buf = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetTokenInformation(
            token, TOKEN_USER, buf, size, ctypes.byref(size)
        ):
            return ""
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        text = ctypes.c_wchar_p()
        _advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)
        ]
        if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            return ""
        value = text.value or ""
        _kernel32.LocalFree(text)
        return value
    except Exception:
        return ""
    finally:
        _kernel32.CloseHandle(token)


# ---------------------------------------------------------------- 输入空闲


def idle_milliseconds() -> int:
    """距上次用户输入的空闲毫秒数（只取一个数字，不含任何输入内容）。"""
    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", w.UINT), ("dwTime", w.DWORD)]

    info = _LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not _user32.GetLastInputInfo(ctypes.byref(info)):
        return 0
    # GetTickCount 32 位会回绕，用掩码保证差值正确
    now = _kernel32.GetTickCount() & 0xFFFFFFFF
    return int((now - info.dwTime) & 0xFFFFFFFF)


# ---------------------------------------------------------------- 自身内存/优先级


def set_below_normal_priority() -> bool:
    """降低自身调度优先级，不与前台程序抢 CPU。"""
    return bool(
        _kernel32.SetPriorityClass(_kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
    )


def empty_working_set() -> bool:
    """把不常用页换出，压低常驻内存。只在写完库之后调用。"""
    return bool(_psapi.EmptyWorkingSet(_kernel32.GetCurrentProcess()))


# ---------------------------------------------------------------- 互斥体探测


def mutex_alive(name: str) -> bool:
    """探测同名命名互斥体是否已存在（用于统计窗口判断守护进程在不在跑）。

    用 OpenMutexW 而不是 CreateMutexW：只探测、绝不创建，
    这样探测失败不会留下一个"假存活"的互斥体误导下一次探测。
    """
    SYNCHRONIZE = 0x00100000
    ERROR_FILE_NOT_FOUND = 2
    handle = _kernel32.OpenMutexW(SYNCHRONIZE, False, name)
    if handle:
        _kernel32.CloseHandle(handle)
        return True
    return ctypes.get_last_error() != ERROR_FILE_NOT_FOUND


# ---------------------------------------------------------------- 开机自启（v0.2 B2）
# R6 红线的落点：只写 HKCU 当前用户的 Run 项、默认不写、只在用户于统计窗口
# 主动勾选时写入、取消勾选即删除。打包 exe 与源码运行自适应两种命令。

AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "UsageWatcher"


def autostart_command() -> str:
    """自启命令自适应：打包 exe 用自身路径；源码运行用 pythonw + app.py。"""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    base = os.path.dirname(os.path.abspath(__file__))
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    return '"%s" "%s"' % (pythonw, os.path.join(base, "app.py"))


def autostart_enabled() -> bool:
    """查询当前用户是否已设置开机自启（只读 HKCU，不碰其它任何键）。"""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, AUTOSTART_NAME)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """写/删 HKCU Run 项。失败返回 False（调用方必须把 UI 状态恢复并提示）。"""
    import winreg
    try:
        if enabled:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY,
                                    0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ,
                                  autostart_command())
        else:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY,
                                0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, AUTOSTART_NAME)
        return True
    except OSError:
        return False


def revive_window(title: str) -> bool:
    """按标题找窗口并恢复置前；找不到（无窗/标题语言差异）安静返回 False。

    BUG-35：统计窗口最小化后再次双击快捷方式，GUI 单实例进程会静默退出、
    用户毫无感知 —— 退出前先调用本函数把已有窗口唤回来。
    句柄类 API 必须声明 argtypes/restype（64 位截断红线，本项目踩过 3 次）。
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE：最小化先恢复
    user32.SetForegroundWindow(hwnd)
    return True
