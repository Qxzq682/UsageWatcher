"""程序入口。三种模式（命令行参数分流）：无参数 = 采集守护进程（不加载 PySide6，
常驻约 35MB）；--gui = 统计窗口（独立进程，关窗即退出）；--smoke = 自检（自动退出）。
主线程跑 Win32 消息循环（回调/托盘/电源通知），工作线程采集与写库，UI 永不被 I/O 卡住。
"""

from __future__ import annotations

import ctypes
import os
import subprocess   # nosec B404 —— 只用于以 shell=False、字面量参数启动本程序自己的 --gui 模式
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apps      # noqa: E402
import collect   # noqa: E402
import config    # noqa: E402
import data      # noqa: E402
import track     # noqa: E402
import sysmsg    # noqa: E402
import tray      # noqa: E402
import winutil   # noqa: E402

_MUTEX_NAME = "Global\\UsageWatcher_SingleInstance_"
_mutex_handles: list = []      # 互斥体句柄要保活，进程退出由系统回收


# ---------------------------------------------------------------- 单实例

def _acquire_mutex(name: str) -> bool:
    """创建命名互斥体，返回是否为新实例；创建失败就不拦（宁多开不开不了）。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True                     # 创建失败就不拦，宁可多开也不要开不了
    _mutex_handles.append(handle)
    return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS


def acquire_single_instance() -> bool:
    return _acquire_mutex(_MUTEX_NAME + _user_sid())


# ---------------------------------------------------------------- 守护进程

class Daemon:
    """采集守护。主线程跑消息循环，工作线程采集。"""

    def __init__(self) -> None:
        self.stop_event = threading.Event()
        # 暂停状态必须从配置恢复：用户按下暂停的意图不得被重启静默撤销（BUG-4）
        self.paused = bool(config.load().get("paused", False))
        self.paused_by_power = False
        self.store = data.Store()
        self.resolver = apps.Resolver()
        self.fg = collect.FgWatcher(lambda _s: None)
        self.sampler = collect.BgSampler(self.resolver)
        self.fg_tracker = track.FgTracker()
        self.bg_tracker = track.BgTracker()
        self.power = sysmsg.PowerWatcher()
        self.window = tray.ShellWindow()
        self.icon = None
        self._pending: list = []
        self._pending_lock = threading.Lock()   # 主线程/工作线程都写 _pending（BUG-9）
        self._last_trim, self._last_housekeep = 0.0, 0.0
        self._last_flush = self._last_maint = time.monotonic()   # maint: 启动已跑过一次
        self._enabled: dict = {}
        self._enabled_at = 0.0
        self._cap_written: set = set()      # 结论只写一次，防覆盖 UI 的重置（BUG-6）
        self._recheck_pending: set = set()  # 等待重探的应用；采样到即强制重写结论
        self._known_exes: set = set()
        self._known_at, self._worker = 0.0, None

    # ---------------------------------------------------- 生命周期

    def start(self) -> bool:
        # 清掉残留的停止标志：否则上一轮没消费的标志会让刚启动的进程立刻自杀
        try:
            config.stop_flag_path().unlink(missing_ok=True)
        except OSError:
            pass
        if not self.window.create():
            print("[x] 创建隐藏消息窗口失败")
            return False
        self.icon = tray.TrayIcon(self.window, self._menu_items)
        self.icon.show()
        if self.paused:
            self.icon.set_tooltip("已暂停统计")   # 恢复的暂停状态要立刻可见
        self.fg.install()
        self.power.register(self.window.hwnd)
        self.window.on(tray.WM_COMMAND, self._on_command)
        # 消息 id 绑进 lambda，避免处理器里再猜"这条消息是哪一个"
        self.window.on(sysmsg.WM_POWERBROADCAST, lambda wp, lp: self._on_power(sysmsg.WM_POWERBROADCAST, wp))
        self.window.on(sysmsg.WM_WTSSESSION_CHANGE, lambda wp, lp: self._on_power(sysmsg.WM_WTSSESSION_CHANGE, wp))
        self.store.maintenance()
        self._worker = threading.Thread(target=self._loop, name="collector", daemon=True)
        self._worker.start()
        return True
    def run(self) -> int:
        if not self.start():
            return 1
        self.window.run(on_tick=self._housekeeping, should_stop=self.stop_event.is_set,
                        tick_ms=500)
        self.shutdown()
        return 0

    def _housekeeping(self) -> None:
        """60s 节流的低频杂务：tooltip 刷新（第 7 步）+ 异常可见（R8）+ event 周期裁剪。"""
        now = time.monotonic()
        if now - self._last_housekeep < 60.0:
            return
        self._last_housekeep = now
        if now - self._last_maint >= 86400.0:    # maintenance 的 event 裁剪每 24h
            self._last_maint = now
            self.store.maintenance()
        if not self.icon:
            return
        if self.paused:
            self.icon.set_tooltip("已暂停统计")
            return
        d = track.day_of(_now_ms()); s = self.store.totals_summary(d, d)
        tip = "%s\n今日前台 %s · 后台 %s" % (config.APP_TITLE, _fmt_duration(s["fg_ms"]), _fmt_duration(s["bg_ms"]))
        if self.store.error_count:
            tip += "\n[!] 采集异常 %d 次" % self.store.error_count
        self.icon.set_tooltip(tip)

    def stop_and_flush(self) -> None:
        """停采集 + 等工作线程收尾 + 先闭合还开着的片段再落库（不闭合会丢
        "用户正在用的那一段"，自检表现为"记录 0 个应用"）。"""
        self.stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=3)
        now = _now_ms()
        self._emit(self.fg_tracker.force_close(now, config.FLAG_EXIT))
        self._emit(self.bg_tracker.close_all(now, config.FLAG_EXIT))
        self.flush(force=True)

    def shutdown(self) -> None:
        self.stop_and_flush()          # 数据已落库，之后才允许关库
        try:
            self.power.unregister(self.window.hwnd)
            self.fg.uninstall()
            if self.icon:
                self.icon.hide()
            self.window.destroy()
        except Exception:
            pass      # nosec B110  剩下的只是资源释放，失败也不能阻断退出（防进程残留）
        self.store.log_event("daemon_stop")
        self.store.close()

    # ---------------------------------------------------- 托盘菜单

    def _menu_items(self) -> list:
        day = track.day_of(_now_ms())
        s = self.store.totals_summary(day, day)
        return tray.Options.build(self.paused, _fmt_duration(s["fg_ms"]),
                                  _fmt_duration(s["bg_ms"]))

    def _on_command(self, item_id: int, _lparam: int) -> None:
        if item_id == tray.Options.OPEN:
            self.open_gui()
        elif item_id == tray.Options.PAUSE:
            self.set_paused(not self.paused)
        elif item_id == tray.Options.QUIT:
            self.stop_event.set()
            self.window.post_quit()

    def open_gui(self) -> None:
        """按需拉起统计窗口进程。参数全为字面量、绝不 shell=True（R5）。"""
        try:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--gui"],  # nosec B603
                             shell=False, close_fds=True)
        except OSError as exc:
            self.store.log_event("gui_launch_fail", detail=str(exc))

    def set_paused(self, value: bool) -> None:
        if value == self.paused:
            return
        if value:
            self.flush(force=True)
            self.fg_tracker.force_close(_now_ms(), config.FLAG_LOCK)
        self.paused = value
        try:    # 暂停意图落盘（BUG-4）：重启/崩溃重开都不许静默恢复采集
            config.save({**config.load(), "paused": value})
        except OSError:
            self.store.error_count += 1   # R8：落盘失败必须可见，不许静默吞掉
        self.store.log_event("pause" if value else "resume_collect")
        if self.icon:
            self.icon.set_tooltip("已暂停统计" if value else config.APP_TITLE)

    # ---------------------------------------------------- 电源 / 会话

    def _on_power(self, message: int, wparam: int) -> None:
        event = sysmsg.PowerWatcher.interpret(message, wparam)
        if event is None:
            return
        if event in ("sleep", "lock"):
            self.paused_by_power = True
            self.flush(force=True)
            self._emit(self.fg_tracker.force_close(_now_ms(), config.FLAG_LOCK))
            self._emit(self.bg_tracker.close_all(_now_ms(), config.FLAG_LOCK))
            self.sampler.reset_baselines()
        else:   # resume / unlock
            if not self.paused_by_power:
                return
            self.sampler.reset_baselines()   # 必须重置基线：休眠中计数不增长，
            self.fg.forget()                 # 不重置则唤醒后第一次差分是巨值
            self.paused_by_power = False
        self.store.log_event(event)

    # ---------------------------------------------------- 采集工作线程

    def _loop(self) -> None:
        winutil.set_below_normal_priority()
        while not self.stop_event.is_set():
            try:
                if self._stop_flag_seen():
                    self.stop_event.set()
                    self.window.post_quit()      # 让主线程的消息循环也退出
                    break
                if self.paused or self.paused_by_power:
                    time.sleep(0.3)
                    continue
                self._tick()
            except Exception as exc:      # R8：出错要留痕，不许静默
                self.store.log_event("collect_error", detail=type(exc).__name__)
                time.sleep(0.5)
            time.sleep(0.2)
        self.flush(force=True)

    @staticmethod
    def _stop_flag_seen() -> bool:
        """停止脚本放下的标志文件 → 走优雅收尾（不丢最后一段）。"""
        flag = config.stop_flag_path()
        if not flag.exists():
            return False
        try:
            flag.unlink()
        except OSError:
            pass
        return True

    def _tick(self) -> None:
        now_ms = _now_ms()
        for sample in self.fg.drain():
            self._handle_fg(sample)
        if (polled := self.fg.poll()) is not None:
            self._handle_fg(polled)

        if self.sampler.due(time.monotonic()):
            samples, exited = self.sampler.sample(now_ms, self._known())
            for pid in exited:
                self._emit(self.bg_tracker.close_pid(pid, now_ms))
                self.resolver.forget_pid(pid)
            for sample in samples:
                self._handle_bg(sample)

        self.flush()

    def _emit(self, segments: list) -> None:
        """新片段汇入待写队列。主线程（锁屏/暂停/退出）与工作线程（采集）
        都会调用，不加锁的话 rebind 交错会静默丢掉 ≤1 个 flush 周期（BUG-9）。"""
        with self._pending_lock:
            self._pending.extend(segments)

    def _handle_fg(self, sample) -> None:
        if sample.pid <= 0:
            self._emit(self.fg_tracker.force_close(sample.ts_ms, 0))
            return
        info = self.resolver.resolve(sample.pid)
        if self._is_junk(info):
            return       # 无信息量样本：不落库不采集，也不闭合当前真实前台段（BUG-28）
        if not self._register(info):
            # 不可监测 / 用户没勾选 → 不采集，并闭合可能还开着的片段
            self._emit(self.fg_tracker.force_close(sample.ts_ms, 0))
            return
        guessed = not sample.from_event
        self._emit(self.fg_tracker.on_fg(info.app_key, sample.pid, sample.ts_ms, guessed))

    def _handle_bg(self, sample) -> None:
        info = self.resolver.resolve(sample.pid)
        if self._is_junk(info):
            return
        if not self._register(info):
            self._emit(self.bg_tracker.close_pid(sample.pid, sample.ts_ms))
            return
        self._emit(self.bg_tracker.on_sample(
            info.app_key, sample.pid, sample.ts_ms, sample.active))

    @staticmethod
    def _is_junk(info) -> bool:
        """读不到路径且连进程名都没有（name:pid-N）的样本：落库只会产生
        「(null)[锁]」这种用户看不懂的行，且没有任何可用信息（BUG-28）。"""
        return not info.exe_path and info.app_key.startswith("name:pid-")

    def _register(self, info) -> bool:
        """写入元信息并决定是否采集。最终决定权在 app_capability 表
        （level='no' 恒 enabled=0），手工改配置也绕不过。结论每个应用只写
        一次；「重新检测」过的例外，采样到即重写一次重探结论（读不到会
        如实写回 no）。绝不能恒重写，否则内存 fail_count 会把 UI 的重置
        立刻覆盖回 no（BUG-6）。
        """
        self.store.upsert_app(info)
        cap = apps.capability_of(info, self.resolver.fail_count(info.app_key))
        if info.app_key not in self._cap_written or info.app_key in self._recheck_pending:
            self._cap_written.add(info.app_key)
            self._recheck_pending.discard(info.app_key)
            self.store.upsert_capability(cap)
        if time.monotonic() - self._enabled_at > 30.0:
            self._enabled = {key: True for key in self.store.enabled_keys()}
            self._recheck_pending = self.store.recheck_pending_keys()
            self._enabled_at = time.monotonic()
        if cap.level == "no":
            return False
        return bool(self._enabled.get(info.app_key, cap.enabled))

    def _known(self) -> set:
        if time.monotonic() - self._known_at > 300.0:
            # 7 天窗口容忍"应用几天没被记到"的空档（last_seen_ms 已由 upsert 周期刷新）
            self._known_exes = self.store.known_exe_paths(7 * 24)
            self._known_at = time.monotonic()
        return self._known_exes

    # ---------------------------------------------------- 落库

    def flush(self, force: bool = False) -> None:
        due = time.monotonic() - self._last_flush >= config.FLUSH_INTERVAL_S
        with self._pending_lock:
            if not self._pending or not (force or due or len(self._pending) >= 50):
                return
            batch, self._pending = self._pending, []   # 原子交换：杜绝交错丢段
        segments, clamped = track.clamp_fg_overlaps(track.finalize(batch))
        self._last_flush = time.monotonic()
        if clamped:
            self.store.error_count += clamped    # 兜底触发=未知重叠路径，必须可见（R8）
        if segments:
            self.store.write_segments(segments)
        self._maybe_trim_memory()

    def _maybe_trim_memory(self) -> None:
        """写完库后把不常用页换出：压低常驻内存，又不频繁触发增加磁盘负担。"""
        if time.monotonic() - self._last_trim < config.EMPTY_WORKING_SET_INTERVAL_S:
            return
        self._last_trim = time.monotonic()
        winutil.empty_working_set()


# ---------------------------------------------------------------- 工具

def _now_ms() -> int:
    return int(time.time() * 1000)


def _user_sid() -> str:
    return winutil.current_user_sid() or "unknown"


def _fmt_duration(ms: int) -> str:
    m = int(ms // 60000)
    return "%dm" % m if m < 60 else "%dh%02dm" % (m // 60, m % 60)


def run_gui(smoke: bool = False) -> int:
    """统计窗口（独立进程，关窗即退出）；延迟导入 PySide6，守护模式永不加载 Qt。"""
    import ui_main
    sid = _user_sid()
    # 统计窗口自己的单实例：重复点「打开统计窗口」时第二个进程直接退（BUG-13）
    if not _acquire_mutex("Local\\UsageWatcher_GUI_" + sid):
        return 0
    return ui_main.run(smoke=smoke, daemon_alive=lambda: winutil.mutex_alive(_MUTEX_NAME + sid))


def main(argv: list) -> int:
    if "--gui" in argv:
        return run_gui(smoke="--smoke" in argv)
    if "--smoke" in argv:
        return _smoke_run()
    if not acquire_single_instance():
        print("程序已在运行，正在打开统计窗口…")
        run_gui()          # 不再起第二个采集器，直接把统计窗口拉起来
        return 0
    print("%s v%s 正在后台采集。托盘图标可右键操作。" % (config.APP_TITLE, config.APP_VERSION))
    return Daemon().run()


def _smoke_run(seconds: float = 8.0) -> int:
    """自检交给 tools/smoke_daemon.py；只有真用到 --smoke 才把 tools 加进 sys.path。"""
    tools_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import smoke_daemon
    return smoke_daemon.run(seconds)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
