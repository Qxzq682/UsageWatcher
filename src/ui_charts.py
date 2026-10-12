"""统计页手绘图（v0.5 从 ui_main 拆出）：横向堆叠条 + 每日 Top5 趋势。

为什么不用 QtCharts：PySide6_Essentials 里没有 Qt6Charts.dll，import 直接
失败（实测）。引 PySide6-Addons 只为画条形图不划算。

分层：ui_* 末端，不许 SQL，取数只调 data.Store。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

import i18n

COLOR_FG = QColor("#378ADD")
COLOR_BG = QColor("#B4B2A9")
COLOR_TEXT = QColor("#2C2C2A")
COLOR_MUTED = QColor("#888780")

# Top5 趋势调色板：按名次取色（名次在周期内全局排名，颜色跟着应用走）
TREND_COLORS = ("#378ADD", "#5DCAA5", "#EF9F27", "#D4537E", "#7F77DD")


def asset_path(name: str) -> Path:
    """资源文件定位：源码运行 = 项目根/assets；打包运行 = 解包目录/assets。
    样式表与隐私说明是资源而非 Python 实现，放 assets 不占代码配额。"""
    base = getattr(sys, "_MEIPASS", None) \
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return Path(base) / "assets" / name


def apply_style(app) -> None:
    """给 QApplication 挂全局 QSS；缺失/读失败跳过（样式不致命，
    资源完整性由 selftest 断言兜底）。"""
    try:
        app.setStyleSheet(asset_path("style.qss").read_text(encoding="utf-8"))
    except OSError:
        pass


def fmt_duration(ms: int) -> str:
    minutes = int(ms // 60000)
    if minutes < 60:
        return i18n.t("dur_min", minutes)
    return i18n.t("dur_hm", minutes // 60, minutes % 60)


def _elide(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class StackedBar(QWidget):
    """各应用前/后台时长占比的横向堆叠条（QPainter 手绘）。"""

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
            painter.drawText(self.rect(), Qt.AlignCenter, i18n.t("nodata"))
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
    """每日时长柱状图（v0.5 升级为 Top5 前台拆分）。

    口径：柱高 = 当天 fg∪bg 并集（与统计页一致，BUG-27）；柱内自下而上
    堆叠**全局 Top5 应用**的前台时长色块 —— 前台天然互斥，跨应用堆叠安全；
    剩余部分灰底。图例由 legend_items() 交给宿主页面绘制。"""

    def __init__(self) -> None:
        super().__init__()
        self.days: list = []
        self.fg_by_day: dict = {}
        self._top: list = []
        self.setMinimumHeight(130)

    def set_days(self, days: list, fg_by_day: dict) -> None:
        self.days = days
        self.fg_by_day = fg_by_day
        totals: dict = {}
        for labels in fg_by_day.values():
            for label, ms in labels.items():
                totals[label] = totals.get(label, 0) + ms
        self._top = sorted(totals, key=totals.get, reverse=True)[:5]
        self.update()

    def legend_items(self) -> list:
        """[(颜色, 应用名)]，按柱内堆叠顺序（名次）。"""
        return [(QColor(TREND_COLORS[i]), label) for i, label in enumerate(self._top)]

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if not self.days:
            painter.setPen(QPen(COLOR_MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, i18n.t("nodata"))
            return
        peak = max((d["active_ms"] for d in self.days), default=1) or 1
        slot = self.width() / max(len(self.days), 1)
        bar_width = min(slot * 0.5, 34)
        base = self.height() - 20

        painter.setFont(QFont("Microsoft YaHei", 8))
        for index, day in enumerate(self.days):
            center = slot * (index + 0.5)
            total = day["active_ms"]
            height = max((base - 10) * total / peak, 2)
            x = center - bar_width / 2
            painter.fillRect(QRectF(x, base - height, bar_width, height), COLOR_BG)
            parts = self.fg_by_day.get(day["day"], {})
            y = base
            for rank, label in enumerate(self._top):
                fg_ms = parts.get(label, 0)
                if not fg_ms:
                    continue
                fg_height = min(height * fg_ms / total if total else 0, y - (base - height))
                if fg_height <= 0:
                    continue
                y -= fg_height
                painter.fillRect(QRectF(x, y, bar_width, fg_height),
                                 QColor(TREND_COLORS[rank]))
            painter.setPen(QPen(COLOR_MUTED))
            painter.drawText(QRectF(center - 24, base + 2, 48, 16),
                             int(Qt.AlignHCenter | Qt.AlignTop), day["day"][5:])
        painter.end()
