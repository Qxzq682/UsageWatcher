"""统计窗口（PySide6）。

只有 `--gui` 模式会加载本模块 —— 守护进程永远不 import PySide6，
这是常驻内存能压到 ~35MB 的前提。

架构约束（tools/test_arch.py 会断言）：
**本文件不许出现任何 SQL / sqlite3**，所有取数一律调用 data.Store 暴露的方法。
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date, timedelta

from PySide6.QtCore import QAbstractTableModel, QDate, QFileInfo, QModelIndex, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QFileDialog, QFileIconProvider, QHBoxLayout,
    QHeaderView, QLabel, QMainWindow, QMessageBox, QPushButton, QTableView,
    QTabWidget, QVBoxLayout, QWidget,
)

import config
import data
import track

COLOR_FG = QColor("#378ADD")
COLOR_BG = QColor("#B4B2A9")
COLOR_TEXT = QColor("#2C2C2A")
COLOR_MUTED = QColor("#888780")


def fmt_duration(ms: int) -> str:
    minutes = int(ms // 60000)
    if minutes < 60:
        return "%d 分" % minutes
    return "%d 小时 %02d 分" % (minutes // 60, minutes % 60)


# ---------------------------------------------------------------- CSV 导出

def _csv_safe(text: str) -> str:
    """防 CSV 公式注入（R3 精神）：以 = + - @ 开头的单元格加前导单引号。

    应用名来自版本资源等外部数据（R3），进 Excel/审阅时可能被当公式执行。
    """
    text = str(text)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def csv_rows(rows: list) -> list:
    """把 app_totals 的行转成 CSV 单元格（含表头），纯函数便于回归。"""
    out = [("应用", "可执行文件", "前台(分钟)", "后台活跃(分钟)", "活跃总时长(分钟)", "前台占比%")]
    for r in rows:
        out.append((
            _csv_safe(r["display_name"]),
            _csv_safe(r["exe_name"]),
            round(r["fg_ms"] / 60000, 1),
            round(r["bg_ms"] / 60000, 1),
            round(r["total_ms"] / 60000, 1),
            round(r["fg_ratio"] * 100, 1),
        ))
    return out


# ---------------------------------------------------------------- 周期计算

PERIODS = ("今日", "昨日", "按日", "本周", "本月", "近 30 天")


def period_range(period: str, day=None) -> tuple:
    """周期 → (起日, 止日) ISO 串。day 是"按日"模式的指定日期（date 对象），
    缺省昨天；其它周期忽略 day（纯函数，便于回归）。"""
    today = date.today()
    if period == "昨日":
        y = today - timedelta(days=1)
        return y.isoformat(), y.isoformat()
    if period == "按日":
        d = day or (today - timedelta(days=1))
        if isinstance(d, str):
            d = date.fromisoformat(d)
        return d.isoformat(), d.isoformat()
    if period == "今日":
        return today.isoformat(), today.isoformat()
    if period == "本周":
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat()
    if period == "本月":
        return today.replace(day=1).isoformat(), today.isoformat()
    return (today - timedelta(days=29)).isoformat(), today.isoformat()


# ---------------------------------------------------------------- 排行表

class RankModel(QAbstractTableModel):
    HEADERS = ("应用", "前台", "后台活跃", "活跃总时长(去重)", "前台占比")

    def __init__(self) -> None:
        super().__init__()
        self.rows: list = []
        self._fip, self._icons = QFileIconProvider(), {}   # 本机 shell 图标（零联网）+ 缓存

    def set_rows(self, rows: list) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, _parent=QModelIndex()) -> int:
        return len(self.rows)

    def columnCount(self, _parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, _role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if _role == Qt.ToolTipRole:
            return "活跃总时长=前台∪后台去重；多进程应用前后台会重叠，故小于相加"
        return self.HEADERS[section] if _role == Qt.DisplayRole else None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        column = index.column()
        if role == Qt.DisplayRole:
            if column == 0:
                return row["display_name"]
            if column == 1:
                return fmt_duration(row["fg_ms"])
            if column == 2:
                return fmt_duration(row["bg_ms"]) if row["bg_ms"] else "—"
            if column == 3:
                return fmt_duration(row["total_ms"])
            return "%.0f%%" % (row["fg_ratio"] * 100)
        if role == Qt.DecorationRole and column == 0 and row["exe_path"]:
            key = row["exe_path"]
            if key not in self._icons:
                self._icons[key] = self._fip.icon(QFileInfo(key))
            return self._icons[key]
        if role == Qt.ToolTipRole and column == 0:
            return "%s\n%s" % (row["display_name"], row["exe_path"] or row["app_key"])
        if role == Qt.TextAlignmentRole and column > 0:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


# ---------------------------------------------------------------- 手绘图

class StackedBar(QWidget):
    """各应用前/后台时长占比的横向堆叠条（QPainter 手绘）。

    为什么不用 QtCharts：PySide6_Essentials 里没有 Qt6Charts.dll，
    import 直接失败（实测）。引 PySide6-Addons 只为画条形图不划算。
    """

    def __init__(self) -> None:
        super().__init__()
        self.rows: list = []
        self.setMinimumHeight(120)

    def set_rows(self, rows: list) -> None:
        self.rows = rows[:10]
        self.setMinimumHeight(max(120, 26 * len(self.rows) + 20))
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if not self.rows:
            painter.setPen(QPen(COLOR_MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            return
        label_width = 130
        right = self.width() - 70
        bar_area = max(right - label_width, 20)
        peak = max((r["total_ms"] for r in self.rows), default=1) or 1

        for index, row in enumerate(self.rows):
            y = 12 + index * 26
            painter.setPen(QPen(COLOR_TEXT))
            painter.setFont(QFont("Microsoft YaHei", 9))
            painter.drawText(QRectF(0, y, label_width - 8, 18),
                             int(Qt.AlignRight | Qt.AlignVCenter),
                             _elide(row["display_name"], 12))
            width = max(bar_area * row["total_ms"] / peak, 2)
            fg_width = max(width * row["fg_ratio"], 0)
            painter.fillRect(QRectF(label_width, y + 3, width, 12), COLOR_BG)
            if fg_width > 0:
                painter.fillRect(QRectF(label_width, y + 3, fg_width, 12), COLOR_FG)
            painter.setPen(QPen(COLOR_MUTED))
            painter.drawText(QRectF(label_width + width + 6, y, 62, 18),
                             int(Qt.AlignLeft | Qt.AlignVCenter),
                             fmt_duration(row["total_ms"]))
        painter.end()


class DailyBars(QWidget):
    """近 N 天每日前/后台时长的迷你柱状图。"""

    def __init__(self) -> None:
        super().__init__()
        self.days: list = []
        self.setMinimumHeight(110)

    def set_days(self, days: list) -> None:
        self.days = days
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if not self.days:
            painter.setPen(QPen(COLOR_MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无数据")
            return
        # 柱高与峰值必须用 active_ms（fg∪bg 并集口径，与统计页一致）；
        # 用 fg+bg 相加会让柱子虚高、多日被虚高峰值整体压扁（BUG-27）
        peak = max((d["active_ms"] for d in self.days), default=1) or 1
        slot = self.width() / max(len(self.days), 1)
        bar_width = min(slot * 0.5, 34)
        base = self.height() - 20

        painter.setFont(QFont("Microsoft YaHei", 8))
        for index, day in enumerate(self.days):
            center = slot * (index + 0.5)
            total = day["active_ms"]
            height = max((base - 10) * total / peak, 2)
            fg_height = height * (day["fg_ms"] / total if total else 0)
            painter.fillRect(QRectF(center - bar_width / 2, base - height, bar_width, height), COLOR_BG)
            if fg_height > 0:
                painter.fillRect(QRectF(center - bar_width / 2, base - fg_height,
                                        bar_width, fg_height), COLOR_FG)
            painter.setPen(QPen(COLOR_MUTED))
            painter.drawText(QRectF(center - 24, base + 2, 48, 16),
                             int(Qt.AlignHCenter | Qt.AlignTop), day["day"][5:])
        painter.end()


def _elide(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------- 主窗口

class MainWindow(QMainWindow):
    def __init__(self, store, daemon_alive=None) -> None:
        super().__init__()
        self.store = store
        self.daemon_alive = daemon_alive
        self.setWindowTitle("%s v%s" % (config.APP_TITLE, config.APP_VERSION))
        self.resize(920, 620)

        self.period = QComboBox()
        self.period.addItems(PERIODS)
        self.day_pick = QDateEdit()            # "按日"模式的日期选择（带日历弹窗）
        self.day_pick.setCalendarPopup(True)
        self.day_pick.setDisplayFormat("yyyy-MM-dd")
        self.day_pick.setDate(QDate.currentDate().addDays(-1))
        self.day_pick.dateChanged.connect(lambda _d: self.reload())
        self.day_pick.hide()
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.reload)
        export = QPushButton("导出 CSV")
        export.clicked.connect(self._export_csv)

        top = QHBoxLayout()
        top.addWidget(QLabel("统计周期"))
        top.addWidget(self.period)
        top.addWidget(self.day_pick)
        top.addWidget(refresh)
        top.addWidget(export)
        top.addStretch(1)
        self.summary = QLabel("")
        top.addWidget(self.summary)

        self.model = RankModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)

        self.bars = StackedBar()
        self.daily = DailyBars()
        # 空状态提示（R8：空也要给原因，不许只显示"暂无数据"让人猜）
        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#8a6d3b; background:#fdf6e3; padding:6px 10px;")
        self.hint.setWordWrap(True)
        self.hint.hide()
        self._build_tabs_and_layout(top)

        self.period.currentTextChanged.connect(self.reload)
        self.reload()
        self._start_auto_refresh()

    def _build_tabs_and_layout(self, top: QHBoxLayout) -> None:
        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        stats_layout.addWidget(QLabel("各应用前/后台时长（蓝=前台，灰=后台活跃）。"
                                      "活跃总时长=前后台并集去重，多进程应用前后台重叠，不等于相加"))
        stats_layout.addWidget(self.bars)
        stats_layout.addWidget(self.table, 1)
        self.daily_title = QLabel("每日总时长")
        stats_layout.addWidget(self.daily_title)
        stats_layout.addWidget(self.daily)

        import ui_scope
        self.tabs = QTabWidget()
        self.tabs.addTab(stats_tab, "统计")
        self.tabs.addTab(ui_scope.ScopePage(self.store), "监测范围")
        self.tabs.addTab(ui_scope.PrivacyPage(self.store), "隐私与数据")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addWidget(self.hint)
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

    def _start_auto_refresh(self) -> None:
        # 自动刷新：片段落库有 FLUSH_INTERVAL 延迟，手动刷新容易看到"旧世界"
        timer = QTimer(self)
        timer.setInterval(config.UI_REFRESH_INTERVAL_S * 1000)
        timer.timeout.connect(self.reload)
        timer.start()

    def _picked_day(self) -> date:
        return self.day_pick.date().toPython()   # PySide6 是 toPython（toPyDate 是 PyQt 的）

    def reload(self) -> None:
        period = self.period.currentText()
        self.day_pick.setVisible(period == "按日")
        day_from, day_to = period_range(period, day=self._picked_day())
        rows = self.store.app_totals(day_from, day_to, limit=config.load()["rank_limit"])
        summary = self.store.totals_summary(day_from, day_to)
        self.summary.setText("活跃总时长 %s　其中前台 %s（%.0f%%）" % (
            fmt_duration(summary["total_ms"]), fmt_duration(summary["fg_ms"]),
            summary["fg_ratio"] * 100))
        self.model.set_rows(rows)
        self.bars.set_rows(rows)
        # 柱状图跟随周期（BUG-7）：单日/本周只有 1~几天，单独画没有信息量，
        # 固定画目标日往前 7 天的窗口趋势；本月/近 30 天周期够长，直接跟随周期整体。
        if period in ("今日", "昨日", "按日"):
            anchor = date.fromisoformat(day_to)
            chart_from = (anchor - timedelta(days=6)).isoformat()
            self.daily_title.setText("%s 前后 7 天每日总时长" % day_to)
        else:
            chart_from = day_from
            self.daily_title.setText(period + "每日总时长")
        self.daily.set_days(self.store.daily_series(chart_from, day_to))
        self._update_hint(rows)

    def _update_hint(self, rows: list) -> None:
        """空状态必须说清原因，否则用户会以为程序坏了（实测反馈过）。"""
        if rows:
            self.hint.hide()
            return
        running = bool(self.daemon_alive()) if self.daemon_alive else True
        if not running:
            text = "守护进程未运行 —— 请双击「启动.bat」开始统计。"
        elif config.load().get("paused"):
            # 暂停时永远不会有新数据，不能再说"数据正在积累"误导人（BUG-30）
            text = "监测已暂停 —— 右键托盘图标选择「继续统计」后才会积累新数据。"
        else:
            text = ("守护进程运行中，数据正在积累（每 %d 秒落库一批），"
                    "本窗口每 %d 秒自动刷新，请稍候。" % (
                        int(config.FLUSH_INTERVAL_S), config.UI_REFRESH_INTERVAL_S))
        self.hint.setText(text)
        self.hint.show()

    def _export_csv(self) -> None:
        """把当前周期的统计导出成 Excel 可直接打开的 CSV。

        utf-8-sig（带 BOM）是为了 Excel 双击不乱码；全量导出，不受排行条数限制。
        """
        day_from, day_to = period_range(self.period.currentText(), day=self._picked_day())
        rows = self.store.app_totals(day_from, day_to, limit=0)
        if not rows:
            QMessageBox.information(self, "没有可导出的数据",
                                    "当前周期（%s ~ %s）还没有记录。" % (day_from, day_to))
            return
        period = self.period.currentText().replace(" ", "")
        default_name = "UsageWatcher_%s_%s_%s.csv" % (period, day_from, day_to)
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        default_path = os.path.join(desktop if os.path.isdir(desktop) else os.path.expanduser("~"),
                                    default_name)
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", default_path, "CSV 表格 (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(("统计周期", "%s ~ %s" % (day_from, day_to)))
            writer.writerows(csv_rows(rows))
        QMessageBox.information(self, "导出完成",
                                "已导出 %d 个应用：\n%s" % (len(rows), path))


def run(smoke: bool = False, daemon_alive=None) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    store = data.Store()
    window = MainWindow(store, daemon_alive=daemon_alive)
    window.show()
    if smoke:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2500, app.quit)
    code = app.exec()
    store.close()
    return code
