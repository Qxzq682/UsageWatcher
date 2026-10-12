"""监测范围页 + 隐私与数据页（PySide6）。

「不可监测」的强制锁定在这里体现为第 1 层加固：勾选框**物理不可点**。
另有 3 层兜底在别处（配置层不占最终决定 / 采集层过滤 / 测试断言），
所以就算有人绕过界面也没用。

本文件不许出现任何 SQL（tools/test_arch.py 会断言）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

import config
import i18n
import winutil
from ui_charts import asset_path

LEVEL_COLOR = {"yes": QColor("#3B6D11"), "partial": QColor("#854F0B"), "no": QColor("#A32D2D")}

# 业务键 = 数据库 category 值（逻辑判断只用 key，显示经 i18n.t("cat_"+key)）
CATEGORY_KEYS = ("all", "third_party", "windows", "system", "suggest_exclude")


class ScopePage(QWidget):
    """逐软件勾选；不可监测项灰显锁死。"""

    def __init__(self, store) -> None:
        super().__init__()
        self.store = store
        self._rows: list = []

        self.filter = QComboBox()
        self.filter.addItems([i18n.t("cat_" + k) for k in CATEGORY_KEYS])
        self.search = QLineEdit()
        self.search.setPlaceholderText(i18n.t("search_ph"))
        self.btn_all = QPushButton(i18n.t("btn_select_third"))
        self.btn_none = QPushButton(i18n.t("btn_deselect_all"))
        self.btn_recheck = QPushButton(i18n.t("btn_recheck"))
        self.status = QLabel("")

        top = QHBoxLayout()
        top.addWidget(self.filter)
        top.addWidget(self.search, 1)
        top.addWidget(self.btn_all)
        top.addWidget(self.btn_none)
        top.addWidget(self.btn_recheck)

        headers = (i18n.t("hdr_app"), i18n.t("hdr_publisher"), i18n.t("hdr_monitorable"),
                   i18n.t("hdr_tracked"), i18n.t("hdr_note"))
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(i18n.t("scope_note")))
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)

        self.filter.currentIndexChanged.connect(self.reload)
        self.search.textChanged.connect(self.reload)
        self.btn_all.clicked.connect(lambda: self._bulk(True))
        self.btn_none.clicked.connect(lambda: self._bulk(False))
        self.btn_recheck.clicked.connect(self._recheck)
        self.table.itemChanged.connect(self._on_item_changed)
        self.reload()

    # ---- 数据 ----

    def reload(self) -> None:
        rows = self.store.capability_rows()
        wanted = CATEGORY_KEYS[self.filter.currentIndex()]
        keyword = self.search.text().strip().lower()

        self._rows = []
        for row in rows:
            name = row["display_name"] or row["exe_name"] or row["app_key"]
            category = row["category"] or ""
            if wanted != "all" and category != wanted:
                continue
            if keyword and keyword not in name.lower() and keyword not in (row["app_key"] or "").lower():
                continue
            self._rows.append((row, name, category))

        self.table.blockSignals(True)
        self.table.setRowCount(len(self._rows))
        locked = 0
        checked = 0
        for index, (row, name, category) in enumerate(self._rows):
            level = str(row["level"])
            reason = row["reason"] or ""
            is_locked = level == "no"
            if is_locked:
                locked += 1

            name_item = QTableWidgetItem(name)
            name_item.setToolTip(row["exe_path"] or row["app_key"])
            self.table.setItem(index, 0, name_item)
            self.table.setItem(index, 1, QTableWidgetItem(row["publisher"] or "—"))

            level_text, level_mark = i18n.t("lvl_" + level), i18n.t("mark_" + level)
            level_item = QTableWidgetItem("%s  %s" % (level_mark, level_text))
            level_item.setForeground(LEVEL_COLOR[level])
            level_item.setToolTip(reason or i18n.t("monitor_ok"))
            self.table.setItem(index, 2, level_item)

            # ★ 强制不可更改：不可监测项**不注册 ItemIsUserCheckable**，
            # 所以界面上根本不会出现可点的勾选框（第 1 层加固）
            check = QTableWidgetItem()
            check.setData(Qt.UserRole, row["app_key"])
            if is_locked:
                check.setFlags(Qt.ItemIsSelectable)
                check.setText(i18n.t("locked_mark"))
                check.setForeground(LEVEL_COLOR["no"])
                check.setToolTip(reason)
            else:
                check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                check.setCheckState(Qt.Checked if row["enabled"] else Qt.Unchecked)
                checked += 1 if row["enabled"] else 0
            self.table.setItem(index, 3, check)

            note = QTableWidgetItem(reason or (i18n.t("cat_" + category) if category else ""))
            note.setToolTip(reason)
            if not reason:
                note.setForeground(QColor("#888780"))
            self.table.setItem(index, 4, note)
        self.table.blockSignals(False)

        self.status.setText(i18n.t("status_line", len(self._rows), checked, locked))

    # ---- 交互 ----

    def _recheck(self) -> None:
        """真重置（BUG-6）：把误判的「不可监测」降回待重检，而不是只重画表格。"""
        reset = self.store.reset_recheckable()
        self.reload()
        if reset:
            QMessageBox.information(
                self, i18n.t("recheck_title"), i18n.t("recheck_done", reset))
        else:
            QMessageBox.information(
                self, i18n.t("recheck_title"), i18n.t("recheck_none"))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 3:
            return
        app_key = item.data(Qt.UserRole)
        if not app_key:
            return
        want = item.checkState() == Qt.Checked
        if not self.store.set_enabled(app_key, want):
            # 被拒绝 = 该项不可监测。把界面拨回去，并说明原因（不允许静默失败）
            self.table.blockSignals(True)
            item.setCheckState(Qt.Unchecked)
            self.table.blockSignals(False)
            QMessageBox.information(
                self, i18n.t("enable_fail_title"),
                i18n.t("enable_fail", item.toolTip() or i18n.t("access_denied")))
            return
        self.reload()

    def _bulk(self, enable: bool) -> None:
        changed = 0
        for row, _name, _category in self._rows:
            if str(row["level"]) == "no":
                continue                       # 不可监测项永远不参与批量操作
            if bool(row["enabled"]) == enable:
                continue
            if self.store.set_enabled(row["app_key"], enable):
                changed += 1
        self.reload()
        self.status.setText(self.status.text() + "　（本次改动 %d 个）" % changed)


class PrivacyPage(QWidget):
    """设置页：采集了什么 / 绝不采集什么 / 数据放哪 / 怎么删，以及
    开机自启、界面语言、桌面悬浮窗与卸载入口。卸载走项目根目录的
    卸载.bat（预览先行、逐项确认、全部送回收站），点按钮后本窗口
    自动退出给向导让路。"""

    def __init__(self, store) -> None:
        super().__init__()
        self.store = store
        view = QTextBrowser()
        view.setOpenExternalLinks(False)
        view.setHtml(self._html())

        clear = QPushButton(i18n.t("btn_clear"))
        clear.clicked.connect(self._clear)
        open_dir = QPushButton(i18n.t("btn_open_dir"))
        open_dir.clicked.connect(self._open_data_dir)

        # 开机自启（v0.2 B2）：默认不写（R6），勾选才写 HKCU 自己的 Run 项
        self.autostart = QCheckBox(i18n.t("chk_autostart"))
        self.autostart.setChecked(winutil.autostart_enabled())
        self.autostart.toggled.connect(self._set_autostart)

        # 界面语言（v0.3）：保存偏好，重启统计窗口后生效
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel(i18n.t("lbl_language")))
        self.lang_pick = QComboBox()
        self.lang_pick.addItems(["简体中文", "English"])   # 语言名固定用本语言书写
        self.lang_pick.setCurrentIndex(0 if i18n.current() == "zh" else 1)
        self.lang_pick.currentIndexChanged.connect(self._set_language)
        lang_row.addWidget(self.lang_pick)
        lang_row.addWidget(QLabel(i18n.t("lang_restart_note")))
        lang_row.addStretch(1)

        # 桌面悬浮窗（v0.3）：独立进程，默认关；取消勾选经 overlay.flag 优雅退出
        self.overlay_chk = QCheckBox(i18n.t("chk_overlay"))
        self.overlay_chk.setChecked(bool(config.load().get("overlay_enabled")))
        self.overlay_chk.toggled.connect(self._set_overlay)
        overlay_note = QLabel(i18n.t("overlay_note"))
        overlay_note.setStyleSheet("color:#888780;")

        # 软件管理（v0.2 C3）：卸载入口集成进软件，不再要用户去翻 tools 目录
        manage = QHBoxLayout()
        manage.addWidget(QLabel(i18n.t("lbl_manage")))
        self.uninstall_btn = QPushButton(i18n.t("btn_uninstall"))
        self.uninstall_btn.clicked.connect(self._uninstall)
        manage.addWidget(self.uninstall_btn)
        manage.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addWidget(clear)
        buttons.addWidget(open_dir)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(view, 1)
        layout.addWidget(self.autostart)
        layout.addLayout(lang_row)
        layout.addWidget(self.overlay_chk)
        layout.addWidget(overlay_note)
        layout.addLayout(buttons)
        layout.addLayout(manage)

    def _set_language(self, index: int) -> None:
        lang = "zh" if index == 0 else "en"
        try:
            config.save({**config.load(), "ui_language": lang})
        except OSError:
            self._rollback(self.lang_pick, 0 if i18n.current() == "zh" else 1)
            QMessageBox.warning(self, i18n.t("set_fail_title"), i18n.t("cfg_save_fail"))

    def _set_overlay(self, on: bool) -> None:
        try:
            config.save({**config.load(), "overlay_enabled": bool(on)})
        except OSError:
            self._rollback(self.overlay_chk, not on)
            QMessageBox.warning(self, i18n.t("set_fail_title"), i18n.t("cfg_save_fail"))
            return
        if on:
            import ui_overlay
            ui_overlay.launch_overlay()
        else:
            config.overlay_flag_path().write_text("", encoding="utf-8")

    @staticmethod
    def _rollback(widget, checked) -> None:
        """失败必须回滚 UI（R8），不许让勾选状态与事实不符。"""
        widget.blockSignals(True)
        widget.setChecked(bool(checked))
        widget.blockSignals(False)

    def _uninstall(self) -> None:
        """软件内卸载入口：确认后启动卸载向导（tools/uninstall.py，经卸载.bat），
        本窗口退出让路。删除一律送回收站、向导里逐项确认（R8），这里不做静默卸载。"""
        if getattr(sys, "frozen", False):
            QMessageBox.information(
                self, i18n.t("un_title"), i18n.t("un_frozen_msg", config.data_dir()))
            return
        root = Path(config.__file__).resolve().parent.parent
        bat = root / "卸载.bat"
        if not bat.exists():
            QMessageBox.warning(self, i18n.t("un_title"), i18n.t("un_bat_missing", bat))
            return
        answer = QMessageBox.warning(
            self, i18n.t("un_title"), i18n.t("un_confirm", config.data_dir()),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        os.startfile(str(bat))   # nosec B606 —— 路径由本程序常量拼出，非用户输入
        QApplication.quit()

    def _set_autostart(self, on: bool) -> None:
        if winutil.set_autostart(on):
            return
        # 失败必须回滚 UI 并可见（R8），不许让勾选状态与注册表事实不符
        self._rollback(self.autostart, winutil.autostart_enabled())
        QMessageBox.warning(self, i18n.t("set_fail_title"), i18n.t("set_fail_msg"))

    def _open_data_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.data_dir())))

    def _html(self) -> str:
        """隐私说明按当前语言从 assets 读取（资源而非代码，v0.6）；%s ×2 = 数据目录。
        读失败显示占位说明而不是崩设置页（R8：资源缺失可见、功能不炸）。"""
        name = "privacy_en.html" if i18n.current() == "en" else "privacy_zh.html"
        try:
            text = asset_path(name).read_text(encoding="utf-8")
        except OSError:
            text = "<p>privacy notes asset missing — reinstall the app.</p>"
        return text % (config.data_dir(), config.data_dir())

    def _clear(self) -> None:
        answer = QMessageBox.question(
            self, i18n.t("clear_title"), i18n.t("clear_confirm"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.store.clear_all()
        QMessageBox.information(self, i18n.t("clear_done_title"), i18n.t("clear_done"))
