"""界面文案 i18n（v0.3）：中英双语，key 单点、语言随 config.json 重启生效。

规则（tools/test_arch.py + tools/selftest.py 会断言）：
1. 本模块只许 import config，不许碰 data/track/collect/PySide6；
2. 文案单表 _T：key -> (中文, English)，两元素都必须非空（selftest 断言）；
3. 业务逻辑**不许用显示文案做判断**（中文串是展示层，英文切换后必须照常工作）
   —— 周期/分类/等级一律用英文 key（如 period_today），文案只经 t() 取出。
"""

from __future__ import annotations

import config

_T = {
    "app_title": ("软件使用时长监测", "UsageWatcher"),
    "dur_min": ("%d 分", "%d min"), "dur_hm": ("%d 小时 %02d 分", "%d h %02d min"),
    "daemon_running": ("%s v%s 正在后台采集。托盘图标可右键操作。",
                       "%s v%s collecting in background. Right-click the tray icon."),
    "already_running": ("程序已在运行，正在打开统计窗口…",
                        "Already running — opening the statistics window…"),
    "tip_paused": ("已暂停统计", "Paused"),
    "tip_today": ("%s\n今日活跃 %s（前台 %s · 后台 %s）",
                  "%s\nActive today %s (fg %s · bg %s)"),
    "tip_errors": ("\n[!] 采集异常 %d 次", "\n[!] %d collection errors"),
    "tray_summary": ("今日前台 %s · 后台 %s", "Today fg %s · bg %s"),
    "tray_open": ("打开统计窗口", "Open statistics"), "tray_open_data": ("打开数据目录", "Open data folder"),
    "tray_feedback": ("反馈问题…", "Report an issue..."),
    "tray_pause": ("暂停统计", "Pause tracking"),
    "tray_resume": ("继续统计", "Resume tracking"), "tray_quit": ("退出", "Exit"),
    "period_today": ("今日", "Today"), "period_yesterday": ("昨日", "Yesterday"),
    "period_byday": ("按日", "By date"),
    "period_week": ("本周", "This week"), "period_month": ("本月", "This month"),
    "period_30d": ("近 30 天", "Last 30 days"),
    "tab_stats": ("统计", "Statistics"), "tab_scope": ("监测范围", "Scope"),
    "tab_settings": ("设置", "Settings"), "tab_health": ("数据健康", "Data health"),
    "btn_refresh": ("刷新", "Refresh"), "btn_export": ("导出 CSV", "Export CSV"),
    "btn_monthly": ("月度汇总", "Monthly summary"),
    "monthly_title": ("月度汇总导出", "Export monthly summary"),
    "monthly_prompt": ("选择要导出的月份：", "Pick a month to export:"),
    "trend_legend": ("每日 Top5：", "Daily Top5:"),
    "merge_into": ("合并到…", "Merge into…"), "merge_new": ("新建组…", "New group…"),
    "merge_new_title": ("新建归并组", "New merge group"),
    "merge_new_prompt": ("组名（显示在统计列表里）：", "Group name (shown in the list):"),
    "merge_bad_name": ("组名太长或含有换行 / 制表符。", "Name too long or contains newline / tab."),
    "merge_ungroup": ("拆出组「%s」（恢复按原始程序显示）", "Ungroup \"%s\" (show raw apps again)"),
    "health_summary": ("当天 %d 段记录 · %d 个应用 · 采集错误 %d。",
                       "%d segments · %d apps · %d collection errors."),
    "health_cover": ("　记录覆盖 %s – %s（连续窗口 %s）。",
                     " Coverage %s – %s (window %s)."),
    "health_gap_lock": ("%s – %s　锁屏停记（设计行为，锁屏期间不计时长）",
                        "%s – %s  screen lock pause (by design)"),
    "health_gap_idle": ("%s – %s　空闲截断（人不在电脑前，前台挂着的窗口不计时长）",
                        "%s – %s  idle cutoff (away from keyboard)"),
    "health_gap_quiet": ("%s – %s　低活动或未记录（采样在跑，但无达标活动，或该应用未被监测）",
                         "%s – %s  low activity or untracked (sampling ran, no qualifying "
                         "activity, or the app is not monitored)"),
    "health_verdict_ok": ("　数据可信。", " Data looks healthy."),
    "health_verdict_errors": ("　当天有 %d 次采集错误，时长可能偏低。",
                              " %d collection errors — totals may be low."),
    "health_verdict_empty": ("　当天无记录（可能没开机或没使用）。",
                             " No records that day (PC off or unused?)."),
    "health_verdict_off": ("　守护进程未运行 —— 时长没有被记录。",
                           " Daemon is not running — time is not being tracked."),
    "lbl_period": ("统计周期", "Period"),
    "hdr_app": ("应用", "Application"), "hdr_fg": ("前台", "Foreground"),
    "hdr_bg": ("后台活跃", "Background"),
    "hdr_total": ("活跃总时长(去重)", "Active total (dedup)"), "hdr_fg_pct": ("前台占比", "FG ratio"),
    "hdr_exe": ("可执行文件", "Executable"), "hdr_path": ("路径", "Path"), "hdr_fg_min": ("前台(分钟)", "Foreground (min)"),
    "hdr_bg_min": ("后台活跃(分钟)", "Background (min)"),
    "hdr_total_min": ("活跃总时长(分钟)", "Active total (min)"),
    "hdr_fg_pct_c": ("前台占比%", "FG ratio %"), "hdr_publisher": ("发布者", "Publisher"),
    "hdr_monitorable": ("能否监测", "Monitorable"), "hdr_tracked": ("是否监测", "Tracked"),
    "hdr_note": ("说明", "Note"),
    "tip_hdr_total": ("活跃总时长=前台∪后台去重；多进程应用前后台会重叠，故小于相加",
                      "Active total = fg ∪ bg deduplicated; multi-process apps overlap, "
                      "so it is less than the sum"),
    "nodata": ("暂无数据", "No data yet"),
    "chart_note": ("各应用前/后台时长（蓝=前台，灰=后台活跃）。"
                   "活跃总时长=前后台并集去重，多进程应用前后台重叠，不等于相加",
                   "Per-app fg/bg time (blue = foreground, grey = active background). "
                   "Active total = fg ∪ bg deduplicated; multi-process apps overlap, "
                   "so it does not add up"),
    "daily_title": ("每日总时长", "Daily totals"),
    "daily_around": ("%s 前后 7 天每日总时长", "Daily totals within 7 days of %s"),
    "daily_follow": ("%s每日总时长", "Daily totals — %s"),
    "summary_line": ("活跃总时长 %s　其中前台 %s（%.0f%%）", "Active total %s, foreground %s (%.0f%%)"),
    "quality_line": ("\n数据质量：当天 %d 段 · %d 个应用 · 采集错误 %d",
                     "\nData quality: %d segments · %d apps · %d collection errors"),
    "hint_not_running": ("守护进程未运行（打开窗口时已尝试自动拉起）—— "
                         "若长时间没有变化，请双击「启动.bat」。",
                         "Daemon is not running (it was auto-started when the window "
                         "opened) — if nothing changes for a long time, double-click start.bat."),
    "hint_paused": ("监测已暂停 —— 右键托盘图标选择「继续统计」后才会积累新数据。",
                    "Tracking is paused — right-click the tray icon and choose "
                    "\"Resume tracking\" to collect new data."),
    "hint_accumulating": ("守护进程运行中，数据正在积累（每 %d 秒落库一批），"
                          "本窗口每 %d 秒自动刷新，请稍候。",
                          "Daemon is running and data is accumulating (flushed every "
                          "%d s). This window refreshes every %d s — please wait."),
    "export_none_title": ("没有可导出的数据", "Nothing to export"),
    "export_none": ("当前周期（%s ~ %s）还没有记录。", "No records for the selected period (%s ~ %s)."),
    "export_dialog_title": ("导出 CSV", "Export CSV"), "export_filter": ("CSV 表格 (*.csv)", "CSV files (*.csv)"),
    "csv_period_row": ("统计周期", "Period"),
    "export_done_title": ("导出完成", "Export complete"),
    "export_done": ("已导出 %d 个应用：\n%s", "Exported %d apps:\n%s"),
    "lvl_yes": ("可监测", "Monitorable"), "lvl_partial": ("有限监测", "Limited"),
    "lvl_no": ("不可监测", "Not monitorable"),
    "mark_yes": ("[可监测]", "[Monitorable]"), "mark_partial": ("[有限监测]", "[Limited]"),
    "mark_no": ("[不可监测] 锁定", "[Not monitorable] locked"),
    "cat_all": ("全部", "All"), "cat_third_party": ("第三方软件", "Third-party apps"),
    "cat_windows": ("Windows 自带与外设", "Windows built-ins & peripherals"),
    "cat_system": ("系统组件与服务", "System components & services"),
    "cat_suggest_exclude": ("建议排除", "Suggested exclusions"),
    "search_ph": ("搜索应用名…", "Search apps…"), "btn_select_third": ("全选第三方", "Select 3rd-party"),
    "btn_deselect_all": ("全部取消", "Deselect all"), "btn_recheck": ("重新检测", "Re-detect"),
    "scope_note": ("打勾 = 记录这个软件的时长。标为「不可监测」的行已被锁定 —— "
                   "技术上拿不到归属信息（系统拒绝访问），所以无法勾选。\n"
                   "「重新检测」会把误判的项放回待重检状态，守护进程下次探测到时自动重新定性"
                   "（系统组件保持锁死）。",
                   "Tick = track this app's time. Rows marked \"Not monitorable\" are "
                   "locked — ownership info is technically unavailable (access denied).\n"
                   "\"Re-detect\" puts misjudged rows back into re-check state; the daemon "
                   "re-classifies them on the next sighting (system components stay locked)."),
    "status_line": ("显示 %d 个软件 · 已勾选 %d 个 · 不可监测 %d 个（已自动锁定，无法勾选）",
                    "Showing %d apps · %d checked · %d not monitorable (locked)"),
    "bulk_changed": ("　（本次改动 %d 个）", "  (%d changed)"),
    "monitor_ok": ("可以正常监测", "Monitorable"), "locked_mark": ("[锁]", "[L]"),
    "recheck_title": ("重新检测", "Re-detect"),
    "recheck_done": ("已把 %d 个「不可监测」项重置为待重检状态。\n\n"
                     "守护进程下次探测到它们时会自动重新定性；若仍读不到，会如实写回「不可监测」。",
                     "Reset %d \"not monitorable\" items to re-check state.\n\n"
                     "The daemon re-classifies them on the next sighting; if still "
                     "unreadable they are honestly written back as \"not monitorable\"."),
    "recheck_none": ("当前没有可重新检测的「不可监测」项（系统组件保持锁死）。",
                     "No \"not monitorable\" items can be re-detected right now "
                     "(system components stay locked)."),
    "enable_fail_title": ("无法启用", "Cannot enable"),
    "enable_fail": ("这个程序技术上无法监测，所以不能勾选。\n\n原因：%s",
                    "This app cannot be monitored technically, so it cannot be ticked."
                    "\n\nReason: %s"),
    "access_denied": ("系统拒绝访问", "Access denied by the system"),
    "btn_clear": ("清空全部数据", "Clear all data"), "btn_open_dir": ("打开数据目录", "Open data folder"),
    "chk_autostart": ("开机自动启动监测（仅写入当前用户的注册表 Run 项，取消勾选即删除）",
                      "Start tracking at login (writes a Run entry for the current "
                      "user only; uncheck to remove)"),
    "lbl_manage": ("软件管理：", "Manage:"), "btn_uninstall": ("卸载本软件…", "Uninstall…"),
    "un_title": ("卸载", "Uninstall"),
    "un_frozen_msg": ("这是打包版：直接删除程序目录即可完成卸载；\n"
                      "使用数据在 %s，不需要时可一并删除。",
                      "This is the packaged build: delete the program folder to "
                      "uninstall;\nyour data lives in %s and can be deleted too."),
    "un_bat_missing": ("未找到卸载向导：%s", "Uninstall wizard not found: %s"),
    "un_confirm": ("将停止监测程序，并把以下内容送回收站（可恢复）：\n"
                   "  · 开机自启项（若有）\n  · 桌面快捷方式\n  · 全部使用数据（%s）\n\n"
                   "程序文件不会删除（绿色程序，不需要时删目录即可）。\n"
                   "向导窗口会弹出，请在其中确认执行。\n\n确定继续吗？",
                   "This will stop the tracker and move the following to the Recycle "
                   "Bin (recoverable):\n  · Auto-start entry (if any)\n  · Desktop "
                   "shortcut\n  · All usage data (%s)\n\nProgram files are not deleted "
                   "(portable app — just delete the folder).\nThe wizard window will "
                   "open; confirm there.\n\nContinue?"),
    "set_fail_title": ("设置失败", "Setting failed"),
    "set_fail_msg": ("写入注册表失败（可能被安全软件拦截），已恢复原状态。",
                     "Writing the registry failed (possibly blocked by security "
                     "software); the original state was restored."),
    "cfg_save_fail": ("保存设置失败（配置文件无法写入），已恢复原状态。",
                      "Saving settings failed (config file not writable); the "
                      "original state was restored."),
    "clear_title": ("确认清空", "Confirm clear"),
    "clear_confirm": ("将删除全部使用时长记录，且不可恢复。\n\n确定继续吗？",
                      "This deletes ALL usage records and cannot be undone."
                      "\n\nContinue?"),
    "clear_done_title": ("已完成", "Done"), "clear_done": ("数据已清空。", "All data cleared."),
    "lbl_language": ("界面语言", "Interface language"),
    "lang_restart_note": ("（重启统计窗口后生效）",
                          " (effective after restarting the statistics window)"),
    "chk_overlay": ("显示桌面悬浮窗（独立进程，额外占用约 45 MB 内存）",
                    "Show desktop overlay (separate process, ~45 MB extra memory)"),
    "chk_dark": ("深色主题（即时生效，重启后保持）", "Dark theme (applies instantly, persists)"),
    "dark_fail": ("主题设置保存失败，已还原。", "Failed to save theme setting; reverted."),
    "chk_summary": ("每日摘要（每天首次打开统计窗口时展示昨日概览）",
                    "Daily digest (yesterday's overview on first open each day)"),
    "chk_limits": ("应用用时提醒总开关（在统计页右键某个应用设置阈值）",
                   "Usage alerts master switch (right-click an app in Statistics to set)"),
    "sum_banner": ("昨日活跃 <b>%s</b>　Top3：%s",
                   "Yesterday <b>%s</b>　Top 3: %s"),
    "sum_balloon": ("昨日活跃 %s。Top3：%s",
                    "Yesterday %s. Top 3: %s"),
    "limit_menu": ("今日用时提醒…", "Daily usage alert..."),
    "limit_title": ("用时提醒", "Usage alert"),
    "limit_body": ("%s 今日前台已达 %s", "%s has reached %s of foreground time today"),
    "limit_prompt": ("%s：今日前台超过 ___ 小时时提醒（0 = 不提醒）",
                     "%s: alert when foreground today exceeds ___ h (0 = off)"),
    "overlay_note": ("重启电脑后，打开统计窗口时悬浮窗会自动恢复。",
                     "After a reboot, the overlay is restored automatically when you "
                     "open the statistics window."),
    "ov_title": ("今日活跃", "Active today"), "ov_paused": ("监测未运行", "Not running"),
    "ov_fg_bg": ("前台 %s · 后台 %s", "fg %s · bg %s"), "ov_menu_quit": ("退出悬浮窗", "Exit overlay"),
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
    """取文案；args 提供时做 % 格式化。缺 key 回显 key 本身（R8：宁可丑不肯哑）。"""
    row = _T.get(key)
    text = (row[1] if current() == "en" else row[0]) if row else key
    return text % args if args else text
