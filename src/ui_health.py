"""数据健康页（v0.5 第四 tab）：一天一条 24h 时间轴 + 断档归因 + 可信度摘要。

回答的问题：「某天数据怎么怪怪的」——界面上直接看到断档时段与原因
（空闲截断 / 锁屏停记 / 低活动 / 守护未运行），不用再查库对质。

分层：ui_* 末端，不许 SQL，取数只调 data.Store（day_grid / quality_stats）。"""

from __future__ import annotations

import time
from datetime import date, timedelta

from PySide6.QtCore import QDate, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QDateEdit, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

import i18n
from ui_charts import COLOR_MUTED, fmt_duration

_SLOT_W_MS = 5 * 60 * 1000
_COLORS = {0: QColor("#EDEBE4"), 1: QColor("#B4B2A9"), 2: QColor("#378ADD")}
_GAP_COLORS = {"lock": QColor("#E24B4A"), "idle": QColor("#EF9F27"),
               "quiet": QColor("#D3D1C7")}


class TimelineBar(QWidget):
    """24h 时间轴：每格 5 分钟，蓝=有前台、灰=仅后台、浅灰=无记录；
    内部断档在条下沿画橙色短标记。"""

    def __init__(self) -> None:
        super().__init__()
        self.slots: list = []
        self.gaps: list = []
        self.day_start = 0
        self.now_ratio = -1.0        # 今天才画"现在"竖线（比例 0~1）
        self.setMinimumHeight(56)

    def set_grid(self, grid: dict) -> None:
        self.slots = grid.get("slots") or []
        self.gaps = grid.get("gaps") or []
        self.day_start = 0
        if self.slots:
            try:
                self.day_start = int(time.mktime(time.strptime(
                    grid["day"], "%Y-%m-%d"))) * 1000
            except (ValueError, OverflowError):
                self.slots = []
        today = time.strftime("%Y-%m-%d")
        if grid.get("day") == today:
            self.now_ratio = (int(time.time() * 1000) - self.day_start) / 86400000.0
        else:
            self.now_ratio = -1.0
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if not self.slots:
            painter.setPen(QPen(COLOR_MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, i18n.t("nodata"))
            return
        bar = QRectF(0, 4, self.width(), 22)
        slot_w = bar.width() / len(self.slots)
        for index, level in enumerate(self.slots):
            painter.fillRect(QRectF(bar.x() + index * slot_w, bar.y(),
                                    slot_w + 0.5, bar.height()), _COLORS[level])
        painter.setFont(QFont("Microsoft YaHei", 7))
        painter.setPen(QPen(QColor("#EF9F27")))
        for gap in self.gaps:      # 断档标记：条下沿的短橙条
            x0 = gap["start_ms"] - self.day_start
            x1 = gap["end_ms"] - self.day_start
            painter.fillRect(QRectF(x0 / _SLOT_W_MS * slot_w, bar.bottom() + 5,
                                    max((x1 - x0) / _SLOT_W_MS * slot_w, 2), 4),
                             _GAP_COLORS.get(gap["reason"], _GAP_COLORS["quiet"]))
        painter.setPen(QPen(COLOR_MUTED))
        for hour, label in ((0, "00:00"), (6, "06:00"), (12, "12:00"),
                            (18, "18:00"), (24, "24:00")):
            painter.drawText(QRectF(hour / 24 * self.width() - 16, bar.bottom() + 10,
                                    40, 14), int(Qt.AlignHCenter), label)
        if 0 <= self.now_ratio <= 1:   # "现在"竖线（仅当天）
            painter.setPen(QPen(QColor("#E24B4A"), 1.5))
            x = bar.x() + bar.width() * self.now_ratio
            painter.drawLine(int(x), int(bar.y()), int(x), int(bar.bottom() + 4))
        painter.end()


class HealthPage(QWidget):
    """数据健康 tab。日期自带导航，统计页 reload 时会同步过来。"""

    def __init__(self, store, daemon_alive=None) -> None:
        super().__init__()
        self.store = store
        self.daemon_alive = daemon_alive
        self.day_pick = QDateEdit()
        self.day_pick.setCalendarPopup(True)
        self.day_pick.setDisplayFormat("yyyy-MM-dd")
        self.day_pick.setDate(QDate.currentDate())
        prev = QPushButton("◀")
        nxt = QPushButton("▶")
        prev.clicked.connect(lambda: self._shift(-1))
        nxt.clicked.connect(lambda: self._shift(1))
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(prev)
        top.addWidget(self.day_pick)
        top.addWidget(nxt)
        top.addStretch(1)
        self.timeline = TimelineBar()
        self.gaps = QLabel("")
        self.gaps.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.timeline)
        layout.addWidget(self.gaps)
        layout.addWidget(self.summary)
        layout.addStretch(1)
        self.day_pick.dateChanged.connect(lambda _d: self.reload())

    def _shift(self, days: int) -> None:
        self.day_pick.setDate(self.day_pick.date().addDays(days))

    def sync_day(self, day: str) -> None:
        """统计页周期变化时跟随（止日）；用户在健康页自选日期不被打断。"""
        target = QDate.fromString(day, "yyyy-MM-dd")
        if target.isValid() and target != self.day_pick.date():
            self.day_pick.setDate(target)

    def reload(self) -> None:
        day = self.day_pick.date().toString("yyyy-MM-dd")
        grid = self.store.day_grid(day)
        quality = self.store.quality_stats(day)
        self.timeline.set_grid(grid)
        lines = []
        for gap in grid["gaps"][:6]:
            lines.append(i18n.t("health_gap_%s" % gap["reason"],
                                _clock(gap["start_ms"]), _clock(gap["end_ms"])))
        self.gaps.setText("\n".join(lines))
        running = bool(self.daemon_alive()) if self.daemon_alive else True
        if not grid["slots"]:
            verdict = i18n.t("health_verdict_off" if not running else "health_verdict_empty")
        elif quality["errors"]:
            verdict = i18n.t("health_verdict_errors", quality["errors"])
        else:
            verdict = i18n.t("health_verdict_ok")
        covered = ""
        if grid["first_ms"]:
            covered = i18n.t("health_cover", _clock(grid["first_ms"]),
                             _clock(grid["last_ms"]),
                             fmt_duration(grid["last_ms"] - grid["first_ms"]))
        self.summary.setText(i18n.t("health_summary", quality["segments"],
                                    quality["apps"], quality["errors"])
                             + covered + verdict)


def _clock(ms: int) -> str:
    return time.strftime("%H:%M", time.localtime(ms / 1000))
