"""程序入口。

三种模式（靠命令行参数分流，同一个脚本）：
    无参数      采集守护进程（**不加载 PySide6**，常驻约 35MB）
    --gui       统计窗口（独立的 PySide6 进程，关窗即退出、内存释放）
    --smoke     自检模式，跑几秒自动退出（给自动化验证用）

线程模型：
    主线程  = Win32 消息循环（派发 WinEvent 回调、托盘消息、电源/会话通知）
    工作线程 = 采集与写库
这样 UI/托盘永远不会被磁盘 I/O 卡住（否则会出现"点击无响应"）。
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

_MUTEX_NAME = "Global\\UsageWatcher_SingleInstance_"
_mutex_handle = None


# ---------------------------------------------------------------- 单实例

def acquire_single_instance() -> bool:
    """命名互斥体判重。名字带当前用户 SID，避免多用户环境互相抢占。

    刻意不建任何 IPC 通道（不用命名管道/端口/QLocalServer）——
    第二个实例直接去开统计窗口读同一个库即可，没有可被劫持的通道。
    """
    global _mutex_handle
    import winutil
    name = _MUTEX_NAME + (winutil.current_user_sid() or "unknown")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    _mutex_handle = kernel32.CreateMutexW(None, False, name)
    if not _mutex_handle:
        return True                     # 创建失败就不拦，宁可多开也不要开不了
    return ctypes.get_last_error() != 183   # ERROR_ALREADY_EXISTS


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
        self._last_flush = time.monotonic()
        self._last_trim = 0.0
        self._enabled: dict = {}
        self._enabled_at = 0.0
        self._known_exes: set = set()
        self._known_at = 0.0
        self._worker = None

    # ---------------------------------------------------- 生命周期

    def start(self) -> bool:
        # 清掉可能残留的停止标志：否则上一轮没被消费的标志会让刚启动的进程
        # 立刻把自己关掉（实测踩过：表现为"启动了但马上就没进程了"）
        try:
            stale = config.stop_flag_path()
            if stale.exists():
                stale.unlink()
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
        # 两个电源相关消息各注册一个处理器：把消息 id 一起绑进去，
        # 避免在处理器里再去猜"这条消息到底是哪一个"（那种写法很容易出错）
        self.window.on(sysmsg.WM_POWERBROADCAST,
                       lambda wp, lp: self._on_power(sysmsg.WM_POWERBROADCAST, wp))
        self.window.on(sysmsg.WM_WTSSESSION_CHANGE,
                       lambda wp, lp: self._on_power(sysmsg.WM_WTSSESSION_CHANGE, wp))
        self.store.maintenance()
        self._worker = threading.Thread(target=self._loop, name="collector", daemon=True)
        self._worker.start()
        return True
    def run(self) -> int:
        if not self.start():
            return 1
        self.window.run(on_tick=lambda: None, should_stop=self.stop_event.is_set,
                        tick_ms=500)
        self.shutdown()
        return 0

    def stop_and_flush(self) -> None:
        """停采集 + 等工作线程收尾 + 闭合在开片段 + 落库。

        两条实战教训都在这里：
        · 必须**先把当前还开着的片段闭合**再退出，否则每次退出都会丢掉
          "用户正在用的那一段"（自检时表现为"记录了 0 个应用"）。
        · 查询要发生在关库之前，shutdown() 之后查库会撞
          "Cannot operate on a closed database"。
        """
        self.stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=3)
        now = _now_ms()
        self._pending.extend(self.fg_tracker.force_close(now, config.FLAG_EXIT))
        self._pending.extend(self.bg_tracker.close_all(now, config.FLAG_EXIT))
        self.flush(force=True)

    def shutdown(self) -> None:
        self.stop_and_flush()          # 关键：数据已经写完了
        try:
            self.power.unregister(self.window.hwnd)
            self.fg.uninstall()
            if self.icon:
                self.icon.hide()
            self.window.destroy()
        except Exception:
            # 数据此刻已落库，剩下的都是资源释放；
            # 释放失败不该让退出流程中断（否则进程可能残留）
            pass      # nosec B110
        self.store.log_event("daemon_stop")
        self.store.close()

    # ---------------------------------------------------- 托盘菜单

    def _menu_items(self) -> list:
        summary = self.store.totals_summary(track.day_of(_now_ms()), track.day_of(_now_ms()))
        return tray.Options.build(
            self.paused,
            _fmt_duration(summary["fg_ms"]),
            _fmt_duration(summary["bg_ms"]),
        )

    def _on_command(self, item_id: int, _lparam: int) -> None:
        if item_id == tray.Options.OPEN:
            self.open_gui()
        elif item_id == tray.Options.PAUSE:
            self.set_paused(not self.paused)
        elif item_id == tray.Options.QUIT:
            self.stop_event.set()
            self.window.post_quit()

    def open_gui(self) -> None:
        """按需拉起统计窗口进程。子进程参数用列表传递、绝不 shell=True（R5）。"""
        try:
            # 参数全是代码里写死的字面量，不含任何外部输入；shell=False 已显式指定
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
        # 暂停意图落盘：重启/崩溃重开都不许静默恢复采集（BUG-4，隐私优先）
        cfg = config.load()
        cfg["paused"] = value
        try:
            config.save(cfg)
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
            self._pending.extend(self.fg_tracker.force_close(_now_ms(), config.FLAG_LOCK))
            self._pending.extend(self.bg_tracker.close_all(_now_ms(), config.FLAG_LOCK))
            self.sampler.reset_baselines()
        else:   # resume / unlock
            if not self.paused_by_power:
                return
            # 必须重置基线：休眠期间进程还在但 CPU/IO 计数不增长，
            # 不重置的话唤醒后第一次差分会把整段差额算进一个采样周期 → 巨值
            self.sampler.reset_baselines()
            self.fg.forget()                # 强制重新识别当前前台程序
            self.paused_by_power = False
        self.store.log_event(event)

    # ---------------------------------------------------- 采集工作线程

    def _loop(self) -> None:
        import winutil
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
        polled = self.fg.poll()
        if polled is not None:
            self._handle_fg(polled)

        if self.sampler.due(time.monotonic()):
            samples, exited = self.sampler.sample(now_ms, self._known())
            for pid in exited:
                self._pending.extend(self.bg_tracker.close_pid(pid, now_ms))
                self.resolver.forget_pid(pid)
            for sample in samples:
                self._handle_bg(sample)

        self.flush()

    def _handle_fg(self, sample) -> None:
        if sample.pid <= 0:
            self._pending.extend(self.fg_tracker.force_close(sample.ts_ms, 0))
            return
        info = self.resolver.resolve(sample.pid)
        if not self._register(info):
            # 不可监测 / 用户没勾选 → 不采集，并闭合可能还开着的片段
            self._pending.extend(self.fg_tracker.force_close(sample.ts_ms, 0))
            return
        guessed = not sample.from_event
        self._pending.extend(
            self.fg_tracker.on_fg(info.app_key, sample.pid, sample.ts_ms, guessed))

    def _handle_bg(self, sample) -> None:
        info = self.resolver.resolve(sample.pid)
        if not self._register(info):
            self._pending.extend(self.bg_tracker.close_pid(sample.pid, sample.ts_ms))
            return
        self._pending.extend(
            self.bg_tracker.on_sample(info.app_key, sample.pid, sample.ts_ms, sample.active))

    def _register(self, info) -> bool:
        """写入元信息并决定是否采集。返回 True 表示允许采集。

        不能只信配置文件：最终决定权在 app_capability 表
        （level='no' 恒为 enabled=0），所以手工改配置也绕不过去。
        """
        self.store.upsert_app(info)
        cap = apps.capability_of(info, self.resolver.fail_count(info.app_key))
        if self._enabled.get(info.app_key) is None or cap.level == "no":
            self.store.upsert_capability(cap)
        if time.monotonic() - self._enabled_at > 30.0:
            self._enabled = {key: True for key in self.store.enabled_keys()}
            self._enabled_at = time.monotonic()
        if cap.level == "no":
            return False
        return bool(self._enabled.get(info.app_key, cap.enabled))

    def _known(self) -> set:
        if time.monotonic() - self._known_at > 300.0:
            # 7 天窗口：last_seen_ms 现在由 upsert_app 周期性刷新（APP_SEEN_FLUSH_S），
            # 放宽窗口可容忍"应用几天没被记到"的空档，避免候选池静默缩水
            self._known_exes = self.store.known_exe_paths(7 * 24)
            self._known_at = time.monotonic()
        return self._known_exes

    # ---------------------------------------------------- 落库

    def flush(self, force: bool = False) -> None:
        due = time.monotonic() - self._last_flush >= config.FLUSH_INTERVAL_S
        if not self._pending or not (force or due or len(self._pending) >= 50):
            return
        segments = track.finalize(self._pending)
        self._pending = []
        self._last_flush = time.monotonic()
        if segments:
            self.store.write_segments(segments)
        self._maybe_trim_memory()

    def _maybe_trim_memory(self) -> None:
        """写完库之后顺带把不常用页换出。

        只在真写过库之后调用 —— 这样既压低了常驻内存，
        又不会因为频繁触发而反过来增加磁盘负担。
        """
        if time.monotonic() - self._last_trim < config.EMPTY_WORKING_SET_INTERVAL_S:
            return
        self._last_trim = time.monotonic()
        import winutil
        winutil.empty_working_set()


# ---------------------------------------------------------------- 工具

def _now_ms() -> int:
    return int(time.time() * 1000)


def _fmt_duration(ms: int) -> str:
    minutes = int(ms // 60000)
    if minutes < 60:
        return "%dm" % minutes
    return "%dh%02dm" % (minutes // 60, minutes % 60)


def run_gui(smoke: bool = False) -> int:
    """统计窗口（独立进程，关窗即退出）。延迟导入 PySide6，
    这样守护模式下永远不会加载 Qt。"""
    import ui_main
    import winutil
    name = _MUTEX_NAME + (winutil.current_user_sid() or "unknown")
    return ui_main.run(smoke=smoke, daemon_alive=lambda: winutil.mutex_alive(name))


def main(argv: list) -> int:
    if "--gui" in argv:
        return run_gui(smoke="--smoke" in argv)
    if "--smoke" in argv:
        return _smoke_run()
    if not acquire_single_instance():
        # 已在运行：不再起第二个采集器，直接把统计窗口拉起来
        print("程序已在运行，正在打开统计窗口…")
        run_gui()
        return 0
    daemon = Daemon()
    print("%s v%s 正在后台采集。托盘图标可右键操作。" % (config.APP_TITLE, config.APP_VERSION))
    return daemon.run()


def _smoke_run(seconds: float = 8.0) -> int:
    """自检交给 tools/smoke_daemon.py —— 诊断工具不该占生产代码的地方。

    只有真的用到 --smoke 时才把它加进 sys.path，
    这样正常运行（守护/统计窗口）完全不引入 tools 目录。
    """
    tools_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import smoke_daemon
    return smoke_daemon.run(seconds)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
