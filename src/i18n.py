"""界面文案 i18n（v0.3）：中英双语，key 单点、语言随 config.json 重启生效。

规则（tools/test_arch.py + tools/selftest.py 会断言）：
1. 本模块只许 import config，不许碰 data/track/collect/PySide6；
2. 新增文案必须 zh/en 同时加（selftest 断言两表 key 集合一致，缺一即失败）；
3. 业务逻辑**不许用显示文案做判断**（中文串是展示层，英文切换后必须照常工作）
   —— 周期/分类/等级一律用英文 key（如 period_today），文案只经 t() 取出。
"""

from __future__ import annotations

import config

_ZH = {
    "app_title": "软件使用时长监测",
    "dur_min": "%d 分", "dur_hm": "%d 小时 %02d 分",
    "daemon_running": "%s v%s 正在后台采集。托盘图标可右键操作。",
    "already_running": "程序已在运行，正在打开统计窗口…",
    "tip_paused": "已暂停统计",
    "tip_today": "%s\n今日活跃 %s（前台 %s · 后台 %s）",
    "tip_errors": "\n[!] 采集异常 %d 次",
    "tray_summary": "今日前台 %s · 后台 %s",
    "tray_open": "打开统计窗口", "tray_pause": "暂停统计",
    "tray_resume": "继续统计", "tray_quit": "退出",
    "period_today": "今日", "period_yesterday": "昨日", "period_byday": "按日",
    "period_week": "本周", "period_month": "本月", "period_30d": "近 30 天",
    "tab_stats": "统计", "tab_scope": "监测范围", "tab_settings": "设置",
    "btn_refresh": "刷新", "btn_export": "导出 CSV", "lbl_period": "统计周期",
    "hdr_app": "应用", "hdr_fg": "前台", "hdr_bg": "后台活跃",
    "hdr_total": "活跃总时长(去重)", "hdr_fg_pct": "前台占比",
    "hdr_exe": "可执行文件", "hdr_fg_min": "前台(分钟)",
    "hdr_bg_min": "后台活跃(分钟)", "hdr_total_min": "活跃总时长(分钟)",
    "hdr_fg_pct_c": "前台占比%", "hdr_publisher": "发布者",
    "hdr_monitorable": "能否监测", "hdr_tracked": "是否监测", "hdr_note": "说明",
    "tip_hdr_total": "活跃总时长=前台∪后台去重；多进程应用前后台会重叠，故小于相加",
    "nodata": "暂无数据",
    "chart_note": "各应用前/后台时长（蓝=前台，灰=后台活跃）。"
                  "活跃总时长=前后台并集去重，多进程应用前后台重叠，不等于相加",
    "daily_title": "每日总时长",
    "daily_around": "%s 前后 7 天每日总时长",
    "daily_follow": "%s每日总时长",
    "summary_line": "活跃总时长 %s　其中前台 %s（%.0f%%）",
    "hint_not_running": "守护进程未运行（打开窗口时已尝试自动拉起）—— "
                        "若长时间没有变化，请双击「启动.bat」。",
    "hint_paused": "监测已暂停 —— 右键托盘图标选择「继续统计」后才会积累新数据。",
    "hint_accumulating": "守护进程运行中，数据正在积累（每 %d 秒落库一批），"
                         "本窗口每 %d 秒自动刷新，请稍候。",
    "export_none_title": "没有可导出的数据",
    "export_none": "当前周期（%s ~ %s）还没有记录。",
    "export_dialog_title": "导出 CSV", "export_filter": "CSV 表格 (*.csv)",
    "csv_period_row": "统计周期",
    "export_done_title": "导出完成",
    "export_done": "已导出 %d 个应用：\n%s",
    "lvl_yes": "可监测", "lvl_partial": "有限监测", "lvl_no": "不可监测",
    "mark_yes": "[可监测]", "mark_partial": "[有限监测]", "mark_no": "[不可监测] 锁定",
    "cat_all": "全部", "cat_third_party": "第三方软件",
    "cat_windows": "Windows 自带与外设", "cat_system": "系统组件与服务",
    "cat_suggest_exclude": "建议排除",
    "search_ph": "搜索应用名…", "btn_select_third": "全选第三方",
    "btn_deselect_all": "全部取消", "btn_recheck": "重新检测",
    "scope_note": "打勾 = 记录这个软件的时长。标为「不可监测」的行已被锁定 —— "
                  "技术上拿不到归属信息（系统拒绝访问），所以无法勾选。\n"
                  "「重新检测」会把误判的项放回待重检状态，守护进程下次探测到时自动重新定性"
                  "（系统组件保持锁死）。",
    "status_line": "显示 %d 个软件 · 已勾选 %d 个 · 不可监测 %d 个（已自动锁定，无法勾选）",
    "bulk_changed": "　（本次改动 %d 个）",
    "monitor_ok": "可以正常监测", "locked_mark": "[锁]",
    "recheck_title": "重新检测",
    "recheck_done": "已把 %d 个「不可监测」项重置为待重检状态。\n\n"
                    "守护进程下次探测到它们时会自动重新定性；若仍读不到，会如实写回「不可监测」。",
    "recheck_none": "当前没有可重新检测的「不可监测」项（系统组件保持锁死）。",
    "enable_fail_title": "无法启用",
    "enable_fail": "这个程序技术上无法监测，所以不能勾选。\n\n原因：%s",
    "access_denied": "系统拒绝访问",
    "privacy_html": """
        <h3>本程序采集什么</h3>
        <table border="1" cellspacing="0" cellpadding="4">
          <tr><th>字段</th><th>用途</th><th>是否落库</th></tr>
          <tr><td>前台窗口句柄 → 进程 ID</td><td>判断哪个程序在前台</td><td>不落库（瞬时值）</td></tr>
          <tr><td>程序可执行文件全路径</td><td>标识是哪个软件</td><td>落库</td></tr>
          <tr><td>版本资源：产品名 / 版本 / 发布者</td><td>界面上显示可读名称</td><td>落库</td></tr>
          <tr><td>进程 CPU 时间、磁盘读写字节数</td><td>判断后台是否真有活动</td><td>不落库（仅差分）</td></tr>
          <tr><td>距上次输入的毫秒数</td><td>空闲时自动降频省电</td><td>不落库</td></tr>
          <tr><td>时间戳（起止）</td><td>算时长</td><td>落库</td></tr>
        </table>

        <h3>绝不采集</h3>
        <ul>
          <li>窗口标题、控件文字</li><li>网址 / 网页内容</li><li>聊天内容</li>
          <li>键盘输入</li><li>鼠标轨迹</li><li>剪贴板</li><li>截屏 / 录屏</li>
          <li>文件内容与文件名（exe 自身路径除外）</li>
          <li>浏览器历史 / 书签 / 密码</li><li>麦克风 / 摄像头</li>
          <li>网络流量内容</li><li>账号 / 机器名 / IP / 位置</li>
          <li>任何遥测或崩溃上报</li>
        </ul>

        <h3>数据存在哪</h3>
        <p><code>%s</code><br>
        只写入这个目录（当前用户的私有目录），<b>不会写程序目录</b>，
        也不会修改任何系统设置或注册表项（开机自启默认关闭，需要你主动勾选）。</p>

        <h3>不联网</h3>
        <p>本程序<b>不包含任何联网代码</b>，不监听任何端口，不上传任何数据。
        你可以自己验证：关掉程序外的一切干扰，运行
        <code>tools\\audit.py</code>，它会扫描源码里的网络调用并检查本进程是否在监听端口。</p>

        <h3>怎么彻底删除</h3>
        <p>点下面的「清空全部数据」删除所有记录；或直接删除整个目录
        <code>%s</code>。</p>
        """,
    "btn_clear": "清空全部数据", "btn_open_dir": "打开数据目录",
    "chk_autostart": "开机自动启动监测（仅写入当前用户的注册表 Run 项，取消勾选即删除）",
    "lbl_manage": "软件管理：", "btn_uninstall": "卸载本软件…",
    "un_title": "卸载",
    "un_frozen_msg": "这是打包版：直接删除程序目录即可完成卸载；\n"
                     "使用数据在 %s，不需要时可一并删除。",
    "un_bat_missing": "未找到卸载向导：%s",
    "un_confirm": "将停止监测程序，并把以下内容送回收站（可恢复）：\n"
                  "  · 开机自启项（若有）\n  · 桌面快捷方式\n  · 全部使用数据（%s）\n\n"
                  "程序文件不会删除（绿色程序，不需要时删目录即可）。\n"
                  "向导窗口会弹出，请在其中确认执行。\n\n确定继续吗？",
    "set_fail_title": "设置失败",
    "set_fail_msg": "写入注册表失败（可能被安全软件拦截），已恢复原状态。",
    "cfg_save_fail": "保存设置失败（配置文件无法写入），已恢复原状态。",
    "clear_title": "确认清空",
    "clear_confirm": "将删除全部使用时长记录，且不可恢复。\n\n确定继续吗？",
    "clear_done_title": "已完成", "clear_done": "数据已清空。",
    "lbl_language": "界面语言", "lang_restart_note": "（重启统计窗口后生效）",
    "chk_overlay": "显示桌面悬浮窗（独立进程，额外占用约 45 MB 内存）",
    "overlay_note": "重启电脑后，打开统计窗口时悬浮窗会自动恢复。",
    "ov_title": "今日活跃", "ov_paused": "监测未运行",
    "ov_fg_bg": "前台 %s · 后台 %s", "ov_menu_quit": "退出悬浮窗",
}

_EN = {
    "app_title": "UsageWatcher",
    "dur_min": "%d min", "dur_hm": "%d h %02d min",
    "daemon_running": "%s v%s collecting in background. Right-click the tray icon.",
    "already_running": "Already running — opening the statistics window…",
    "tip_paused": "Paused",
    "tip_today": "%s\nActive today %s (fg %s · bg %s)",
    "tip_errors": "\n[!] %d collection errors",
    "tray_summary": "Today fg %s · bg %s",
    "tray_open": "Open statistics", "tray_pause": "Pause tracking",
    "tray_resume": "Resume tracking", "tray_quit": "Exit",
    "period_today": "Today", "period_yesterday": "Yesterday", "period_byday": "By date",
    "period_week": "This week", "period_month": "This month", "period_30d": "Last 30 days",
    "tab_stats": "Statistics", "tab_scope": "Scope", "tab_settings": "Settings",
    "btn_refresh": "Refresh", "btn_export": "Export CSV", "lbl_period": "Period",
    "hdr_app": "Application", "hdr_fg": "Foreground", "hdr_bg": "Background",
    "hdr_total": "Active total (dedup)", "hdr_fg_pct": "FG ratio",
    "hdr_exe": "Executable", "hdr_fg_min": "Foreground (min)",
    "hdr_bg_min": "Background (min)", "hdr_total_min": "Active total (min)",
    "hdr_fg_pct_c": "FG ratio %", "hdr_publisher": "Publisher",
    "hdr_monitorable": "Monitorable", "hdr_tracked": "Tracked", "hdr_note": "Note",
    "tip_hdr_total": "Active total = fg ∪ bg deduplicated; multi-process apps overlap, "
                     "so it is less than the sum",
    "nodata": "No data yet",
    "chart_note": "Per-app fg/bg time (blue = foreground, grey = active background). "
                  "Active total = fg ∪ bg deduplicated; multi-process apps overlap, "
                  "so it does not add up",
    "daily_title": "Daily totals",
    "daily_around": "Daily totals within 7 days of %s",
    "daily_follow": "Daily totals — %s",
    "summary_line": "Active total %s, foreground %s (%.0f%%)",
    "hint_not_running": "Daemon is not running (it was auto-started when the window "
                        "opened) — if nothing changes for a long time, double-click start.bat.",
    "hint_paused": "Tracking is paused — right-click the tray icon and choose "
                   "\"Resume tracking\" to collect new data.",
    "hint_accumulating": "Daemon is running and data is accumulating (flushed every "
                         "%d s). This window refreshes every %d s — please wait.",
    "export_none_title": "Nothing to export",
    "export_none": "No records for the selected period (%s ~ %s).",
    "export_dialog_title": "Export CSV", "export_filter": "CSV files (*.csv)",
    "csv_period_row": "Period",
    "export_done_title": "Export complete",
    "export_done": "Exported %d apps:\n%s",
    "lvl_yes": "Monitorable", "lvl_partial": "Limited", "lvl_no": "Not monitorable",
    "mark_yes": "[Monitorable]", "mark_partial": "[Limited]",
    "mark_no": "[Not monitorable] locked",
    "cat_all": "All", "cat_third_party": "Third-party apps",
    "cat_windows": "Windows built-ins & peripherals",
    "cat_system": "System components & services",
    "cat_suggest_exclude": "Suggested exclusions",
    "search_ph": "Search apps…", "btn_select_third": "Select 3rd-party",
    "btn_deselect_all": "Deselect all", "btn_recheck": "Re-detect",
    "scope_note": "Tick = track this app's time. Rows marked \"Not monitorable\" are "
                  "locked — ownership info is technically unavailable (access denied).\n"
                  "\"Re-detect\" puts misjudged rows back into re-check state; the daemon "
                  "re-classifies them on the next sighting (system components stay locked).",
    "status_line": "Showing %d apps · %d checked · %d not monitorable (locked)",
    "bulk_changed": "  (%d changed)",
    "monitor_ok": "Monitorable", "locked_mark": "[L]",
    "recheck_title": "Re-detect",
    "recheck_done": "Reset %d \"not monitorable\" items to re-check state.\n\n"
                    "The daemon re-classifies them on the next sighting; if still "
                    "unreadable they are honestly written back as \"not monitorable\".",
    "recheck_none": "No \"not monitorable\" items can be re-detected right now "
                    "(system components stay locked).",
    "enable_fail_title": "Cannot enable",
    "enable_fail": "This app cannot be monitored technically, so it cannot be ticked."
                   "\n\nReason: %s",
    "access_denied": "Access denied by the system",
    "privacy_html": """
        <h3>What this program collects</h3>
        <table border="1" cellspacing="0" cellpadding="4">
          <tr><th>Field</th><th>Purpose</th><th>Stored?</th></tr>
          <tr><td>Foreground window handle → process ID</td><td>Which app is in front</td><td>No (transient)</td></tr>
          <tr><td>Full path of the executable</td><td>Identify the app</td><td>Yes</td></tr>
          <tr><td>Version resource: product / version / publisher</td><td>Readable names</td><td>Yes</td></tr>
          <tr><td>Process CPU time, disk I/O bytes</td><td>Real background activity</td><td>No (diff only)</td></tr>
          <tr><td>Milliseconds since last input</td><td>Idle down-clocking</td><td>No</td></tr>
          <tr><td>Timestamps (start/end)</td><td>Compute durations</td><td>Yes</td></tr>
        </table>

        <h3>Never collected</h3>
        <ul>
          <li>Window titles, control text</li><li>URLs / page content</li><li>Chat content</li>
          <li>Keyboard input</li><li>Mouse trails</li><li>Clipboard</li><li>Screenshots / recording</li>
          <li>File contents and names (except the exe's own path)</li>
          <li>Browser history / bookmarks / passwords</li><li>Microphone / camera</li>
          <li>Network traffic content</li><li>Account / machine name / IP / location</li>
          <li>Any telemetry or crash reporting</li>
        </ul>

        <h3>Where data lives</h3>
        <p><code>%s</code><br>
        Only this folder (your private per-user folder) is written; the program folder
        is never written, and no system settings or registry entries are modified
        (auto-start is off by default and needs your explicit opt-in).</p>

        <h3>No network</h3>
        <p>This program <b>contains no networking code</b>, listens on no port and
        uploads nothing. Verify it yourself: run <code>tools\\audit.py</code> — it scans
        the source for network calls and checks whether this process is listening.</p>

        <h3>How to delete everything</h3>
        <p>Click \"Clear all data\" below; or simply delete the whole folder
        <code>%s</code>.</p>
        """,
    "btn_clear": "Clear all data", "btn_open_dir": "Open data folder",
    "chk_autostart": "Start tracking at login (writes a Run entry for the current "
                     "user only; uncheck to remove)",
    "lbl_manage": "Manage:", "btn_uninstall": "Uninstall…",
    "un_title": "Uninstall",
    "un_frozen_msg": "This is the packaged build: delete the program folder to "
                     "uninstall;\nyour data lives in %s and can be deleted too.",
    "un_bat_missing": "Uninstall wizard not found: %s",
    "un_confirm": "This will stop the tracker and move the following to the Recycle "
                  "Bin (recoverable):\n  · Auto-start entry (if any)\n  · Desktop "
                  "shortcut\n  · All usage data (%s)\n\nProgram files are not deleted "
                  "(portable app — just delete the folder).\nThe wizard window will "
                  "open; confirm there.\n\nContinue?",
    "set_fail_title": "Setting failed",
    "set_fail_msg": "Writing the registry failed (possibly blocked by security "
                    "software); the original state was restored.",
    "cfg_save_fail": "Saving settings failed (config file not writable); the "
                     "original state was restored.",
    "clear_title": "Confirm clear",
    "clear_confirm": "This deletes ALL usage records and cannot be undone."
                     "\n\nContinue?",
    "clear_done_title": "Done", "clear_done": "All data cleared.",
    "lbl_language": "Interface language", "lang_restart_note": " (effective after "
                   "restarting the statistics window)",
    "chk_overlay": "Show desktop overlay (separate process, ~45 MB extra memory)",
    "overlay_note": "After a reboot, the overlay is restored automatically when you "
                    "open the statistics window.",
    "ov_title": "Active today", "ov_paused": "Not running",
    "ov_fg_bg": "fg %s · bg %s", "ov_menu_quit": "Exit overlay",
}

LANGS = ("zh", "en")
_lang = None


def current() -> str:
    """当前语言（进程内首次调用后定死，重启生效）。"""
    global _lang
    if _lang not in LANGS:
        _lang = str(config.load().get("ui_language") or "zh")
        if _lang not in LANGS:
            _lang = "zh"
    return _lang


def t(key: str, *args) -> str:
    """取文案；args 提供时做 % 格式化。缺 key 回落中文，再缺回显 key（R8：宁可丑不肯哑）。"""
    text = (_EN if current() == "en" else _ZH).get(key) or _ZH.get(key) or key
    return text % args if args else text
