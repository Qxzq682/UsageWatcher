"""Win32 消息循环与系统状态事件（电源 / 会话）。零 Qt 依赖。

单独成文件的原因：这是「消息」，和 winutil 的「查询系统信息」是两件事。
放一起会让两个文件都变胖、职责变糊。

关键机制：WinEvent 钩子用 WINEVENT_OUTOFCONTEXT 时，回调靠**线程消息队列**派发，
所以注册钩子的线程必须持续抽消息，否则回调永远不来。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
from typing import Callable, Optional

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", w.HWND),
        ("message", w.UINT),
        ("wParam", w.WPARAM),
        ("lParam", w.LPARAM),
        ("time", w.DWORD),
        ("pt_x", w.LONG),
        ("pt_y", w.LONG),
    ]


PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF

_user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), w.HWND, w.UINT, w.UINT, w.UINT]
_user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
_user32.PostQuitMessage.argtypes = [ctypes.c_int]
_user32.MsgWaitForMultipleObjects.argtypes = [w.DWORD, ctypes.c_void_p, w.BOOL, w.DWORD, w.DWORD]
_wtsapi32.WTSRegisterSessionNotification.argtypes = [w.HWND, w.DWORD]
_wtsapi32.WTSUnRegisterSessionNotification.argtypes = [w.HWND]


def pump_messages() -> int:
    """抽干并派发当前待处理消息，返回处理的条数。"""
    msg = MSG()
    count = 0
    while _user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE):
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))
        count += 1
    return count


def wait_for_message(timeout_ms: int) -> None:
    """阻塞直到有消息到达或超时（无消息时几乎不耗 CPU）。

    句柄列表传空 => 只等消息 + 超时，正好是我们要的语义。
    """
    _user32.MsgWaitForMultipleObjects(0, None, False, int(timeout_ms), QS_ALLINPUT)


def pump_loop(on_tick: Callable[[], None], should_stop: Callable[[], bool],
              tick_ms: int = 1000) -> None:
    """带消息派发的阻塞循环。全项目只有这一份实现（别再写第二份）。"""
    while not should_stop():
        pump_messages()
        on_tick()
        if should_stop():
            break
        wait_for_message(tick_ms)


# ---------------------------------------------------------------- 电源 / 会话

WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1

PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012

WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
WTS_CONSOLE_DISCONNECT = 0x1
WTS_CONSOLE_CONNECT = 0x2
WTS_REMOTE_DISCONNECT = 0x3
WTS_REMOTE_CONNECT = 0x4

NOTIFY_FOR_THIS_SESSION = 0


class PowerWatcher:
    """把 Win32 的电源/会话消息翻译成 4 个语义事件。

    为什么必须处理：休眠 8 小时期间进程还活着，但 CPU/IO 计数不增长。
    不暂停并在唤醒后重置基线，那 8 小时会被算成"后台活跃"，
    而且唤醒后第一次差分会把整段差额算进一个采样周期 → 巨值。
    """

    def register(self, hwnd: int) -> bool:
        try:
            return bool(_wtsapi32.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION))
        except Exception:
            return False

    def unregister(self, hwnd: int) -> None:
        try:
            _wtsapi32.WTSUnRegisterSessionNotification(hwnd)
        except Exception:
            # 注销失败只是少收一个通知，且此时进程正在退出，不值得让退出流程失败
            pass      # nosec B110

    @staticmethod
    def interpret(message: int, wparam: int) -> Optional[str]:
        """返回 'sleep' / 'resume' / 'lock' / 'unlock'，无关消息返回 None。"""
        if message == WM_POWERBROADCAST:
            if wparam == PBT_APMSUSPEND:
                return "sleep"
            if wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                return "resume"
            return None
        if message == WM_WTSSESSION_CHANGE:
            if wparam in (WTS_SESSION_LOCK, WTS_CONSOLE_DISCONNECT, WTS_REMOTE_DISCONNECT):
                return "lock"
            if wparam in (WTS_SESSION_UNLOCK, WTS_CONSOLE_CONNECT, WTS_REMOTE_CONNECT):
                return "unlock"
            return None
        return None
