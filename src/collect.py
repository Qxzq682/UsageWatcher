"""采集层：前台窗口变化。零 Qt 依赖。

分层（严格单向，见 重点提示清单 §7）：
    collect → apps → track → data → ui_*

本模块只负责"当前谁在前台"这一个事实，不做业务判断、不写数据库、不解析应用身份。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import queue
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

import psutil

import apps
import audio
import config
import winutil

# ---------------------------------------------------------------- 契约

# 跨模块传递的扁平结构。契约稳定，内部实现随便改（见清单 A2）。
# 这里刻意不存窗口标题 —— 标识应用靠 exe 路径，不靠标题（隐私红线）。


@dataclass
class Sample:
    """一次采样。

    kind 由消费它的 tracker 决定：
      · FgWatcher 产出的 Sample → 前台片段（active 恒为 True）
      · BgSampler 产出的 Sample → 后台片段（active 表示"确有真实活动"）
    """

    ts_ms: int          # epoch 毫秒
    pid: int            # 0 表示当前锁屏 / 无前台窗口
    from_event: bool    # True=WinEvent 事件驱动（毫秒级）；False=轮询/采样驱动
    active: bool = True  # 仅后台采样使用：CPU 或磁盘 I/O 超过阈值


# ---------------------------------------------------------------- WinEvent 钩子

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_WinEventProc = ctypes.WINFUNCTYPE(
    None, w.HANDLE, w.DWORD, w.HWND, w.LONG, w.LONG, w.DWORD, w.DWORD
)

_user32.SetWinEventHook.restype = w.HANDLE
_user32.SetWinEventHook.argtypes = [
    w.DWORD,
    w.DWORD,
    w.HANDLE,
    _WinEventProc,
    w.DWORD,
    w.DWORD,
    w.DWORD,
]
_user32.UnhookWinEvent.argtypes = [w.HANDLE]
_kernel32.GetTickCount.restype = w.DWORD


def _tick_to_epoch_ms(tick_ms: int) -> int:
    """把 dwmsEventTime（开机以来的毫秒）换算成 epoch 毫秒。

    事件时间比"回调被派发的时刻"更准（消息可能被排队延迟），所以值得换算。
    """
    now_tick = _kernel32.GetTickCount() & 0xFFFFFFFF
    delta = (now_tick - int(tick_ms)) & 0xFFFFFFFF
    if delta > 60_000:      # 明显不合理（时钟/回绕异常）→ 退回当前时间
        return int(time.time() * 1000)
    return int(time.time() * 1000) - delta


class FgWatcher:
    """前台窗口监视器：事件驱动为主。

    ⚠ 关键约束：`install()` 必须在**有消息循环的线程**上调用，
    且该线程要持续调用 `winutil.pump_messages()`，否则 OUTOFCONTEXT 回调不会被派发。
    """

    def __init__(self, on_change: Callable[[Sample], None]) -> None:
        self._on_change = on_change
        self._hook = None
        self._proc = None      # 必须保活！GC 掉回调会让进程崩溃（实测坑）
        self._last_pid = -1
        self.idle_cut = False  # 空闲闩：True=已因无输入截断前台（见 idle_gate）
        # 用 Queue 而不是 list：回调在消息循环线程写，采集线程读
        self.pending: "queue.Queue" = queue.Queue()

    # ---- 事件回调（运行在注册线程的消息循环里，务必保持极短） ----

    def _callback(self, _hook, _event, hwnd, _obj, _child, _thread, event_tick) -> None:
        try:
            ts = _tick_to_epoch_ms(event_tick)
            # 最小化瞬间事件先到、窗口已 IsIconic：不判会把"最小化"记成前台切换（BUG-15）
            pid = winutil.pid_of_window(hwnd) if hwnd and not winutil.is_minimized(hwnd) else 0
            # 同步给轮询用，否则轮询会把同一个 PID 再报一次，
            # 并把本可精确的边界错误标成 FLAG_GUESS
            self._last_pid = pid
            self.pending.put(Sample(ts_ms=ts, pid=pid, from_event=True))
        except Exception:
            # 回调里绝不能抛异常穿过 ctypes 边界，否则进程直接挂。
            # 这是刻意的整体兜底：这里丢的只是"一次切换事件"，不涉及数据正确性。
            pass      # nosec B110

    def drain(self) -> List[Sample]:
        """取出当前所有待处理事件（供采集线程调用）。"""
        out: List[Sample] = []
        while True:
            try:
                out.append(self.pending.get_nowait())
            except queue.Empty:
                return out

    def samples(self) -> List[Sample]:
        """取事件样本供消费；空闲闩合上时事件照收但不判前台。

        人已离开，期间 toast/弹窗抢到的前台不算"使用"（BUG-32），
        丢弃即可——恢复输入后轮询会重新上报真实前台。
        """
        if self.idle_cut:
            self.drain()
            return []
        return self.drain()

    def install(self) -> bool:
        self._proc = _WinEventProc(self._callback)
        self._hook = _user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            0,
            self._proc,
            0,
            0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
        )
        return bool(self._hook)

    def uninstall(self) -> None:
        if self._hook:
            _user32.UnhookWinEvent(self._hook)
            self._hook = None

    def forget(self) -> None:
        """忘掉"上次见到的前台 PID"。

        唤醒/解锁后必须调用：休眠期间前台程序可能已经换了，
        不重置的话轮询会以为"没有变化"，白白丢掉一段真实时长。
        """
        self._last_pid = -1

    def idle_gate(self, now_ms: int, close: Callable[[int, int], list]) -> list:
        """空闲截断闸门：无输入超过 FG_IDLE_CUTOFF_S 就闭合在开前台段。

        锁屏/休眠有 force_close 收尾，但"不锁屏直接走人"没有——离开前的
        窗口会一直占着前台，整段 AFK 时间被记成该应用的使用时长
        （BUG-32：QQ 被记 95 分钟）。进入空闲时闭合并 forget()，恢复输入
        后轮询会重新上报当时的前台（新段，不与 AFK 前的旧段相连）。
        close 由调用方注入（track.FgTracker.force_close），collect 不反向
        依赖 track。返回需要落库的闭合片段（可能为空）。
        """
        idle = winutil.idle_milliseconds() >= config.FG_IDLE_CUTOFF_S * 1000
        if idle and self._last_pid in audio.recently_audible_pids():
            idle = False    # 前台应用自己在出声（视频/会议）：人可能还在看，豁免（v0.2 A1）
        if idle == self.idle_cut:
            return []
        self.idle_cut = idle
        if idle:
            self.forget()
            return close(now_ms, config.FLAG_IDLE)
        return []

    # ---- 轮询兜底（UIPI 下某些高完整性进程可能收不到事件） ----

    def poll(self) -> Optional[Sample]:
        """返回一个新的 Sample（前台 PID 有变化时），无变化返回 None。

        最小化的窗口仍可能被 GetForegroundWindow 返回，必须判掉（实测坑）。
        空闲闩合上期间恒返回 None：人已离开，不能把离开前的窗口重新
        报成"当前前台"，否则闭合→重开的循环会每 0.2s 造一个碎片段。
        """
        if self.idle_cut:
            return None
        hwnd = winutil.foreground_hwnd()
        pid = 0
        if hwnd and not winutil.is_minimized(hwnd):
            pid = winutil.pid_of_window(hwnd)
        if pid == self._last_pid:
            return None
        self._last_pid = pid
        return Sample(ts_ms=int(time.time() * 1000), pid=pid, from_event=False)


# ---------------------------------------------------------------- 阻塞循环


# ---------------------------------------------------------------- 后台采样


class BgSampler:
    """后台活跃采样器。

    候选集合只包含"用户可见软件"：
        有可见顶层窗口的进程 ∪ 已知应用的其它同 exe 进程
    这样 svchost / 服务类进程天然被排除，不会把系统组件算成"在用的软件"。

    ⚠ 两个必须遵守的契约：
    1. `sample()` 要是**连续调用**的 —— `cpu_percent(interval=None)` 返回的是
       "自上次调用以来的均值"，不连续调用得到的数字毫无意义。
    2. 新进程第一轮**只建基线、不判定**（首次调用必返回 0.0，是实测已知坑）。
    """

    def __init__(self, resolver: apps.Resolver) -> None:
        self._resolver = resolver
        self._procs: dict = {}          # pid -> 采样基线
        self._candidates: list = []
        self._last_rebuild = 0.0
        self._last_sample = 0.0
        self._audible: set = set()      # 本轮正在出声的 pid（v0.2 A1 第三判活指标）
        # R8：采集失败必须显式计数并能在 UI 看到，不许静默当成"没有活动"
        self.error_count = 0

    # ---- 采样节奏（空闲时自适应降频，省 CPU） ----

    def interval(self) -> float:
        if winutil.idle_milliseconds() >= config.IDLE_DOWNGRADE_AFTER_S * 1000:
            return config.BG_SAMPLE_INTERVAL_IDLE_S
        return config.BG_SAMPLE_INTERVAL_S

    def due(self, now_mono: float) -> bool:
        return (now_mono - self._last_sample) >= self.interval()

    # ---- 候选集合 ----

    def _rebuild_candidates(self, known_exes: set) -> None:
        visible = winutil.top_level_window_pids(visible_only=True)
        foreground = winutil.pid_of_window(winutil.foreground_hwnd())
        others: list = []
        for proc in psutil.process_iter(["pid"]):
            pid = proc.info["pid"]
            if pid in visible or pid == foreground:
                continue
            path = winutil.process_image_path(pid)
            if path and path.lower() in known_exes:
                others.append(pid)

        # 音频会话里的 pid 直接进候选（v0.2 A1）：出声进程哪怕无窗口、
        # 不在已知列表（如临时脚本播音），其时长也该被归因记录
        audible = audio.active_pids()

        # 去重 + 排除自身：前台 PID 常常同时也在 visible 里，
        # 重复进候选会让同一个进程一轮被采样多次 → 第二次起基线被打乱，
        # 于是出现"判为活跃但 cpu=0.00%"这种自相矛盾的结果（实测踩过）
        self_pid = os.getpid()
        ordered: list = []
        seen: set = {self_pid}
        for pid in ([foreground] if foreground else []) + sorted(visible) \
                + sorted(others) + sorted(audible):
            if pid and pid not in seen:
                seen.add(pid)
                ordered.append(pid)

        # 超上限时按"前台优先 → 有窗口 → 已知应用"的顺序截断
        self._candidates = ordered[: config.MAX_CANDIDATES]
        self._last_rebuild = time.monotonic()

    # ---- 采样 ----

    def sample(self, now_ms: int, known_exes: Optional[set] = None) -> tuple:
        """跑一轮采样。返回 (samples, exited_pids)。"""
        now_mono = time.monotonic()
        if now_mono - self._last_rebuild >= config.CANDIDATE_REBUILD_S:
            try:
                self._rebuild_candidates(known_exes or set())
            except Exception:
                self.error_count += 1
        self._audible = audio.active_pids()   # 本轮出声 pid（v0.2 A1）
        # 先取上一次的采样时刻再更新，否则差值为 0 → IO 速率会被算成虚高
        previous_mono = self._last_sample
        self._last_sample = now_mono
        elapsed = (now_mono - previous_mono) if previous_mono else self.interval()
        elapsed = max(elapsed, 0.001)

        samples: list = []
        exited: list = []
        alive: set = set()
        for pid in self._candidates:
            try:
                sample = self._sample_one(pid, now_ms, elapsed)
            except psutil.NoSuchProcess:
                exited.append(pid)
                self._procs.pop(pid, None)
                continue
            except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
                self.error_count += 1
                self._procs.pop(pid, None)
                continue
            if sample is not None:
                samples.append(sample)
                alive.add(pid)
        for pid in list(self._procs):
            if pid not in alive and pid not in self._candidates:
                self._procs.pop(pid, None)
        return samples, exited

    def _sample_one(self, pid: int, now_ms: int, elapsed: float) -> Optional[Sample]:
        record = self._procs.get(pid)
        if record is None:
            proc = psutil.Process(pid)
            if not proc.is_running():
                raise psutil.NoSuchProcess(pid)
            # 首轮只建基线，不判定（没有上一次读数就算不出速率）
            self._procs[pid] = {
                "proc": proc,
                "create_time": proc.create_time(),
                "cpu": _cpu_total(proc),
                "io": _io_total(proc),
                "warm": True,
            }
            return None

        proc = record["proc"]
        # 必须先判存活：进程退出后 create_time() 不会抛异常（实测），
        # 只靠异常捕获会让死进程的记录一直赖着，被反复记成"不活跃"
        try:
            if not proc.is_running():
                raise psutil.NoSuchProcess(pid)
            if abs(proc.create_time() - record["create_time"]) > 0.01:
                raise psutil.NoSuchProcess(pid)   # PID 被复用 → 当作新进程
        except psutil.Error:
            raise psutil.NoSuchProcess(pid)

        # 用**累计计数差分**而不是 cpu_percent(interval=None)：
        # 后者是"自上次调用以来的均值"，任何第三方调用都会偷走基线、
        # 让下一次读数变成接近 0（实测踩过）。累计计数没有这个隐患。
        cpu_now = _cpu_total(proc)
        io_now = _io_total(proc)
        cpu_pct = (cpu_now - record["cpu"]) / elapsed * 100
        io_rate = max(io_now - record["io"], 0) / elapsed
        record["cpu"] = cpu_now
        record["io"] = io_now
        record["last_cpu_pct"] = cpu_pct
        record["last_io_rate"] = io_rate

        if record["warm"]:
            record["warm"] = False      # 这一轮才有可用的差分窗口
            return None

        active = (cpu_pct >= config.CPU_ACTIVE_PCT or io_rate >= config.IO_ACTIVE_BPS
                  or pid in self._audible)   # 在出声也判活跃（在线音乐盲区，v0.2 A1）
        return Sample(ts_ms=now_ms, pid=pid, from_event=False, active=active)

    # ---- 唤醒/解锁后必须重置（否则休眠期间的计数差会造出整段虚高） ----

    def reset_baselines(self) -> None:
        self._procs.clear()

    def rates(self, pid: int) -> tuple:
        """仅供标定工具显示用；不影响判定。"""
        record = self._procs.get(pid)
        if not record:
            return (0.0, 0.0)
        return (record.get("last_cpu_pct", 0.0), record.get("last_io_rate", 0.0))


def _cpu_total(proc) -> float:
    """累计 CPU 时间（用户 + 内核），单位秒。"""
    times = proc.cpu_times()
    return float(times.user + times.system)


def _io_total(proc) -> int:
    """累计进程 I/O 字节（后台判活口径）。

    注意（v0.4 措辞修正）：Windows 的 io_counters().read_bytes **包含网络
    收发字节**（socket 收发被计入 ReadTransfer，psutil 没有单进程网络计数）。
    所以判活阈值实际是"进程总 IO（磁盘+网络）"——Node/Electron 类进程
    走网络时也会被判活跃（实测：dsh ego-cast 经 CDP 20fps 截屏案，09-29）。
    """
    counters = proc.io_counters()
    return int(counters.read_bytes + counters.write_bytes)
