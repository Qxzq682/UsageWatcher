"""隐藏消息窗口 + 原生托盘图标。零 Qt 依赖。

为什么不用 Qt 做托盘：Qt 一加载就是 24MB（实测）。托盘图标只需要
Shell_NotifyIcon + 一个隐藏窗口，用 Win32 直接做，常驻内存能省一大截。

这个隐藏窗口同时也是**消息汇聚点**：
  · WinEvent 前台回调靠它所在线程的消息循环派发
  · 电源/会话通知（休眠、锁屏）也投递到这里
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
from typing import Callable, Dict, List, Optional

import i18n
import sysmsg
import winutil

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0x0, 0x1, 0x2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED = 0x0, 0x800, 0x8, 0x1
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
IDI_APPLICATION = 32512
_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM
)


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
        ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT), ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT), ("hIcon", w.HICON), ("szTip", ctypes.c_wchar * 128),
        ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
        ("szInfo", ctypes.c_wchar * 256), ("uVersion", w.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64), ("dwInfoFlags", w.DWORD),
        ("guidItem", _GUID), ("hBalloonIcon", w.HICON),
    ]


_u32.CreateWindowExW.restype = w.HWND
# 句柄类型参数必须逐个声明 argtypes：不声明时 ctypes 按 32 位 int 传参，
# 64 位下句柄会被截断 → 调用静默失败或直接 OverflowError（本项目已踩 3 次）
_u32.CreateWindowExW.argtypes = [
    w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p,
]
_u32.RegisterClassExW.restype = w.USHORT
_u32.RegisterClassExW.argtypes = [ctypes.c_void_p]
_u32.CreatePopupMenu.restype = w.HMENU
_u32.CreatePopupMenu.argtypes = []
_u32.LoadIconW.restype = w.HICON
_u32.LoadIconW.argtypes = [w.HINSTANCE, ctypes.c_wchar_p]
_u32.DefWindowProcW.restype = ctypes.c_ssize_t
_u32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
_u32.SendMessageW.restype = ctypes.c_ssize_t
# 句柄类型的参数必须显式声明：不声明时 ctypes 按 32 位 int 传参，
# 64 位下句柄会被截断，调用静默失败（本项目实测踩过）
_u32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, ctypes.c_wchar_p]
_u32.DestroyMenu.argtypes = [w.HMENU]
_u32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, w.HWND, ctypes.c_void_p]
_u32.TrackPopupMenu.restype = w.UINT
_u32.SetForegroundWindow.argtypes = [w.HWND]
_u32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
_u32.DestroyWindow.argtypes = [w.HWND]
_u32.PostQuitMessage.argtypes = [ctypes.c_int]
_u32.RegisterWindowMessageW.restype = w.UINT
_u32.RegisterWindowMessageW.argtypes = [ctypes.c_wchar_p]
_k32.GetModuleHandleW.restype = w.HINSTANCE
_k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
_shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)]


class ShellWindow:
    """隐藏窗口：承载托盘图标，并作为所有消息的汇聚点。"""

    _CLASS_NAME = "UsageWatcherShellWnd"

    def __init__(self) -> None:
        self.hwnd: int = 0
        self._proc = None                      # WNDPROC 必须保活，否则崩
        self._handlers: Dict[int, List[Callable]] = {}
        self.taskbar_created = int(_u32.RegisterWindowMessageW("TaskbarCreated"))

    def on(self, message: int, handler: Callable) -> None:
        self._handlers.setdefault(message, []).append(handler)

    def dispatch(self, message: int, wparam: int, lparam: int) -> bool:
        for handler in self._handlers.get(message, []):
            try:
                handler(wparam, lparam)
            except Exception:
                # 刻意的：单个消息处理器出错不能带走整个消息循环，
                # 否则托盘菜单与窗口事件会全线失效，比丢一次点击严重得多
                pass      # nosec B110
        return bool(self._handlers.get(message))

    # ---- 创建 / 销毁 ----

    def create(self) -> bool:
        self._proc = _WNDPROC(self._wndproc)
        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = _k32.GetModuleHandleW(None)
        wc.lpszClassName = self._CLASS_NAME
        if not _u32.RegisterClassExW(ctypes.byref(wc)):
            return False
        # message-only window（HWND_MESSAGE = -3）：不显示、不占任务栏
        self.hwnd = _u32.CreateWindowExW(
            0, self._CLASS_NAME, "UsageWatcher", 0, 0, 0, 0, 0,
            w.HWND(-3), 0, wc.hInstance, 0)
        return bool(self.hwnd)

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == self.taskbar_created and self.taskbar_created:
            self.dispatch(self.taskbar_created, wparam, lparam)
        elif message == WM_DESTROY:
            _u32.PostQuitMessage(0)
        else:
            self.dispatch(message, wparam, lparam)
        return _u32.DefWindowProcW(hwnd, message, wparam, lparam)

    def destroy(self) -> None:
        if self.hwnd:
            _u32.DestroyWindow(self.hwnd)
            self.hwnd = 0

    # ---- 消息循环（同时派发 WinEvent 回调） ----

    def run(self, on_tick: Callable[[], None], should_stop: Callable[[], bool],
            tick_ms: int = 1000) -> None:
        # 委托给 sysmsg.pump_loop —— 全项目只有这一份消息循环实现
        sysmsg.pump_loop(on_tick, should_stop, tick_ms)

    def post_quit(self) -> None:
        _u32.PostQuitMessage(0)


class TrayIcon:
    """原生托盘图标 + 弹出菜单。

    `menu_provider()` 由 app.py 提供，返回 [(id, 文本, 标志位 或 None表示分隔线)]。
    回调通过 ShellWindow 的 WM_COMMAND 处理器走，托盘本身不关心业务。
    """

    UID = 1

    def __init__(self, window: ShellWindow, menu_provider: Callable[[], list]) -> None:
        if not window.hwnd:
            raise RuntimeError("必须先创建 ShellWindow 再创建托盘图标")
        self._window = window
        self._menu_provider = menu_provider
        self._visible = False
        self._icon = _u32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))
        self._data = _NOTIFYICONDATAW()
        self._data.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        self._data.hWnd = window.hwnd
        self._data.uID = self.UID
        self._data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._data.uCallbackMessage = WM_TRAYICON
        self._data.hIcon = self._icon
        # 资源管理器重启后托盘会被清空，必须响应 TaskbarCreated 重新注册
        window.on(window.taskbar_created, lambda *_: self.show())
        window.on(WM_TRAYICON, self._on_tray_message)

    def set_tooltip(self, text: str) -> None:
        self._data.szTip = text[:127]
        if self._visible:
            _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._data))

    def show(self) -> bool:
        ok = bool(_shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._data)))
        self._visible = ok
        return ok

    def hide(self) -> None:
        if self._visible:
            _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data))
            self._visible = False

    def _on_tray_message(self, wparam: int, lparam: int) -> None:
        # lparam 低字是鼠标消息：WM_LBUTTONUP=0x0202, WM_RBUTTONUP=0x0205
        low = lparam & 0xFFFF
        if low == 0x0202:
            self._window.dispatch(WM_COMMAND, Options.OPEN, 0)
        elif low == 0x0205:
            self.popup()

    def popup(self) -> None:
        menu = _u32.CreatePopupMenu()
        if not menu:
            return
        for item_id, label, flags in self._menu_provider():
            if flags is None:
                _u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            else:
                _u32.AppendMenuW(menu, MF_STRING | flags, item_id, label)
        # 必须先置前台，否则菜单弹出后点别处不会消失
        _u32.SetForegroundWindow(self._window.hwnd)
        point = w.POINT()
        _u32.GetCursorPos(ctypes.byref(point))
        chosen = _u32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y,
            0, self._window.hwnd, None)
        _u32.DestroyMenu(menu)
        if chosen:
            self._window.dispatch(WM_COMMAND, chosen, 0)


class Options:
    """菜单项 id 与显示文本。"""

    OPEN = 1001
    PAUSE = 1002
    QUIT = 1003

    @staticmethod
    def build(paused: bool, today_fg: str, today_bg: str) -> list:
        return [
            (0, i18n.t("tray_summary", today_fg, today_bg), MF_GRAYED),
            (0, "", None),
            (Options.OPEN, i18n.t("tray_open"), 0),
            (Options.PAUSE, i18n.t("tray_resume" if paused else "tray_pause"),
             MF_CHECKED if paused else 0),
            (0, "", None),
            (Options.QUIT, i18n.t("tray_quit"), 0),
        ]
