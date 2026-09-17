"""存储层：SQLite（WAL）。SQL 只许出现在本文件与 schema.py（test_arch 断言），
UI 层只能调这里暴露的查询函数。安全约束：全部参数化查询（R1）。"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional

import config
import schema
import track

RECHECK_PENDING = "待重新检测，等待下次采样"


class _LockedConnection:
    """给 sqlite3 连接套锁实现跨线程安全（实测：主线程建、工作线程用会
    ProgrammingError 且采集链静默失效）。check_same_thread=False + 这把锁，
    所有现有调用自动线程安全。"""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def __getattr__(self, name):
        """execute / executemany / commit / close 等全部转发进锁内执行——
        薄适配器：Store 侧调用方式不变，锁语义一致。"""
        def wrapped(*args, **kwargs):
            with self._lock:
                return getattr(self._conn, name)(*args, **kwargs)
        return wrapped


class Store:
    """SQLite 封装。可跨线程使用（内部有锁）。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else config.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        raw = sqlite3.connect(str(self.path), timeout=5.0, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        self.conn = _LockedConnection(raw, self._lock)
        self._apply_pragmas()
        self.conn.executescript(schema.SCHEMA_SQL)
        self.conn.commit()
        self._app_ids: dict = {}
        self._app_seen_flush: dict = {}   # app_key -> 上次回写 last_seen_ms 的时刻
        self.error_count = 0     # R8：写库失败必须可见

    def _apply_pragmas(self) -> None:
        for pragma in ("PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL",
                       "PRAGMA foreign_keys=ON", "PRAGMA busy_timeout=5000"):
            try:
                self.conn.execute(pragma)
            except sqlite3.Error:
                self.error_count += 1

    def close(self) -> None:
        try:
            self.conn.commit()
            self.conn.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------ 应用元信息

    def upsert_app(self, info) -> int:
        """写入或更新应用元信息，返回 app_id。缓存命中也按 APP_SEEN_FLUSH_S
        间隔回写 last_seen_ms（后台候选池依据，不刷会让无窗口应用静默消失）。"""
        cached = self._app_ids.get(info.app_key)
        now = int(time.time() * 1000)
        if cached is not None:
            last = self._app_seen_flush.get(info.app_key, 0)
            if now - last >= config.APP_SEEN_FLUSH_S * 1000:
                try:
                    self.conn.execute(
                        "UPDATE app SET last_seen_ms = ? WHERE app_key = ?", (now, info.app_key))
                    self.conn.commit()
                    self._app_seen_flush[info.app_key] = now
                except sqlite3.Error:
                    self.error_count += 1
            return cached
        self.conn.execute(
            "INSERT INTO app (app_key, exe_path, exe_name, display_name, version, publisher,"
            " first_seen_ms, last_seen_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(app_key)"
            " DO UPDATE SET exe_path=excluded.exe_path, exe_name=excluded.exe_name,"
            " display_name=excluded.display_name, version=excluded.version,"
            " publisher=excluded.publisher, last_seen_ms=excluded.last_seen_ms",
            (info.app_key, info.exe_path, info.exe_name, info.display_name,
             info.version, info.publisher, now, now),
        )
        row = self.conn.execute(
            "SELECT app_id FROM app WHERE app_key = ?", (info.app_key,)).fetchone()
        app_id = int(row["app_id"]) if row else 0
        self._app_ids[info.app_key] = app_id
        self._app_seen_flush[info.app_key] = now
        return app_id

    def app_key_to_id(self) -> dict:
        if not self._app_ids:
            rows = self.conn.execute("SELECT app_id, app_key FROM app").fetchall()
            self._app_ids = {r["app_key"]: int(r["app_id"]) for r in rows}
            self._app_seen_flush.clear()   # 缓存重建 = 时间戳作废，下次命中按需重写
        return self._app_ids

    def known_exe_paths(self, hours: int = 24) -> set:
        """近 N 小时出现过的应用 exe 路径 —— 用于后台采样候选集合。"""
        cutoff = int((time.time() - hours * 3600) * 1000)
        rows = self.conn.execute(
            "SELECT exe_path FROM app WHERE last_seen_ms >= ? AND exe_path <> ''",
            (cutoff,)).fetchall()
        return {str(r["exe_path"]).lower() for r in rows}

    # ------------------------------------------------------------ 可监测性

    def upsert_capability(self, cap) -> None:
        """写入可监测性。level='no' 的行**恒为 enabled=0**（数据库层 CHECK 兜底），
        所以手工改配置文件也绕不过去（不可监测四重加固的第 2 层）。
        """
        enabled = 0 if cap.level == "no" else int(bool(cap.enabled))
        self.conn.execute(
            "INSERT INTO app_capability (app_key, level, reason, category, enabled, decided_ms)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(app_key) DO UPDATE SET level=excluded.level,"
            " reason=excluded.reason, category=excluded.category, decided_ms=excluded.decided_ms,"
            " enabled=CASE WHEN excluded.level='no' THEN 0 ELSE app_capability.enabled END",
            (cap.app_key, cap.level, cap.reason, cap.category, enabled, int(time.time() * 1000)),
        )

    def set_enabled(self, app_key: str, enabled: bool) -> bool:
        """用户勾选。返回是否真的改了（不可监测项会被拒绝）。"""
        cursor = self.conn.execute(
            "UPDATE app_capability SET enabled = ? WHERE app_key = ? AND level <> 'no'",
            (int(bool(enabled)), app_key))
        return cursor.rowcount > 0

    def enabled_keys(self) -> set:
        rows = self.conn.execute(
            "SELECT app_key FROM app_capability WHERE enabled = 1 AND level <> 'no'").fetchall()
        return {str(r["app_key"]) for r in rows}

    def capability_rows(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            "SELECT c.app_key, c.level, c.reason, c.category, c.enabled, a.display_name, a.exe_name,"
            " a.exe_path, a.publisher FROM app_capability c LEFT JOIN app a ON a.app_key = c.app_key"
            " ORDER BY CASE c.level WHEN 'no' THEN 2 WHEN 'partial' THEN 1 ELSE 0 END, a.display_name"
        ).fetchall()

    def reset_recheckable(self) -> int:
        """「重新检测」真实现（BUG-6）：误判 no 降回待重检并恢复默认勾选。
        只动非系统项；重探仍失败会由守护进程如实写回 no。"""
        cursor = self.conn.execute(
            "UPDATE app_capability SET level = 'partial', reason = ?, decided_ms = ?,"
            " enabled = CASE WHEN category = 'third_party' THEN 1 ELSE 0 END"
            " WHERE level = 'no' AND category <> 'system'",
            (RECHECK_PENDING, int(time.time() * 1000)),
        )
        self.conn.commit()
        return cursor.rowcount

    def recheck_pending_keys(self) -> set:
        """正在等待重新定性的应用（守护进程采样到它们时强制重写结论）。"""
        rows = self.conn.execute(
            "SELECT app_key FROM app_capability WHERE level = 'partial' AND reason = ?",
            (RECHECK_PENDING,),
        ).fetchall()
        return {str(r["app_key"]) for r in rows}

    # ------------------------------------------------------------ 写片段

    def write_segments(self, segments: Iterable, app_key_to_id: Optional[dict] = None) -> int:
        """批量写入片段（跨零点按天拆开）。外键冲突（缓存的 app_id 与库不一致）
        时回滚 → 清缓存重建 → 重试一次；没有这层重试，清空数据后所有片段
        都会被外键静默拒绝（实测复现过）。"""
        def build(mapping: dict) -> list:
            rows = []
            for seg in segments:
                app_id = mapping.get(seg.app_key)
                if not app_id:
                    continue
                for part in track.split_by_day(seg):
                    end = max(part.end_ms, part.start_ms)   # 乱序时间戳防护（BUG-16）
                    rows.append((app_id, seg.pid, seg.kind, part.start_ms, end,
                                 end - part.start_ms, track.day_of(part.start_ms), seg.flags))
            return rows

        insert_sql = ("INSERT INTO segment (app_id, pid, kind, start_ms, end_ms, dur_ms, day, flags)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
        rows = build(app_key_to_id or self.app_key_to_id())
        for attempt in (0, 1):
            if not rows:
                return 0
            try:
                self.conn.executemany(insert_sql, rows)
                self.conn.commit()
                return len(rows)
            except sqlite3.IntegrityError:
                self.error_count += 1
                if attempt:
                    return 0
                self.conn.rollback()
                self._app_ids = {}
                rows = build(self.app_key_to_id())
            except sqlite3.Error:
                self.error_count += 1
                self.conn.rollback()
                return 0
        return 0

    def log_event(self, ev_type: str, pid: int = 0, app_id: int = 0, detail: str = "") -> None:
        try:
            self.conn.execute(
                "INSERT INTO event (ts_ms, type, pid, app_id, detail) VALUES (?, ?, ?, ?, ?)",
                (int(time.time() * 1000), ev_type, pid, app_id, detail))
            self.conn.commit()
        except sqlite3.Error:
            self.error_count += 1

    # ------------------------------------------------------------ 查询

    def _intervals(self, day_from: str, day_to: str) -> dict:
        """取出区间，按 (app_id, kind) 分组。"""
        rows = self.conn.execute(
            "SELECT app_id, kind, start_ms, end_ms FROM segment WHERE day BETWEEN ? AND ?",
            (day_from, day_to)).fetchall()
        grouped: dict = {}
        for row in rows:
            grouped.setdefault((int(row["app_id"]), str(row["kind"])), []).append(
                (int(row["start_ms"]), int(row["end_ms"])))
        return grouped

    def app_totals(self, day_from: str, day_to: str, limit: int = 0) -> List[dict]:
        """周期内每个应用的前台/后台/活跃总时长，全部用区间并集：同类多进程
        区间重叠、前台与后台也重叠，total 是 fg∪bg 大并集而非相加
        （相加实测虚高过 80%）。"""
        grouped = self._intervals(day_from, day_to)
        meta = {
            int(r["app_id"]): r for r in self.conn.execute(
                "SELECT app_id, app_key, display_name, exe_name, exe_path FROM app").fetchall()
        }
        totals: dict = {}
        union_spans: dict = {}
        for (app_id, kind), spans in grouped.items():
            entry = totals.setdefault(app_id, {"fg_ms": 0, "bg_ms": 0})
            entry["fg_ms" if kind == "fg" else "bg_ms"] += track.union_ms(spans)
            union_spans.setdefault(app_id, []).extend(spans)
        out: List[dict] = []
        for app_id, entry in totals.items():
            row = meta.get(app_id)
            if row is None:
                continue
            total = track.union_ms(union_spans.get(app_id, []))
            out.append({
                "app_key": row["app_key"],
                "display_name": row["display_name"] or row["exe_name"] or row["app_key"],
                "exe_name": row["exe_name"] or "", "exe_path": row["exe_path"] or "",
                "fg_ms": entry["fg_ms"], "bg_ms": entry["bg_ms"], "total_ms": total,
                "fg_ratio": (entry["fg_ms"] / total) if total else 0.0,
            })
        out.sort(key=lambda r: r["total_ms"], reverse=True)
        if limit:
            out = out[:limit]
        return out


    def daily_series(self, day_from: str, day_to: str) -> List[dict]:
        """按天序列（近 N 天迷你柱状图）；active_ms = 当天 fg∪bg 并集。"""
        rows = self.conn.execute(
            "SELECT day, app_id, kind, start_ms, end_ms FROM segment WHERE day BETWEEN ? AND ? ORDER BY day",
            (day_from, day_to)).fetchall()
        grouped: dict = {}
        per_day: dict = {}
        for row in rows:
            day, kind = str(row["day"]), str(row["kind"])
            spans = (int(row["start_ms"]), int(row["end_ms"]))
            grouped.setdefault((day, kind), []).append(spans)
            entry = per_day.setdefault(day, {"day": day, "fg_ms": 0, "bg_ms": 0, "_spans": []})
            entry["fg_ms" if kind == "fg" else "bg_ms"] += track.union_ms([spans])
            entry["_spans"].append(spans)
        for entry in per_day.values():   # fg∪bg 大并集（不分 kind）
            entry["active_ms"] = track.union_ms(entry.pop("_spans"))
        return [per_day[d] for d in sorted(per_day)]

    def totals_summary(self, day_from: str, day_to: str) -> dict:
        series = self.daily_series(day_from, day_to)
        fg, bg, active = (sum(d[k] for d in series)
                          for k in ("fg_ms", "bg_ms", "active_ms"))
        return {"fg_ms": fg, "bg_ms": bg, "total_ms": active,
                "fg_ratio": (fg / active) if active else 0.0}

    def sample_content(self, limit: int = 10) -> dict:
        """给 audit 用：抽样库里的实际内容，让人肉眼看「到底存了什么」。"""
        return {
            "app": [dict(r) for r in self.conn.execute(
                "SELECT app_id, app_key, display_name, exe_name, publisher FROM app LIMIT ?",
                (limit,)).fetchall()],
            "segment": [dict(r) for r in self.conn.execute(
                "SELECT seg_id, app_id, pid, kind, start_ms, end_ms, dur_ms, day FROM segment LIMIT ?",
                (limit,)).fetchall()],
        }

    def table_columns(self) -> dict:
        out = {}
        for table in schema.ALLOWED_COLUMNS:   # 遍历的就是白名单本身（R1）
            try:
                rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
                out[table] = [str(r["name"]) for r in rows]
            except sqlite3.Error:
                out[table] = []
        return out

    # ------------------------------------------------------------ 维护

    def maintenance(self) -> None:
        """清理过期事件/片段、name:pid 垃圾行 + 按需 VACUUM。启动时与每 24h 调用一次。"""
        cutoff = int((time.time() - config.EVENT_KEEP_DAYS * 86400) * 1000)
        seg_cutoff = time.strftime(
            "%Y-%m-%d", time.localtime(time.time() - config.SEGMENT_KEEP_DAYS * 86400))
        try:
            self.conn.execute("DELETE FROM event WHERE ts_ms < ?", (cutoff,))
            self.conn.execute("DELETE FROM segment WHERE day < ?", (seg_cutoff,))
            # 历史遗留的 name:pid-N 垃圾行（BUG-28）：从未采集、无 segment 引用，删除安全
            self.conn.execute("DELETE FROM app_capability WHERE app_key LIKE 'name:pid-%'")
            self.conn.execute(
                "DELETE FROM app WHERE app_key LIKE 'name:pid-%'"
                " AND app_id NOT IN (SELECT app_id FROM segment)")
            self.conn.commit()
        except sqlite3.Error:
            self.error_count += 1
        last = self._meta_get("last_vacuum")
        now_s = int(time.time())
        if not last or now_s - int(last) >= config.VACUUM_MIN_INTERVAL_S:
            try:
                self.conn.execute("VACUUM")
                self._meta_set("last_vacuum", str(now_s))
            except sqlite3.Error:
                self.error_count += 1

    def _meta_get(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key)"
            " DO UPDATE SET value = excluded.value", (key, value))
        self.conn.commit()

    def clear_all(self) -> None:
        """一键清空使用记录（隐私页用）：只删 segment/event，保留 app 元数据。
        清空记录不该丢软件清单；且删 app 行会让守护进程缓存的 app_id 悬空、
        之后所有写库被外键静默拒绝（实测：静默丢光后续记录）。"""
        # 表名写死成字面量，不做任何拼接 —— 这段代码不接受外部输入（R1）
        self.conn.execute("DELETE FROM segment")
        self.conn.execute("DELETE FROM event")
        self.conn.commit()
        self.conn.execute("VACUUM")
        # 已删页必须从 WAL 物理回收，否则"彻底删除"只是逻辑上的（实测 WAL 滞留过 4MB 历史）
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            self.error_count += 1
        self._app_ids = {}
        self._app_seen_flush = {}
