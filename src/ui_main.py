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

from PySide6.QtCore import QAbstractTableModel, QDate, QFileInfo, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QFileDialog, QFileIconProvider, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QMainWindow, QMessageBox, QPushButton, QTableView,
    QTabWidget, QVBoxLayout, QWidget,
)

import apps
import config
import data
import i18n
import ui_merge
from ui_charts import DailyBars, StackedBar, fmt_duration

PERIOD_KEYS = ("today", "yesterday", "byday", "week", "month", "30d")

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
    out = [(i18n.t("hdr_app"), i18n.t("hdr_exe"), i18n.t("hdr_path"),
            i18n.t("hdr_fg_min"), i18n.t("hdr_bg_min"),
            i18n.t("hdr_total_min"), i18n.t("hdr_fg_pct_c"))]
    for r in rows:
        out.append((
            _csv_safe(r["display_name"]),
            _csv_safe(r["exe_name"]),
            _csv_safe(r["exe_path"]),
            round(r["fg_ms"] / 60000, 1),
            round(r["bg_ms"] / 60000, 1),
            round(r["total_ms"] / 60000, 1),
            round(r["fg_ratio"] * 100, 1),
        ))
    return out


# ---------------------------------------------------------------- 周期计算

def period_label(key: str) -> str:
    return i18n.t("period_" + key)


def period_range(key: str, day=None) -> tuple:
    """周期 key → (起日, 止日) ISO 串。day 是"按日"模式的指定日期（date 对象），
    缺省昨天；其它周期忽略 day（纯函数，便于回归）。"""
    today = date.today()
    if key == "yesterday":
        y = today - timedelta(days=1)
        return y.isoformat(), y.isoformat()
    if key == "byday":
        d = day or (today - timedelta(days=1))
        if isinstance(d, str):
            d = date.fromisoformat(d)
        return d.isoformat(), d.isoformat()
    if key == "today":
        return today.isoformat(), today.isoformat()
    if key == "week":
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat()
    if key == "month":
        return today.replace(day=1).isoformat(), today.isoformat()
    return (today - timedelta(days=29)).isoformat(), today.isoformat()


# ---------------------------------------------------------------- 排行表

class RankModel(QAbstractTableModel):
    @staticmethod
    def _headers() -> tuple:
        return (i18n.t("hdr_app"), i18n.t("hdr_fg"), i18n.t("hdr_bg"),
                i18n.t("hdr_total"), i18n.t("hdr_fg_pct"))

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
        return len(self._headers())

    def headerData(self, section, orientation, _role=Qt.DisplayRole):
        if orientation != Qt.Horizontal:
            return None
        if _role == Qt.ToolTipRole:
            return i18n.t("tip_hdr_total")
        headers = self._headers()
        return headers[section] if _role == Qt.DisplayRole else None

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


# ---------------------------------------------------------------- 主窗口

class MainWindow(QMainWindow):
    def __init__(self, store, daemon_alive=None) -> None:
        super().__init__()
        self.store = store
        self.daemon_alive = daemon_alive
        self.setWindowTitle("%s v%s" % (config.APP_TITLE, config.APP_VERSION))
        self.resize(920, 620)

        self.period = QComboBox()
        self.period.addItems([period_label(k) for k in PERIOD_KEYS])
        self.day_pick = QDateEdit()            # "按日"模式的日期选择（带日历弹窗）
        self.day_pick.setCalendarPopup(True)
        self.day_pick.setDisplayFormat("yyyy-MM-dd")
        self.day_pick.setDate(QDate.currentDate().addDays(-1))
        self.day_pick.dateChanged.connect(lambda _d: self.reload())
        self.day_pick.hide()
        refresh = QPushButton(i18n.t("btn_refresh"))
        refresh.clicked.connect(self.reload)
        export = QPushButton(i18n.t("btn_export"))
        export.clicked.connect(self._export_csv)
        monthly = QPushButton(i18n.t("btn_monthly"))
        monthly.clicked.connect(self._export_monthly)

        top = QHBoxLayout()
        top.addWidget(QLabel(i18n.t("lbl_period")))
        top.addWidget(self.period)
        top.addWidget(self.day_pick)
        top.addWidget(refresh)
        top.addWidget(export)
        top.addWidget(monthly)
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
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)

        self.bars = StackedBar()
        self.daily = DailyBars()
        self.daily_legend = QLabel("")
        self.daily_legend.setTextFormat(Qt.RichText)
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
        stats_layout.addWidget(QLabel(i18n.t("chart_note")))
        stats_layout.addWidget(self.bars)
        stats_layout.addWidget(self.table, 1)
        self.daily_title = QLabel(i18n.t("daily_title"))
        stats_layout.addWidget(self.daily_title)
        stats_layout.addWidget(self.daily)
        stats_layout.addWidget(self.daily_legend)

        import ui_health
        import ui_scope
        self.tabs = QTabWidget()
        self.tabs.addTab(stats_tab, i18n.t("tab_stats"))
        self.tabs.addTab(ui_health.HealthPage(self.store, daemon_alive=self.daemon_alive),
                         i18n.t("tab_health"))
        self.tabs.addTab(ui_scope.ScopePage(self.store), i18n.t("tab_scope"))
        self.tabs.addTab(ui_scope.PrivacyPage(self.store), i18n.t("tab_settings"))

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
        key = PERIOD_KEYS[self.period.currentIndex()]
        self.day_pick.setVisible(key == "byday")
        day_from, day_to = period_range(key, day=self._picked_day())
        self.app_paths = self.store.app_paths()
        merge_map = apps.build_merge_map(self.app_paths,
                                         overrides=config.load()["merge_overrides"])
        rows = self.store.app_totals(day_from, day_to, limit=config.load()["rank_limit"],
                                     merge_map=merge_map)
        summary = self.store.totals_summary(day_from, day_to)
        quality = self.store.quality_stats(day_to)
        self.summary.setText(i18n.t("summary_line",
                                    fmt_duration(summary["total_ms"]),
                                    fmt_duration(summary["fg_ms"]),
                                    summary["fg_ratio"] * 100)
                             + i18n.t("quality_line", quality["segments"],
                                      quality["apps"], quality["errors"]))
        self.model.set_rows(rows)
        self.bars.set_rows(rows)
        self._merge_map = merge_map
        # 柱状图跟随周期（BUG-7）：单日/本周只有 1~几天，单独画没有信息量，
        # 固定画目标日往前 7 天的窗口趋势；本月/近 30 天周期够长，直接跟随周期整体。
        if key in ("today", "yesterday", "byday"):
            anchor = date.fromisoformat(day_to)
            chart_from = (anchor - timedelta(days=6)).isoformat()
            self.daily_title.setText(i18n.t("daily_around", day_to))
        else:
            chart_from = day_from
            self.daily_title.setText(i18n.t("daily_follow",
                                            self.period.currentText()))
        self.daily.set_days(self.store.daily_series(chart_from, day_to),
                            self.store.daily_fg(chart_from, day_to, merge_map))
        self.daily_legend.setText(i18n.t("trend_legend") + "　" + "　".join(
            '<span style="color:%s">■</span>%s' % (color.name(), label)
            for color, label in self.daily.legend_items()))
        self.tabs.widget(1).sync_day(day_to)      # 数据健康页跟随止日
        self._update_hint(rows)

    def _row_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = self.model.rows[index.row()]
        from PySide6.QtGui import QCursor
        ui_merge.show_menu(self, QCursor.pos(), row, self._merge_map,
                           self.app_paths, on_done=self.reload)

    def _update_hint(self, rows: list) -> None:
        """空状态必须说清原因，否则用户会以为程序坏了（实测反馈过）。"""
        if rows:
            self.hint.hide()
            return
        running = bool(self.daemon_alive()) if self.daemon_alive else True
        if not running:
            text = i18n.t("hint_not_running")
        elif config.load().get("paused"):
            # 暂停时永远不会有新数据，不能再说"数据正在积累"误导人（BUG-30）
            text = i18n.t("hint_paused")
        else:
            text = i18n.t("hint_accumulating",
                          int(config.FLUSH_INTERVAL_S), config.UI_REFRESH_INTERVAL_S)
        self.hint.setText(text)
        self.hint.show()

    def _export_csv(self) -> None:
        """把当前周期的统计导出成 Excel 可直接打开的 CSV。

        utf-8-sig（带 BOM）是为了 Excel 双击不乱码；全量导出，不受排行条数限制。
        """
        day_from, day_to = period_range(PERIOD_KEYS[self.period.currentIndex()],
                                        day=self._picked_day())
        self._write_csv(day_from, day_to, self.period.currentText().replace(" ", ""))

    def _export_monthly(self) -> None:
        """月度汇总自选导出（v0.5）：选月份 → 系统对话框选保存位置。"""
        today = date.today()
        months = [(today.replace(day=1) - timedelta(days=30 * i)).strftime("%Y-%m")
                  for i in range(24)]
        choice, ok = QInputDialog.getItem(self, i18n.t("monthly_title"),
                                          i18n.t("monthly_prompt"), months, 0, False)
        if not ok:
            return
        year, month = int(choice[:4]), int(choice[5:7])
        day_from = date(year, month, 1).isoformat()
        nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        day_to = min(nxt - timedelta(days=1), today).isoformat()
        self._write_csv(day_from, day_to, choice)

    def _write_csv(self, day_from: str, day_to: str, tag: str) -> None:
        rows = self.store.app_totals(day_from, day_to, limit=0)
        if not rows:
            QMessageBox.information(self, i18n.t("export_none_title"),
                                    i18n.t("export_none", day_from, day_to))
            return
        default_name = "UsageWatcher_%s_%s_%s.csv" % (tag, day_from, day_to)
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        default_path = os.path.join(desktop if os.path.isdir(desktop) else os.path.expanduser("~"),
                                    default_name)
        path, _ = QFileDialog.getSaveFileName(
            self, i18n.t("export_dialog_title"), default_path, i18n.t("export_filter"))
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow((i18n.t("csv_period_row"), "%s ~ %s" % (day_from, day_to)))
            writer.writerows(csv_rows(rows))
        QMessageBox.information(self, i18n.t("export_done_title"),
                                i18n.t("export_done", len(rows), path))


def run(smoke: bool = False, daemon_alive=None) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    store = data.Store()
    import ui_overlay
    ui_overlay.restore_if_enabled()
    window = MainWindow(store, daemon_alive=daemon_alive)
    window.show()
    if smoke:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2500, app.quit)
    code = app.exec()
    store.close()
    return code
