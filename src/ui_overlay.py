"""桌面悬浮窗（v0.3）：独立进程，`pythonw app.py --overlay` 或 exe --overlay 启动。

设计取舍：
- 不进任务栏（Qt.Tool）、无边框、置顶、半透明圆角 —— 用 PySide6 做渲染，
  内存代价约 45MB（设置页勾选项已注明，R8 精神：让用户知情后再开）；
- 单实例：Local\\UsageWatcher_Overlay_<SID> 互斥体，重复启动直接退；
- 数据只读：data.Store 查 totals_summary（UI 层不许 SQL，走 data.py）；
- 守护不在 → 显示「监测未运行」，连续 OVERLAY_GRACE_QUITS 个周期仍不在 → 自动退出
  （跟随守护生命周期，不留孤儿窗口）；
- 退出通道与 stop.flag 同一套零 IPC 思路：overlay.flag；
- 本文件属于 ui_ 前缀，tools/test_arch.py 断言其无 SQL、无反向依赖。
"""

from __future__ import annotations

import ctypes
import os
import subprocess   # nosec B404 —— 只以 shell=False、字面量参数拉起统计窗口
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QMenu, QWidget

import config          # noqa: E402
import data            # noqa: E402
import i18n            # noqa: E402
import winutil         # noqa: E402

_MUTEX = "Local\\UsageWatcher_Overlay_"
_mutex_handles: list = []


def acquire_overlay_mutex() -> bool:
    """单实例：已是第二个悬浮窗就返回 False（进程直接退）。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    handle = k32.CreateMutexW(None, False, _MUTEX + (winutil.current_user_sid() or "x"))
    if not handle:
        return True
    _mutex_handles.append(handle)
    return ctypes.get_last_error() != 183


def launch_overlay() -> None:
    """从统计窗口/设置页拉起悬浮窗进程（参数全字面量，R5）。"""
    if getattr(sys, "frozen", False):
        target = [sys.executable, "--overlay"]
    else:
        target = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               "ui_overlay.py"), "--overlay"]
    try:
        subprocess.Popen(target, shell=False, close_fds=True)   # nosec B603
    except OSError:
        pass    # 拉不起来不影响主程序（R8：设置页下次勾选可重试）


class Overlay(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.store = data.Store()
        self.missed = 0
        self._drag = QPoint()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowOpacity(config.OVERLAY_OPACITY)
        self.resize(config.OVERLAY_WIDTH, config.OVERLAY_HEIGHT)
        self._place()
        self._today: dict = {"total_ms": 0, "fg_ms": 0, "bg_ms": 0}
        self._alive = True
        self.tick()
        slow = QTimer(self)
        slow.setInterval(config.OVERLAY_REFRESH_S * 1000)
        slow.timeout.connect(self.tick)
        slow.start()
        # flag 要快感知：设置页取消勾选后 2 秒内消失，不能挂在 60s 数据刷新上
        fast = QTimer(self)
        fast.setInterval(2000)
        fast.timeout.connect(self._check_flag)
        fast.start()

    # ---- 数据 ----

    def _shutdown(self) -> None:
        """关窗并退出事件循环。QWidget.close() 只隐藏窗口 —— 不 quit 的话
        app.exec() 永远不返回，进程变孤儿（冒烟实测 rc=-1 被 kill）。"""
        self.close()
        QApplication.instance().quit()

    def _check_flag(self) -> None:
        if config.overlay_flag_path().exists():
            config.overlay_flag_path().unlink(missing_ok=True)
            self._shutdown()

    def tick(self) -> None:
        """周期刷新；守护不在连续超限就优雅退出，退出通道是 overlay.flag。"""
        if config.overlay_flag_path().exists():
            config.overlay_flag_path().unlink(missing_ok=True)
            return self._shutdown()
        if winutil.mutex_alive("Global\\UsageWatcher_SingleInstance_"
                               + (winutil.current_user_sid() or "x")):
            self.missed = 0
            self._alive = True
            self._today = self.store.totals_summary(*_today_iso())
        else:
            self.missed += 1
            self._alive = False
            if self.missed >= config.OVERLAY_GRACE_QUITS:
                return self._shutdown()
        self.update()   # 触发重绘

    # ---- 外观 ----

    def _place(self) -> None:
        cfg = config.load()
        x, y = int(cfg.get("overlay_x", -1)), int(cfg.get("overlay_y", -1))
        if x >= 0 and y >= 0:
            return self.move(x, y)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 16, screen.top() + 80)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#22343C"))
        p.drawRoundedRect(self.rect(), 12, 12)
        p.setPen(QPen(QColor("#DDE7EC")))
        p.setFont(QFont("Microsoft YaHei", 8))
        p.drawText(self.rect().adjusted(12, 7, -12, -30), Qt.AlignLeft,
                   i18n.t("ov_title") if self._alive else i18n.t("ov_paused"))
        p.setPen(QPen(QColor("#63D19E" if self._alive else "#9AA7AD")))
        p.setFont(QFont("Microsoft YaHei", 13, QFont.Bold))
        p.drawText(self.rect().adjusted(12, 20, -12, -4), Qt.AlignLeft,
                   _fmt(self._today["total_ms"]))
        p.setPen(QPen(QColor("#8FA6B0")))
        p.setFont(QFont("Microsoft YaHei", 7))
        p.drawText(self.rect().adjusted(12, 44, -8, -2), Qt.AlignLeft,
                   i18n.t("ov_fg_bg", _fmt(self._today["fg_ms"]),
                          _fmt(self._today["bg_ms"])))
        p.end()

    # ---- 交互：拖动 / 右键菜单 ----

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        # v0.5：几乎没移动 = 左键点击 → 打开统计窗口（拖动仍走保存位置）
        moved = (event.globalPosition().toPoint() - self._press).manhattanLength() \
            if hasattr(self, "_press") else 999
        try:
            config.save({**config.load(), "overlay_x": self.x(), "overlay_y": self.y()})
        except OSError:
            pass    # 位置记不住是小损失，不值得打断使用（R8 针对故障可见）
        if moved <= 6:
            self._open_gui()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction(i18n.t("tray_open"), self._open_gui)
        menu.addSeparator()
        menu.addAction(i18n.t("ov_menu_quit"), self._shutdown)
        menu.exec(event.globalPos())

    def _open_gui(self) -> None:
        if getattr(sys, "frozen", False):
            target = [sys.executable, "--gui"]
        else:
            target = [sys.executable, os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "app.py"), "--gui"]
        try:
            subprocess.Popen(target, shell=False, close_fds=True)   # nosec B603
        except OSError:
            pass

    def closeEvent(self, event) -> None:
        self.store.close()
        event.accept()


def _today_iso() -> tuple:
    day = time.strftime("%Y-%m-%d")
    return day, day


def _fmt(ms: int) -> str:
    m = int(ms // 60000)
    return i18n.t("dur_min", m) if m < 60 else i18n.t("dur_hm", m // 60, m % 60)


def restore_if_enabled() -> None:
    """偏好开了悬浮窗且它没在跑 → 顺手拉起（重启后由打开统计窗口恢复，v0.3）。
    只探测互斥体（winutil.mutex_alive），绝不在 GUI 进程里创建
    overlay 的互斥体 —— 否则真悬浮窗启动时会误判"已有实例"而自杀。"""
    import winutil
    if not config.load().get("overlay_enabled"):
        return
    alive = winutil.mutex_alive("Local\\UsageWatcher_Overlay_"
                                + (winutil.current_user_sid() or "x"))
    if not alive:
        launch_overlay()


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    window = Overlay()
    window.show()
    return app.exec()


if __name__ == "__main__":
    if "--overlay" in sys.argv[1:] and acquire_overlay_mutex():
        raise SystemExit(run())
    raise SystemExit(0)
