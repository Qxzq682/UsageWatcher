r"""自测：纯逻辑回归 + 安全红线用例。

每次改完代码都要跑一遍（配合 test_arch.py）。两条腿都绿才算改完 ——
这是「精简但安全不降级」的唯一硬保证。

用法：python.exe tools\selftest.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import apps      # noqa: E402
import audio     # noqa: E402
import collect   # noqa: E402
import config    # noqa: E402
import data      # noqa: E402
import track     # noqa: E402
import winutil   # noqa: E402

PASSED = 0
FAILED: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print("  [√] %s" % name)
    else:
        FAILED.append(name + ("  " + detail if detail else ""))
        print("  [x] %s  %s" % (name, detail))


def ms(hhmm: str) -> int:
    """把 'HH:MM' 转成某个固定基准日的毫秒时间戳。"""
    base = time.mktime((2026, 1, 15, 0, 0, 0, 0, 0, -1))
    hour, minute = (int(x) for x in hhmm.split(":"))
    return int((base + hour * 3600 + minute * 60) * 1000)


# ---------------------------------------------------------------- 前台片段

def test_fg_tracker() -> None:
    print("\n[1] 前台片段：按变化点切段")
    fg = track.FgTracker()
    check("首次 on_fg 不产段", fg.on_fg("a.exe", 1, ms("09:00")) == [])
    out = fg.on_fg("a.exe", 1, ms("09:05"))
    check("同一应用重复调用不产段（避免每轮轮询都切一刀）", out == [])
    out = fg.on_fg("b.exe", 2, ms("09:05"))
    check("切换到 b 时闭合 a", len(out) == 1 and out[0].app_key == "a.exe"
          and out[0].dur_ms == 5 * 60 * 1000, str(out))
    closed = fg.force_close(ms("09:07"), config.FLAG_LOCK)
    check("force_close 生效且带锁屏标记",
          len(closed) == 1 and closed[0].flags == config.FLAG_LOCK, str(closed))
    check("关闭后不再持有当前应用", fg.current() is None)


def test_fg_guess_flag() -> None:
    print("\n[2] 前台片段：轮询兜底要标 FLAG_GUESS")
    fg = track.FgTracker()
    fg.on_fg("a.exe", 1, ms("10:00"))
    out = fg.on_fg("b.exe", 2, ms("10:01"), guessed=True)
    check("靠轮询发现的边界被标记为推测",
          len(out) == 1 and out[0].flags & config.FLAG_GUESS, str(out))


# ---------------------------------------------------------------- 后台片段

def test_bg_tracker() -> None:
    print("\n[3] 后台片段：滞后计数")
    interval = 5000
    bg = track.BgTracker(interval_ms=interval)

    check("第 1 轮活跃不产段（要连续 %d 轮才开段）" % config.ACTIVE_N,
          bg.on_sample("a.exe", 1, 100000, True) == [])
    out = bg.on_sample("a.exe", 1, 105000, True)
    check("达到 ACTIVE_N 后仍不产段（片段要等收尾才闭合）", out == [])

    out = bg.on_sample("a.exe", 1, 110000, False)
    check("不活跃 1 轮不收尾", out == [])
    out = bg.on_sample("a.exe", 1, 115000, False)
    check("不活跃 2 轮不收尾", out == [])
    out = bg.on_sample("a.exe", 1, 120000, False)
    check("连续 IDLE_N 轮后收尾", len(out) == 1, str(out))
    if out:
        seg = out[0]
        check("起点回补到首个活跃采样点", seg.start_ms == 100000, str(seg.start_ms))
        check("右边界 = 最后活跃采样 + 1 个间隔（覆盖采样间隔内的静默）",
              seg.end_ms == 105000 + interval, str(seg.end_ms))


def test_bg_exit_and_lock() -> None:
    print("\n[4] 后台片段：退出收尾 / 锁屏截断")
    bg = track.BgTracker(interval_ms=5000)
    bg.on_sample("a.exe", 1, 100000, True)
    bg.on_sample("a.exe", 1, 105000, True)
    out = bg.close_pid(1, 106000)
    check("进程退出时收尾并标 FLAG_EXIT",
          len(out) == 1 and out[0].flags == config.FLAG_EXIT, str(out))

    bg2 = track.BgTracker(interval_ms=5000)
    bg2.on_sample("b.exe", 2, 200000, True)
    bg2.on_sample("b.exe", 2, 205000, True)
    out = bg2.close_all(206000, config.FLAG_LOCK)
    check("close_all 闭合全部在开片段",
          len(out) == 1 and out[0].flags == config.FLAG_LOCK, str(out))
    check("close_all 后不再残留状态", bg2.on_sample("b.exe", 2, 210000, False) == [])


# ---------------------------------------------------------------- 合并与并集

def test_finalize() -> None:
    print("\n[5] 收尾：合并相邻同类 + 丢弃过短")
    segs = [
        track.Segment("a.exe", 1, "fg", ms("09:00"), ms("09:01")),
        track.Segment("a.exe", 1, "fg", ms("09:01") + 3000, ms("09:02")),
        # 3s 缝隙不合并：中间被别的应用真实占用过（BUG-31），焊平=跨应用重叠
        track.Segment("a.exe", 1, "fg", ms("09:02"), ms("09:03")),   # 首尾相接 → 合并
        track.Segment("a.exe", 1, "fg", ms("11:00"), ms("11:00") + 500),   # 太短 → 丢弃
    ]
    out = track.finalize(segs)
    check("缝隙段独立、相接段合并（4 段 → 2 段）", len(out) == 2, str([s.dur_ms for s in out]))
    check("过短片段被丢弃", all(s.dur_ms >= config.MIN_SEG_MS for s in out))
    mixed = track.finalize([
        track.Segment("a.exe", 1, "fg", ms("09:00"), ms("09:02")),
        track.Segment("a.exe", 1, "bg", ms("09:02"), ms("09:04")),
    ])
    check("前台与后台不跨类合并", len(mixed) == 2)


def test_union() -> None:
    print("\n[6] 区间并集：多进程重叠必须去重")
    spans = [(0, 10000), (5000, 15000), (20000, 25000)]
    check("重叠区间按并集算 = 20000ms（不是求和 25000ms）",
          track.union_ms(spans) == 20000, str(track.union_ms(spans)))
    check("空输入返回 0", track.union_ms([]) == 0)
    check("完全被包含的区间不重复计数",
          track.union_ms([(0, 10000), (2000, 3000)]) == 10000)


def test_split_by_day() -> None:
    print("\n[7] 跨零点按天切分")
    start = ms("23:30")
    seg = track.Segment("a.exe", 1, "fg", start, start + 2 * 3600 * 1000)
    parts = track.split_by_day(seg)
    check("跨零点片段被切成 2 段", len(parts) == 2, str(len(parts)))
    check("两段落在不同日期", parts[0].start_ms != parts[1].start_ms and
          track.day_of(parts[0].start_ms) != track.day_of(parts[1].start_ms))
    check("总时长不变",
          sum(p.dur_ms for p in parts) == seg.dur_ms,
          "%d vs %d" % (sum(p.dur_ms for p in parts), seg.dur_ms))
    same_day = track.Segment("a.exe", 1, "fg", ms("09:00"), ms("10:00"))
    check("不跨零点的片段原样返回", len(track.split_by_day(same_day)) == 1)


# ---------------------------------------------------------------- 不可信输入

def test_sanitize() -> None:
    print("\n[8] 外部数据按不可信输入处理（R3）")
    check("控制字符被剥离", "\x07" not in apps.sanitize("微\x07信"))
    check("换行/制表换成空格并折叠，不会粘连成词",
          apps.sanitize("a\nb\tc") == "a b c", repr(apps.sanitize("a\nb\tc")))
    check("超长被截断到 200 字符", len(apps.sanitize("x" * 5000)) == 200)
    check("None 安全处理", apps.sanitize(None) == "")


def test_classify_and_capability() -> None:
    print("\n[9] 第三方判定与可监测性三态")
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    third = apps.AppInfo(r"d:\apps\foo\bar.exe", r"d:\apps\foo\bar.exe", "bar.exe", "某软件", "1.0", "某公司")
    winapp = apps.AppInfo(
        (system_root + r"\System32\notepad.exe").lower(),
        system_root + r"\System32\notepad.exe", "notepad.exe", "记事本", "1.0", "Microsoft")
    chrome = apps.AppInfo(r"c:\pf\chrome\chrome.exe", r"c:\pf\chrome\chrome.exe",
                          "chrome.exe", "Google Chrome", "1.0", "Google")
    blocked = apps.AppInfo("name:svchost.exe", "", "svchost.exe", "svchost.exe", "", "")

    check("第三方软件被正确归类", apps.classify(third) == "third_party", apps.classify(third))
    check("Windows 自带被正确归类", apps.classify(winapp) == "windows", apps.classify(winapp))
    check("系统服务被正确归类", apps.classify(blocked) == "system", apps.classify(blocked))

    cap_third = apps.capability_of(third, 0)
    check("第三方软件默认勾选", cap_third.level == "yes" and cap_third.enabled)
    cap_win = apps.capability_of(winapp, 0)
    check("Windows 自带默认不勾选", cap_win.level == "yes" and not cap_win.enabled)
    cap_chrome = apps.capability_of(chrome, 0)
    check("浏览器标记为有限监测并给出原因",
          cap_chrome.level == "partial" and "标签页" in cap_chrome.reason, cap_chrome.reason)
    cap_blocked = apps.capability_of(blocked, config.UNMONITORABLE_FAIL_N)
    check("系统服务定性为不可监测且 enabled 为假",
          cap_blocked.level == "no" and not cap_blocked.enabled)
    cap_retry = apps.capability_of(blocked, 1)
    check("失败次数未达阈值前不定性为永久不可监测（防误伤）",
          cap_retry.level == "no" and "重试" in cap_retry.reason, cap_retry.reason)


# ---------------------------------------------------------------- 存储与强制锁定

def _temp_store() -> data.Store:
    folder = tempfile.mkdtemp(prefix="uw_selftest_")
    return data.Store(Path(folder) / "test.db")


def test_store_multi_process_union() -> None:
    print("\n[10] 存储：多进程后台时长必须算并集（端到端）")
    store = _temp_store()
    try:
        info = apps.AppInfo(r"d:\apps\chrome\chrome.exe", r"d:\apps\chrome\chrome.exe",
                            "chrome.exe", "Google Chrome", "", "")
        app_id = store.upsert_app(info)
        # 三个渲染进程的活跃区间互相重叠，求和会虚高
        same_day = track.day_of(ms("09:00"))
        store.write_segments([
            track.Segment(info.app_key, 101, "bg", ms("09:00"), ms("09:10")),
            track.Segment(info.app_key, 102, "bg", ms("09:05"), ms("09:15")),
            track.Segment(info.app_key, 103, "bg", ms("09:02"), ms("09:08")),
        ])
        rows = store.app_totals(same_day, same_day)
        bg = rows[0]["bg_ms"] if rows else -1
        check("三段重叠区间合并为 15 分钟（若为 26 分钟则是错误求和）",
              bg == 15 * 60 * 1000, "实际 %s ms" % bg)
    finally:
        store.close()


def test_store_split_across_midnight() -> None:
    print("\n[11] 存储：跨零点在写库时按天拆开")
    store = _temp_store()
    try:
        info = apps.AppInfo(r"d:\apps\x\x.exe", r"d:\apps\x\x.exe", "x.exe", "X", "", "")
        store.upsert_app(info)
        start = ms("23:50")
        store.write_segments([track.Segment(info.app_key, 1, "fg", start, start + 20 * 60 * 1000)])
        day1, day2 = track.day_of(start), track.day_of(start + 20 * 60 * 1000)
        totals = store.daily_series(day1, day2)
        check("跨零点被记到两天", len(totals) == 2, str(totals))
        check("两天时长相加等于原片段",
              sum(d["fg_ms"] for d in totals) == 20 * 60 * 1000, str(totals))
    finally:
        store.close()


def test_unmonitorable_cannot_be_enabled() -> None:
    print("\n[12] 不可监测项强制锁定（R2 四重加固的第 2、3 层）")
    store = _temp_store()
    try:
        cap = apps.Capability("name:svchost.exe", "no", "系统进程，Windows 拒绝访问", "system", False)
        store.upsert_capability(cap)
        check("写入时不可监测项 enabled 被强制为 0",
              "name:svchost.exe" not in store.enabled_keys())
        changed = store.set_enabled("name:svchost.exe", True)
        check("用户主动勾选也被拒绝（返回 False）", changed is False)
        check("勾选后仍然没有被启用",
              "name:svchost.exe" not in store.enabled_keys())

        # 把 level='no' 行手工改成 enabled=1，数据库 CHECK 必须拦住
        blocked = False
        try:
            store.conn.execute("UPDATE app_capability SET enabled = 1 WHERE app_key = ?",
                               ("name:svchost.exe",))
            store.conn.commit()
        except Exception:
            blocked = True
        if not blocked:
            rows = [r for r in store.capability_rows() if r["app_key"] == "name:svchost.exe"]
            blocked = bool(rows) and not rows[0]["enabled"]
        check("绕过 API 直接改库也被数据库 CHECK 拦住", blocked)

        normal = apps.Capability("d:\\apps\\foo\\bar.exe", "yes", "", "third_party", True)
        store.upsert_capability(normal)
        check("正常软件可以正常勾选", store.set_enabled(normal.app_key, True) is True)
    finally:
        store.close()


def test_error_visibility() -> None:
    print("\n[13] 错误必须可见，不许静默吞掉（R8）")
    store = _temp_store()
    try:
        check("Store 暴露 error_count", hasattr(store, "error_count"))
        written = store.write_segments([track.Segment("不存在的应用", 1, "fg", 0, 1000)])
        check("写入未知应用时明确返回 0 而不是假装成功", written == 0)
        sampler = collect.BgSampler(apps.Resolver())
        check("BgSampler 暴露 error_count", hasattr(sampler, "error_count"))
    finally:
        store.close()


# ---------------------------------------------------------------- 静态安全断言

def test_store_cross_thread() -> None:
    print("\n[14] 存储跨线程可用（回归：连接默认绑定创建线程，实测踩过）")
    import threading
    store = _temp_store()
    errors: list = []
    try:
        info = apps.AppInfo(r"d:\apps\t\t.exe", r"d:\apps\t\t.exe", "t.exe", "T", "", "")
        store.upsert_app(info)

        def writer() -> None:
            try:
                for index in range(20):
                    store.write_segments([
                        track.Segment(info.app_key, 1, "fg", ms("09:00") + index * 60000,
                                      ms("09:00") + index * 60000 + 30000)])
            except BaseException as exc:      # 线程里的异常不会自动冒泡，必须自己收
                errors.append(repr(exc))

        def reader() -> None:
            try:
                for _ in range(20):
                    store.totals_summary("2026-01-15", "2026-01-15")
            except BaseException as exc:
                errors.append(repr(exc))

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        check("主线程建库、工作线程读写都不报错", not errors, "; ".join(errors[:3]))
        rows = store.app_totals("2026-01-15", "2026-01-15")
        check("跨线程写入的数据读得到", bool(rows), str(rows))
    finally:
        store.close()


def test_static_red_lines() -> None:
    print("\n[15] 静态红线：零网络 / 不采集内容（R4 + 隐私）")
    sys.path.insert(0, str(ROOT / "tools"))
    import _redlines

    hits = _redlines.scan_source(ROOT / "src")
    detail = "; ".join("%s:%d %s" % (n, line_no, token) for n, line_no, token, _ in hits[:5])
    check("src 中不含任何网络/采集内容类标识符", not hits, detail)

    src_text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src").glob("*.py"))
    check("未导入 PySide6 的模块占多数（只有 ui_*.py 可以用）", src_text.count("import PySide6") <= 2)


def test_csv_export() -> None:
    print("\n[16] CSV 导出：公式注入转义 + 行内容")
    import i18n                                # noqa: E402
    from ui_main import _csv_safe, csv_rows      # noqa: E402  PySide6 已装，纯函数不需要 QApplication

    check("= 开头的单元格被转义", _csv_safe("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0")
    check("+ - @ 开头的单元格被转义",
          _csv_safe("+1") == "'+1" and _csv_safe("-2") == "'-2" and _csv_safe("@x") == "'@x")
    check("普通中文名不受影响", _csv_safe("微信") == "微信")
    rows = csv_rows([{"display_name": "=危险", "exe_name": "app.exe",
                      "fg_ms": 90_000, "bg_ms": 30_000, "total_ms": 120_000, "fg_ratio": 0.75}])
    # 表头文案与 i18n 同源（不写死中文：切英文后本测试必须照常通过）
    check("表头 6 列", len(rows[0]) == 6 and rows[0][0] == i18n.t("hdr_app"))
    check("数据行转义与数值换算正确",
          rows[1][0] == "'=危险" and rows[1][2] == 1.5 and rows[1][4] == 2.0
          and rows[1][5] == 75.0, str(rows[1]))


def test_clear_all_keeps_daemon_writing() -> None:
    print("\n[17] 清空数据后守护进程（另一 Store 实例）必须仍能写库（BUG-1 回归）")
    folder = Path(tempfile.mkdtemp(prefix="uw_selftest_"))
    daemon = data.Store(folder / "t.db")
    try:
        info = apps.AppInfo(r"c:\a\b.exe", r"c:\a\b.exe", "b.exe", "B", "", "")
        daemon.upsert_app(info)
        daemon.write_segments(
            [track.Segment(info.app_key, 1, "fg", ms("10:00"), ms("10:05"))])
        gui = data.Store(folder / "t.db")     # 模拟统计窗口：独立进程/独立实例
        try:
            gui.clear_all()
        finally:
            gui.close()
        app_rows = daemon.conn.execute("SELECT COUNT(*) c FROM app").fetchone()["c"]
        check("清空后 app 元数据表保留（缓存不悬空）", app_rows == 1, "app rows=%d" % app_rows)
        n = daemon.write_segments(
            [track.Segment(info.app_key, 1, "fg", ms("10:06"), ms("10:10"))])
        check("清空后守护进程写段成功", n == 1, "写入 %d 行" % n)
        check("无静默失败（error_count 不增长）", daemon.error_count == 0,
              "error_count=%d" % daemon.error_count)

        # 第二道防线：内存缓存的 app_id 与库不一致（悬空/过期）时重试必须自愈
        daemon._app_ids[info.app_key] = 99999     # 注入悬空 app_id
        n2 = daemon.write_segments(
            [track.Segment(info.app_key, 1, "fg", ms("10:11"), ms("10:15"))])
        check("缓存 app_id 悬空时重试自愈", n2 == 1, "写入 %d 行" % n2)
        check("重试后缓存恢复为真实 app_id", daemon._app_ids.get(info.app_key) == 1,
              str(daemon._app_ids))
    finally:
        daemon.close()


def test_app_totals_fg_bg_union() -> None:
    print("\n[18] 存储：活跃总时长 = 前台∪后台 并集（BUG-2 回归）")
    store = _temp_store()
    try:
        info = apps.AppInfo(r"c:\a\c.exe", r"c:\a\c.exe", "c.exe", "C", "", "")
        store.upsert_app(info)
        day = track.day_of(ms("09:00"))
        # 前台 09:00-09:10，后台 09:05-09:15 → 重叠 5 分钟，活跃总时长 = 15 分钟
        store.write_segments([
            track.Segment(info.app_key, 1, "fg", ms("09:00"), ms("09:10")),
            track.Segment(info.app_key, 2, "bg", ms("09:05"), ms("09:15")),
        ])
        rows = store.app_totals(day, day)
        total = rows[0]["total_ms"] if rows else -1
        check("重叠时 total 必须为并集 15 分钟（相加则是 20 分钟）",
              total == 15 * 60 * 1000, "实际 %s ms" % total)
        summary = store.totals_summary(day, day)
        check("概览合计同口径", summary["total_ms"] == 15 * 60 * 1000,
              "实际 %s ms" % summary["total_ms"])
        series = store.daily_series(day, day)
        check("每日序列含 active_ms 且同口径",
              series and series[0].get("active_ms") == 15 * 60 * 1000, str(series))
    finally:
        store.close()


def test_upsert_refreshes_last_seen() -> None:
    print("\n[19] upsert_app 缓存命中也必须按期刷新 last_seen_ms（BUG-3 回归）")
    store = _temp_store()
    try:
        info = apps.AppInfo(r"c:\a\d.exe", r"c:\a\d.exe", "d.exe", "D", "", "")
        store.upsert_app(info)
        old = int((time.time() - 10 * 3600) * 1000)   # 拨回 10 小时前
        store.conn.execute(
            "UPDATE app SET last_seen_ms = ? WHERE app_key = ?", (old, info.app_key))
        store.conn.commit()
        store._app_seen_flush.clear()                 # 强制刷新间隔到期
        store.upsert_app(info)                        # 走缓存命中路径
        row = store.conn.execute(
            "SELECT last_seen_ms FROM app WHERE app_key = ?", (info.app_key,)).fetchone()
        check("到期后 last_seen_ms 被刷新", int(row["last_seen_ms"]) > old,
              str(row["last_seen_ms"]))
        before = int(row["last_seen_ms"])
        store.upsert_app(info)                        # 立即再调用（未到期）
        row2 = store.conn.execute(
            "SELECT last_seen_ms FROM app WHERE app_key = ?", (info.app_key,)).fetchone()
        check("未到期时不重复写库", int(row2["last_seen_ms"]) == before, str(before))
    finally:
        store.close()


def test_reset_recheckable() -> None:
    print("\n[20] 重新检测真重置：误判 no 降回待重检，system 保持锁死（BUG-6 回归）")
    store = _temp_store()
    try:
        store.upsert_capability(apps.Capability("c:\\a\\x.exe", "no", "读不到", "third_party", False))
        store.upsert_capability(apps.Capability("name:svc", "no", "系统服务", "system", False))
        store.upsert_capability(apps.Capability("c:\\a\\y.exe", "partial", "浏览器", "third_party", True))
        reset = store.reset_recheckable()
        check("只重置非系统的 no 行", reset == 1, str(reset))
        row = store.conn.execute(
            "SELECT level, reason, enabled FROM app_capability WHERE app_key = ?",
            ("c:\\a\\x.exe",)).fetchone()
        check("误判项降回 partial", row["level"] == "partial", row["level"])
        check("恢复默认勾选（第三方=1）", int(row["enabled"]) == 1, str(row["enabled"]))
        check("原因改为待重新检测", row["reason"] == data.RECHECK_PENDING, row["reason"])
        row2 = store.conn.execute(
            "SELECT level FROM app_capability WHERE app_key = ?", ("name:svc",)).fetchone()
        check("system 保持锁死", row2["level"] == "no", row2["level"])
        row3 = store.conn.execute(
            "SELECT level, reason FROM app_capability WHERE app_key = ?",
            ("c:\\a\\y.exe",)).fetchone()
        check("原有 partial 行不受影响", row3["reason"] == "浏览器", row3["reason"])
        pending = store.recheck_pending_keys()
        check("待重检集合只含重置项", pending == {"c:\\a\\x.exe"}, str(pending))
    finally:
        store.close()


def test_fg_monotonic_no_overlap() -> None:
    print("\n[21] 前台时间戳乱序不得产出重叠片段（BUG-26 回归）")
    fg = track.FgTracker()
    base = 1_000_000
    out: list = []
    out += fg.on_fg("a", 1, base)               # 正常开段
    out += fg.on_fg("b", 2, base + 5000)        # 正常切换
    out += fg.on_fg("c", 3, base + 13_533)      # 正常切换
    out += fg.on_fg("b", 2, base + 2000)        # 迟到事件：时间戳倒退 11.5 秒
    out += fg.on_fg("d", 4, base + 20_000)      # 正常切换
    out += fg.force_close(base + 30_000, 0)
    # b 的两条段会被 finalize 合并成 b[5000,20000]，c 的零长段被 MIN_SEG_MS 丢弃
    segs = track.finalize(out)
    ordered = sorted(segs, key=lambda s: s.start_ms)
    overlap = sum(
        1 for i in range(1, len(ordered)) for j in range(i)
        if ordered[i].start_ms < ordered[j].end_ms)
    check("乱序下仍产出有效片段", len(segs) == 3, str(len(segs)))
    check("任意两段时间轴不重叠", overlap == 0, str(overlap))
    check("无反向边界（end >= start）",
          all(s.end_ms >= s.start_ms for s in segs), "ok")


def test_maintenance_cleans_junk() -> None:
    print("\n[22] maintenance 清理 name:pid 垃圾条目（BUG-28 回归）")
    store = _temp_store()
    try:
        store.upsert_app(apps.AppInfo("name:pid-999", "", "pid-999", "pid-999", "", ""))
        store.upsert_capability(apps.Capability("name:pid-999", "no", "读不到", "third_party", False))
        store.upsert_app(apps.AppInfo(r"c:\a\ok.exe", r"c:\a\ok.exe", "ok.exe", "OK", "", ""))
        store.maintenance()
        n = store.conn.execute(
            "SELECT COUNT(*) c FROM app WHERE app_key LIKE 'name:pid-%'").fetchone()["c"]
        check("垃圾 app 行被清理", n == 0, str(n))
        n = store.conn.execute(
            "SELECT COUNT(*) c FROM app_capability WHERE app_key LIKE 'name:pid-%'").fetchone()["c"]
        check("垃圾 capability 行被清理", n == 0, str(n))
        n = store.conn.execute(
            "SELECT COUNT(*) c FROM app WHERE app_key = 'c:\\a\\ok.exe'").fetchone()["c"]
        check("正常行不受影响", n == 1, str(n))
    finally:
        store.close()


def test_finalize_fg_no_gap_fill() -> None:
    print("\n[23] 前台合并不填平跨应用缝隙 + 落库兜底截短（BUG-31 回归）")
    base = 1_000_000
    seam = [
        track.Segment("edge", 1, "fg", base, base + 49_000),
        track.Segment("wb", 2, "fg", base + 49_000, base + 54_000),
        track.Segment("edge", 1, "fg", base + 54_000, base + 93_000),
    ]
    segs = track.finalize(seam)
    overlap = any(a.app_key != b.app_key and a.start_ms < b.end_ms and b.start_ms < a.end_ms
                  for a in segs for b in segs if a is not b)
    check("短段插入不再被焊进前一条（无跨应用重叠）", not overlap, str(segs))
    edge = [s for s in segs if s.app_key == "edge"]
    check("被插入的应用两半保持独立（49s 处不填平）",
          len(edge) == 2 and edge[0].end_ms == base + 49_000, str(edge))
    _, clamped = track.clamp_fg_overlaps(track.finalize(seam))
    check("治本后兜底不误触发（clamp=0）", clamped == 0, str(clamped))
    joint = track.finalize([
        track.Segment("edge", 1, "fg", base, base + 10_000),
        track.Segment("edge", 1, "fg", base + 10_000, base + 20_000),
    ])
    check("同应用首尾相接仍正常合并",
          len(joint) == 1 and joint[0].end_ms - joint[0].start_ms == 20_000, str(joint))
    bg = track.finalize([
        track.Segment("wx", 1, "bg", base, base + 10_000),
        track.Segment("wx", 1, "bg", base + 15_000, base + 20_000),
    ])
    check("后台合并维持 MERGE_GAP_MS 容忍",
          len(bg) == 1 and bg[0].end_ms - bg[0].start_ms == 20_000, str(bg))
    bad = [
        track.Segment("a", 1, "fg", base, base + 60_000),
        track.Segment("b", 2, "fg", base + 30_000, base + 40_000),
    ]
    out, clamped = track.clamp_fg_overlaps(bad)
    check("兜底把重叠截短到真实切换点",
          clamped == 1 and out[0].end_ms == base + 30_000
          and out[0].start_ms == base, str(out))
    check("截短后无重叠", all(not (x.start_ms < y.end_ms and y.start_ms < x.end_ms)
                              for x in out for y in out if x is not y and x.kind == "fg"
                              and y.kind == "fg"), "ok")


def test_idle_gate() -> None:
    print("\n[24] 空闲截断闸门：AFK 不再记成前台使用（BUG-32 回归）")
    w = collect.FgWatcher(lambda _s: None)
    trk = track.FgTracker()
    saved = (winutil.idle_milliseconds, winutil.foreground_hwnd,
             winutil.is_minimized, winutil.pid_of_window)
    try:
        winutil.idle_milliseconds = lambda: 60_000            # 1 分钟：未超限
        trk.on_fg("qq.exe", 111, ms("10:00"))
        out = w.idle_gate(ms("10:01"), trk.force_close)
        check("未超限时空闲闸门不动作",
              out == [] and w.idle_cut is False and trk.current() == "qq.exe", str(out))

        winutil.idle_milliseconds = lambda: 400_000           # 6.7 分钟无输入
        out = w.idle_gate(ms("10:05"), trk.force_close)
        check("超限空闲闭合在开前台段并标 FLAG_IDLE",
              len(out) == 1 and out[0].flags == config.FLAG_IDLE
              and out[0].end_ms == ms("10:05"), str(out))
        check("闸门进入闩上状态且已 forget（恢复后要重报前台）",
              w.idle_cut is True and w._last_pid == -1, str(w.idle_cut))
        check("闩上时轮询不再上报（否则闭合→重开每 0.2s 造碎片）", w.poll() is None)
        w.pending.put(collect.Sample(ts_ms=ms("10:06"), pid=222, from_event=True))
        check("闩上时事件只收不判（toast 不算使用）",
              w.samples() == [] and w.pending.empty())
        out2 = w.idle_gate(ms("10:07"), trk.force_close)
        check("闩上期间重复过闸不产段", out2 == [] and trk.current() is None, str(out2))

        winutil.idle_milliseconds = lambda: 1_000             # 输入恢复
        out3 = w.idle_gate(ms("10:08"), trk.force_close)
        check("输入恢复解闩且不产段", out3 == [] and w.idle_cut is False, str(out3))
        winutil.foreground_hwnd = lambda: 4242
        winutil.is_minimized = lambda _h: False
        winutil.pid_of_window = lambda _h: 333
        polled = w.poll()
        check("解闩后轮询重报当前前台",
              polled is not None and polled.pid == 333 and not polled.from_event,
              str(polled))
        segs = trk.on_fg("wx.exe", 333, polled.ts_ms, guessed=not polled.from_event)
        check("恢复后的前台按新片段起算", segs == [] and trk.current() == "wx.exe")
    finally:
        (winutil.idle_milliseconds, winutil.foreground_hwnd,
         winutil.is_minimized, winutil.pid_of_window) = saved


def test_audio_gate() -> None:
    print("\n[25] 音频判活：峰值阈值 + 真实会话冒烟（v0.2 A1）")
    check("峰值阈值判定（实测播音 0.27 / 静默 0.000，阈值 0.01）",
          audio.is_audible(0.05) and not audio.is_audible(0.005))
    pids = audio.active_pids()
    check("真实 COM 枚举返回 set 且不抛异常", isinstance(pids, set), str(type(pids)))
    recent = audio.recently_audible_pids()
    check("记忆窗查询返回 set（AFK 豁免用）", isinstance(recent, set), str(type(recent)))
    check("枚举无错误累积（R8）", audio.error_count == 0, "error_count=%d" % audio.error_count)
    check("枚举结果与记忆窗自洽（出声者必在窗内）", pids <= recent or not (pids - recent),
          "pids=%s recent=%s" % (pids, recent))


def test_idle_gate_audio_exempt() -> None:
    print("\n[26] AFK 豁免：前台应用出声（视频）不截断，后台音乐不豁免（v0.2 A1）")
    w = collect.FgWatcher(lambda _s: None)
    trk = track.FgTracker()
    trk.on_fg("video.exe", 111, ms("11:00"))
    w._last_pid = 111
    saved = (winutil.idle_milliseconds, audio.recently_audible_pids)
    try:
        winutil.idle_milliseconds = lambda: 600_000           # 10 分钟无输入
        audio.recently_audible_pids = lambda: {111}           # 前台应用正在出声
        out = w.idle_gate(ms("11:06"), trk.force_close)
        check("前台出声时 AFK 豁免（不截断、不闩上）",
              out == [] and w.idle_cut is False and trk.current() == "video.exe", str(out))

        audio.recently_audible_pids = lambda: set()           # 声音停了，人还没回来
        out = w.idle_gate(ms("11:08"), trk.force_close)
        check("声音停止后闸门照常截断并标 FLAG_IDLE",
              len(out) == 1 and out[0].flags == config.FLAG_IDLE, str(out))

        winutil.idle_milliseconds = lambda: 1_000             # （人回来过，解闩）
        w.idle_gate(ms("12:01"), trk.force_close)
        winutil.idle_milliseconds = lambda: 400_000           # 又离开
        trk.on_fg("ide.exe", 333, ms("12:02"))                # 新前台：挂着 IDE 离开
        w._last_pid = 333
        audio.recently_audible_pids = lambda: {222}           # 别的应用在放音乐
        out = w.idle_gate(ms("12:08"), trk.force_close)
        check("后台音乐不豁免前台（BUG-32 不复发）",
              len(out) == 1 and out[0].flags == config.FLAG_IDLE, str(out))
    finally:
        winutil.idle_milliseconds, audio.recently_audible_pids = saved


def test_period_ranges() -> None:
    print("\n[27] 统计周期：昨日/按日（v0.2 A2；v0.3 起业务键为英文，显示走 i18n）")
    from datetime import date as _d, timedelta as _td
    from ui_main import period_range
    y = _d.today() - _td(days=1)
    check("昨日周期 = 单日", period_range("yesterday") == (y.isoformat(),) * 2,
          str(period_range("yesterday")))
    check("按日指定日期 = 单日",
          period_range("byday", day=_d(2026, 9, 15)) == ("2026-09-15",) * 2)
    check("按日接受 ISO 字符串",
          period_range("byday", day="2026-09-15") == ("2026-09-15",) * 2)
    check("按日缺省 = 昨天", period_range("byday") == (y.isoformat(),) * 2)
    check("今日不受影响", period_range("today") == (_d.today().isoformat(),) * 2)


def test_autostart() -> None:
    print("\n[28] 开机自启：R6 红线（HKCU Run、默认关、可还原）（v0.2 B2）")
    cmd = winutil.autostart_command()
    check("自启命令生成指向本程序（源码=pythonw+app.py / 打包=exe）",
          "app.py" in cmd or "UsageWatcher" in cmd, cmd)
    before = winutil.autostart_enabled()
    if before:
        check("用户已勾选自启，跳过写删测试（不碰用户设置）", True)
        return
    check("默认不写自启（R6）", before is False, str(before))
    ok = winutil.set_autostart(True)
    check("勾选写入 HKCU Run 成功且可查", ok and winutil.autostart_enabled(),
          "ok=%s enabled=%s" % (ok, winutil.autostart_enabled()))
    ok2 = winutil.set_autostart(False)
    check("取消勾选删除成功（还原初始态）", ok2 and not winutil.autostart_enabled(),
          "ok=%s enabled=%s" % (ok2, winutil.autostart_enabled()))


def test_segment_retention() -> None:
    print("\n[29] segment 保留策略：超龄片段在 maintenance 清理（v0.2 E1）")
    store = _temp_store()
    try:
        app_id = store.upsert_app(apps.AppInfo(
            r"c:\a\old.exe", r"c:\a\old.exe", "old.exe", "OLD", "", ""))
        old_ms = int((time.time() - (config.SEGMENT_KEEP_DAYS + 5) * 86400) * 1000)
        fresh_ms = int(time.time() * 1000)
        rows = [(app_id, 1, kind, t0, t0 + 60_000, 60_000, track.day_of(t0), 0)
                for kind, t0 in (("fg", old_ms), ("fg", fresh_ms))]
        store.conn.executemany(
            "INSERT INTO segment (app_id, pid, kind, start_ms, end_ms, dur_ms, day, flags)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        store.conn.commit()
        store.maintenance()
        n = store.conn.execute("SELECT COUNT(*) c FROM segment").fetchone()["c"]
        check("超龄片段被清理、新鲜片段保留", n == 1, "剩 %d 行" % n)
    finally:
        store.close()


def test_i18n_tables() -> None:
    print("\n[30] i18n：单表结构完整 + 文案回落（v0.3）")
    import i18n
    from ui_main import PERIOD_KEYS
    from ui_scope import CATEGORY_KEYS
    bad = [k for k, v in i18n._T.items()
           if not (isinstance(v, tuple) and len(v) == 2 and v[0] and v[1])]
    check("每条文案均为 (中文, English) 二元组且两元素非空", not bad,
          "异常条目 %s" % bad[:5])
    check("t() 对未知 key 回落为 key 本身（R8：宁可丑不肯哑）",
          i18n.t("__no_such_key__") == "__no_such_key__")
    check("周期/分类文案全量存在（切语言后界面不缺字）",
          all(i18n.t("period_" + k) != k for k in PERIOD_KEYS)
          and all(i18n.t("cat_" + c) != c for c in CATEGORY_KEYS))
    check("语言值合法（zh/en）", i18n.LANGS == ("zh", "en") and i18n.current() in i18n.LANGS)


def test_overlay_prefs() -> None:
    print("\n[31] 悬浮窗：偏好键 / 退出标志 / 纯函数（v0.3）")
    import i18n
    import ui_overlay
    check("config DEFAULTS 含 ui_language 与 overlay_enabled（默认关，R6 精神）",
          config.DEFAULTS.get("ui_language") == "zh"
          and config.DEFAULTS.get("overlay_enabled") is False)
    check("overlay.flag 落在用户数据目录（与 stop.flag 同一套零 IPC 思路）",
          config.overlay_flag_path().parent == config.data_dir()
          and config.overlay_flag_path().name == "overlay.flag")
    today = track.day_of(int(time.time() * 1000))
    check("_today_iso 返回今天（ISO 串）",
          ui_overlay._today_iso() == (today, today), str(ui_overlay._today_iso()))
    check("_fmt 分钟/小时格式与 i18n 一致",
          ui_overlay._fmt(0) == i18n.t("dur_min", 0)
          and ui_overlay._fmt(90 * 60_000) == i18n.t("dur_hm", 1, 30),
          "%s / %s" % (ui_overlay._fmt(0), ui_overlay._fmt(90 * 60_000)))
    check("悬浮窗互斥体名含 Local 前缀（单会话不跨用户）",
          ui_overlay._MUTEX.startswith("Local\\"))


def main() -> int:
    print("=" * 68)
    print(" 自测：纯逻辑回归 + 安全红线")
    print("=" * 68)
    test_fg_tracker()
    test_fg_guess_flag()
    test_bg_tracker()
    test_bg_exit_and_lock()
    test_finalize()
    test_union()
    test_split_by_day()
    test_sanitize()
    test_classify_and_capability()
    test_store_multi_process_union()
    test_store_split_across_midnight()
    test_unmonitorable_cannot_be_enabled()
    test_error_visibility()
    test_store_cross_thread()
    test_static_red_lines()
    test_csv_export()
    test_clear_all_keeps_daemon_writing()
    test_app_totals_fg_bg_union()
    test_upsert_refreshes_last_seen()
    test_reset_recheckable()
    test_fg_monotonic_no_overlap()
    test_maintenance_cleans_junk()
    test_finalize_fg_no_gap_fill()
    test_idle_gate()
    test_audio_gate()
    test_idle_gate_audio_exempt()
    test_period_ranges()
    test_autostart()
    test_segment_retention()
    test_i18n_tables()
    test_overlay_prefs()

    print("\n" + "=" * 68)
    print("通过 %d 项，失败 %d 项" % (PASSED, len(FAILED)))
    for item in FAILED:
        print("  [x] " + item)
    print("=" * 68)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
