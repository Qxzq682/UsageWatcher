"""监测范围页 + 隐私与数据页（PySide6）。

「不可监测」的强制锁定在这里体现为第 1 层加固：勾选框**物理不可点**。
另有 3 层兜底在别处（配置层不占最终决定 / 采集层过滤 / 测试断言），
所以就算有人绕过界面也没用。

本文件不许出现任何 SQL（tools/test_arch.py 会断言）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout, QWidget,
)

import config

LEVEL_TEXT = {"yes": "可监测", "partial": "有限监测", "no": "不可监测"}
LEVEL_MARK = {"yes": "[可监测]", "partial": "[有限监测]", "no": "[不可监测] 锁定"}
LEVEL_COLOR = {"yes": QColor("#3B6D11"), "partial": QColor("#854F0B"), "no": QColor("#A32D2D")}

CATEGORY_TEXT = {
    "third_party": "第三方软件",
    "windows": "Windows 自带与外设",
    "system": "系统组件与服务",
    "suggest_exclude": "建议排除",
}
CATEGORY_ORDER = ["全部", "第三方软件", "Windows 自带与外设", "系统组件与服务", "建议排除"]


class ScopePage(QWidget):
    """逐软件勾选；不可监测项灰显锁死。"""

    HEADERS = ("应用", "发布者", "能否监测", "是否监测", "说明")

    def __init__(self, store) -> None:
        super().__init__()
        self.store = store
        self._rows: list = []

        self.filter = QComboBox()
        self.filter.addItems(CATEGORY_ORDER)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索应用名…")
        self.btn_all = QPushButton("全选第三方")
        self.btn_none = QPushButton("全部取消")
        self.btn_recheck = QPushButton("重新检测")
        self.status = QLabel("")

        top = QHBoxLayout()
        top.addWidget(self.filter)
        top.addWidget(self.search, 1)
        top.addWidget(self.btn_all)
        top.addWidget(self.btn_none)
        top.addWidget(self.btn_recheck)

        self.table = QTableWidget(0, len(self.HEADERS))
        self.table.setHorizontalHeaderLabels(self.HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        for column in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "打勾 = 记录这个软件的时长。标为「不可监测」的行已被锁定 —— "
            "技术上拿不到归属信息（系统拒绝访问），所以无法勾选。"))
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)

        self.filter.currentTextChanged.connect(self.reload)
        self.search.textChanged.connect(self.reload)
        self.btn_all.clicked.connect(lambda: self._bulk(True))
        self.btn_none.clicked.connect(lambda: self._bulk(False))
        self.btn_recheck.clicked.connect(self.reload)
        self.table.itemChanged.connect(self._on_item_changed)
        self.reload()

    # ---- 数据 ----

    def reload(self) -> None:
        rows = self.store.capability_rows()
        wanted = self.filter.currentText()
        keyword = self.search.text().strip().lower()

        self._rows = []
        for row in rows:
            name = row["display_name"] or row["exe_name"] or row["app_key"]
            category = CATEGORY_TEXT.get(row["category"], row["category"] or "")
            if wanted != "全部" and category != wanted:
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

            level_item = QTableWidgetItem("%s  %s" % (LEVEL_MARK[level], LEVEL_TEXT[level]))
            level_item.setForeground(LEVEL_COLOR[level])
            level_item.setToolTip(reason or "可以正常监测")
            self.table.setItem(index, 2, level_item)

            # ★ 强制不可更改：不可监测项**不注册 ItemIsUserCheckable**，
            # 所以界面上根本不会出现可点的勾选框（第 1 层加固）
            check = QTableWidgetItem()
            check.setData(Qt.UserRole, row["app_key"])
            if is_locked:
                check.setFlags(Qt.ItemIsSelectable)
                check.setText("[锁]")
                check.setForeground(LEVEL_COLOR["no"])
                check.setToolTip(reason)
            else:
                check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
                check.setCheckState(Qt.Checked if row["enabled"] else Qt.Unchecked)
                checked += 1 if row["enabled"] else 0
            self.table.setItem(index, 3, check)

            note = QTableWidgetItem(reason or category)
            note.setToolTip(reason)
            if not reason:
                note.setForeground(QColor("#888780"))
            self.table.setItem(index, 4, note)
        self.table.blockSignals(False)

        self.status.setText("显示 %d 个软件 · 已勾选 %d 个 · 不可监测 %d 个（已自动锁定，无法勾选）"
                            % (len(self._rows), checked, locked))

    # ---- 交互 ----

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
                self, "无法启用",
                "这个程序技术上无法监测，所以不能勾选。\n\n原因：%s" % (item.toolTip() or "系统拒绝访问"))
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
    """把「采集了什么 / 绝不采集什么 / 数据放哪 / 怎么删」摊开给用户看。"""

    COLLECTED = [
        ("前台窗口句柄 → 进程 ID", "判断哪个程序在前台", "不落库（瞬时值）"),
        ("程序可执行文件全路径", "标识是哪个软件", "落库"),
        ("版本资源：产品名 / 版本 / 发布者", "界面上显示可读名称", "落库"),
        ("进程 CPU 时间、磁盘读写字节数", "判断后台是否真有活动", "不落库（仅差分）"),
        ("距上次输入的毫秒数", "空闲时自动降频省电", "不落库"),
        ("时间戳（起止）", "算时长", "落库"),
    ]

    NEVER = ("窗口标题、控件文字", "网址 / 网页内容", "聊天内容", "键盘输入", "鼠标轨迹",
             "剪贴板", "截屏 / 录屏", "文件内容与文件名（exe 自身路径除外）",
             "浏览器历史 / 书签 / 密码", "麦克风 / 摄像头", "网络流量内容",
             "账号 / 机器名 / IP / 位置", "任何遥测或崩溃上报")

    def __init__(self, store) -> None:
        super().__init__()
        self.store = store
        view = QTextBrowser()
        view.setOpenExternalLinks(False)
        view.setHtml(self._html())

        clear = QPushButton("清空全部数据")
        clear.clicked.connect(self._clear)
        open_dir = QPushButton("打开数据目录")
        open_dir.clicked.connect(self._open_data_dir)

        buttons = QHBoxLayout()
        buttons.addWidget(clear)
        buttons.addWidget(open_dir)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(view, 1)
        layout.addLayout(buttons)

    def _open_data_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.data_dir())))

    def _html(self) -> str:
        collected = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % row for row in self.COLLECTED)
        never = "".join("<li>%s</li>" % item for item in self.NEVER)
        return f"""
        <h3>本程序采集什么</h3>
        <table border="1" cellspacing="0" cellpadding="4">
          <tr><th>字段</th><th>用途</th><th>是否落库</th></tr>
          {collected}
        </table>

        <h3>绝不采集</h3>
        <ul>{never}</ul>

        <h3>数据存在哪</h3>
        <p><code>{config.data_dir()}</code><br>
        只写入这个目录（当前用户的私有目录），<b>不会写程序目录</b>，
        也不会修改任何系统设置或注册表项（开机自启默认关闭，需要你主动勾选）。</p>

        <h3>不联网</h3>
        <p>本程序<b>不包含任何联网代码</b>，不监听任何端口，不上传任何数据。
        你可以自己验证：关掉程序外的一切干扰，运行
        <code>tools\\audit.py</code>，它会扫描源码里的网络调用并检查本进程是否在监听端口。</p>

        <h3>怎么彻底删除</h3>
        <p>点下面的「清空全部数据」删除所有记录；或直接删除整个目录
        <code>{config.data_dir()}</code>。</p>
        """

    def _clear(self) -> None:
        answer = QMessageBox.question(
            self, "确认清空",
            "将删除全部使用时长记录，且不可恢复。\n\n确定继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.store.clear_all()
        QMessageBox.information(self, "已完成", "数据已清空。")
