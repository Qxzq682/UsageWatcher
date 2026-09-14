"""存储层：SQLite（WAL）。

**SQL 只允许出现在本文件与 schema.py**，UI 层只能调用这里暴露的查询函数、
不许自己拼 SQL —— 这条约束由 tools/test_arch.py 自动断言，
防止迭代中长出第二套查询逻辑。

安全约束：全部参数化查询（R1），禁止任何字符串拼接 SQL。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional

import config
import schema
import track


class _LockedConnection:
    """给 sqlite3 连接套一把锁，让它能安全地跨线程使用。

    为什么需要：python 的 sqlite3 连接默认**绑定创建它的线程**，
    在别的线程里用会直接抛
    "SQLite objects created in a thread can only be used in that same thread"
    （本项目实测踩过：Store 在主线程建、被采集工作线程用 → 采集整条链静默失效）。

    做法：连接用 check_same_thread=False 打开，再用这把锁把每次调用串起来。
    做成适配器而不是在每个方法里手写 with——那样要改 20 处，
    而且以后新增方法忘了加锁就会重新引入这个 bug。
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def execute(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, rows):
        with self._lock:
            return self._conn.executemany(sql, rows)

    def executescript(self, script: str):
        with self._lock:
            return self._conn.executescript(script)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


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
        self.error_count = 0     # R8：写库失败必须可见

    def _apply_pragmas(self) -> None:
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA foreign_keys=ON",
            "PRAGMA busy_timeout=5000",
        ):
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
        """写入或更新应用元信息，返回 app_id。"""
        cached = self._app_ids.get(info.app_key)
        if cached is not None:
            return cached
        now = int(time.time() * 1000)
        self.conn.execute(
            """INSERT INTO app (app_key, exe_path, exe_name, display_name, version,
                                publisher, first_seen_ms, last_seen_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(app_key) DO UPDATE SET
                 exe_path=excluded.exe_path, exe_name=excluded.exe_name,
                 display_name=excluded.display_name, version=excluded.version,
                 publisher=excluded.publisher, last_seen_ms=excluded.last_seen_ms""",
            (info.app_key, info.exe_path, info.exe_name, info.display_name,
             info.version, info.publisher, now, now),
        )
        row = self.conn.execute(
            "SELECT app_id FROM app WHERE app_key = ?", (info.app_key,)
        ).fetchone()
        app_id = int(row["app_id"]) if row else 0
        self._app_ids[info.app_key] = app_id
        return app_id

    def app_key_to_id(self) -> dict:
        if not self._app_ids:
            rows = self.conn.execute("SELECT app_id, app_key FROM app").fetchall()
            self._app_ids = {r["app_key"]: int(r["app_id"]) for r in rows}
        return self._app_ids

    def known_exe_paths(self, hours: int = 24) -> set:
        """近 N 小时出现过的应用 exe 路径 —— 用于后台采样候选集合。"""
        cutoff = int((time.time() - hours * 3600) * 1000)
        rows = self.conn.execute(
            "SELECT exe_path FROM app WHERE last_seen_ms >= ? AND exe_path <> ''",
            (cutoff,),
        ).fetchall()
        return {str(r["exe_path"]).lower() for r in rows}

    # ------------------------------------------------------------ 可监测性

    def upsert_capability(self, cap) -> None:
        """写入可监测性。

        注意 enabled 的最终决定权在 app_capability 表，不在 config.json ——
        level='no' 的行**恒为 enabled=0**（数据库层 CHECK 兜底），
        所以手工改配置文件也绕不过去（不可监测四重加固的第 2 层）。
        """
        enabled = 0 if cap.level == "no" else int(bool(cap.enabled))
        self.conn.execute(
            """INSERT INTO app_capability (app_key, level, reason, category, enabled, decided_ms)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(app_key) DO UPDATE SET
                 level=excluded.level, reason=excluded.reason, category=excluded.category,
                 enabled=CASE WHEN excluded.level='no' THEN 0 ELSE app_capability.enabled END,
                 decided_ms=excluded.decided_ms""",
            (cap.app_key, cap.level, cap.reason, cap.category, enabled,
             int(time.time() * 1000)),
        )

    def set_enabled(self, app_key: str, enabled: bool) -> bool:
        """用户勾选。返回是否真的改了（不可监测项会被拒绝）。"""
        cursor = self.conn.execute(
            "UPDATE app_capability SET enabled = ? WHERE app_key = ? AND level <> 'no'",
            (int(bool(enabled)), app_key),
        )
        return cursor.rowcount > 0

    def enabled_keys(self) -> set:
        rows = self.conn.execute(
            "SELECT app_key FROM app_capability WHERE enabled = 1 AND level <> 'no'"
        ).fetchall()
        return {str(r["app_key"]) for r in rows}

    def capability_rows(self) -> List[sqlite3.Row]:
        return self.conn.execute(
            """SELECT c.app_key, c.level, c.reason, c.category, c.enabled,
                      a.display_name, a.exe_name, a.exe_path, a.publisher
               FROM app_capability c LEFT JOIN app a ON a.app_key = c.app_key
               ORDER BY CASE c.level WHEN 'no' THEN 2 WHEN 'partial' THEN 1 ELSE 0 END,
                        a.display_name"""
        ).fetchall()

    # ------------------------------------------------------------ 写片段

    def write_segments(self, segments: Iterable, app_key_to_id: Optional[dict] = None) -> int:
        """批量写入片段。跨零点的片段在这里按天拆开。

        只在片段闭合时调用（不是每秒），所以一次事务写完即可。
        """
        mapping = app_key_to_id or self.app_key_to_id()
        rows = []
        for seg in segments:
            app_id = mapping.get(seg.app_key)
            if not app_id:
                continue
            for part in track.split_by_day(seg):
                rows.append((app_id, seg.pid, seg.kind, part.start_ms, part.end_ms,
                             part.dur_ms, track.day_of(part.start_ms), seg.flags))
        if not rows:
            return 0
        try:
            self.conn.executemany(
                """INSERT INTO segment (app_id, pid, kind, start_ms, end_ms, dur_ms, day, flags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self.conn.commit()
            return len(rows)
        except sqlite3.Error:
            self.error_count += 1
            return 0

    def log_event(self, ev_type: str, pid: int = 0, app_id: int = 0, detail: str = "") -> None:
        try:
            self.conn.execute(
                "INSERT INTO event (ts_ms, type, pid, app_id, detail) VALUES (?, ?, ?, ?, ?)",
                (int(time.time() * 1000), ev_type, pid, app_id, detail),
            )
            self.conn.commit()
        except sqlite3.Error:
            self.error_count += 1

    # ------------------------------------------------------------ 查询

    def _intervals(self, day_from: str, day_to: str) -> dict:
        """取出区间，按 (app_id, kind) 分组。"""
        rows = self.conn.execute(
            """SELECT app_id, kind, start_ms, end_ms FROM segment
               WHERE day BETWEEN ? AND ?""",
            (day_from, day_to),
        ).fetchall()
        grouped: dict = {}
        for row in rows:
            grouped.setdefault((int(row["app_id"]), str(row["kind"])), []).append(
                (int(row["start_ms"]), int(row["end_ms"]))
            )
        return grouped

    def app_totals(self, day_from: str, day_to: str, limit: int = 0) -> List[dict]:
        """周期内每个应用的前台/后台时长。

        前台与后台都用**区间并集**计算：同一应用多进程的区间会重叠，
        直接求和会虚高（Chrome 类应用尤其严重）。
        """
        grouped = self._intervals(day_from, day_to)
        meta = {
            int(r["app_id"]): r
            for r in self.conn.execute(
                "SELECT app_id, app_key, display_name, exe_name, exe_path FROM app"
            ).fetchall()
        }
        totals: dict = {}
        for (app_id, kind), spans in grouped.items():
            entry = totals.setdefault(app_id, {"fg_ms": 0, "bg_ms": 0})
            entry["fg_ms" if kind == "fg" else "bg_ms"] += track.union_ms(spans)

        out: List[dict] = []
        for app_id, entry in totals.items():
            row = meta.get(app_id)
            if row is None:
                continue
            total = entry["fg_ms"] + entry["bg_ms"]
            out.append({
                "app_key": row["app_key"],
                "display_name": row["display_name"] or row["exe_name"] or row["app_key"],
                "exe_name": row["exe_name"] or "",
                "exe_path": row["exe_path"] or "",
                "fg_ms": entry["fg_ms"],
                "bg_ms": entry["bg_ms"],
                "total_ms": total,
                "fg_ratio": (entry["fg_ms"] / total) if total else 0.0,
            })
        out.sort(key=lambda r: r["total_ms"], reverse=True)
        if limit:
            out = out[:limit]
        return out

    def daily_series(self, day_from: str, day_to: str) -> List[dict]:
        """按天序列（用于近 N 天迷你柱状图）。"""
        rows = self.conn.execute(
            """SELECT day, app_id, kind, start_ms, end_ms FROM segment
               WHERE day BETWEEN ? AND ? ORDER BY day""",
            (day_from, day_to),
        ).fetchall()
        grouped: dict = {}
        for row in rows:
            key = (str(row["day"]), str(row["kind"]))
            grouped.setdefault(key, []).append((int(row["start_ms"]), int(row["end_ms"])))
        per_day: dict = {}
        for (day, kind), spans in grouped.items():
            entry = per_day.setdefault(day, {"day": day, "fg_ms": 0, "bg_ms": 0})
            entry["fg_ms" if kind == "fg" else "bg_ms"] += track.union_ms(spans)
        return [per_day[d] for d in sorted(per_day)]

    def totals_summary(self, day_from: str, day_to: str) -> dict:
        series = self.daily_series(day_from, day_to)
        fg = sum(d["fg_ms"] for d in series)
        bg = sum(d["bg_ms"] for d in series)
        return {"fg_ms": fg, "bg_ms": bg, "total_ms": fg + bg,
                "fg_ratio": (fg / (fg + bg)) if (fg + bg) else 0.0}

    def sample_content(self, limit: int = 10) -> dict:
        """给 audit 用：抽样打印库里的实际内容，让人肉眼看「到底存了什么」。"""
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
        for table in schema.ALLOWED_COLUMNS:
            # PRAGMA 不支持参数占位符，只能拼表名；因此先做白名单守卫，
            # 拼进去的一定是代码里写死的表名，不含任何外部输入（R1）
            if table not in schema.ALLOWED_COLUMNS:
                continue
            try:
                rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
                out[table] = [str(r["name"]) for r in rows]
            except sqlite3.Error:
                out[table] = []
        return out

    # ------------------------------------------------------------ 维护

    def maintenance(self) -> None:
        """清理过期事件 + 按需 VACUUM。启动时与每 24h 调用一次。"""
        cutoff = int((time.time() - config.EVENT_KEEP_DAYS * 86400) * 1000)
        try:
            self.conn.execute("DELETE FROM event WHERE ts_ms < ?", (cutoff,))
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
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def clear_all(self) -> None:
        """一键清空数据（隐私页用）。只删记录，不碰表结构。"""
        # 表名写死成字面量，不做任何拼接 —— 这段代码不接受外部输入（R1）
        self.conn.execute("DELETE FROM segment")
        self.conn.execute("DELETE FROM event")
        self.conn.execute("DELETE FROM app_capability")
        self.conn.execute("DELETE FROM app")
        self.conn.commit()
        self.conn.execute("VACUUM")
        self._app_ids = {}
