"""数据库表结构定义（DDL）与字段白名单。

单独成文件的原因：DDL 是"数据结构"，Store 是"数据访问"，两件事。
放一起会让 data.py 超过单文件行数上限，而且改表结构时要在几百行里翻。

SQL 只允许出现在本文件与 data.py —— 由 tools/test_arch.py 断言。
"""

from __future__ import annotations


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS app (
  app_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  app_key       TEXT    NOT NULL UNIQUE,
  exe_path      TEXT,
  exe_name      TEXT,
  display_name  TEXT,
  version       TEXT,
  publisher     TEXT,
  first_seen_ms INTEGER NOT NULL,
  last_seen_ms  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_lastseen ON app(last_seen_ms);

CREATE TABLE IF NOT EXISTS app_capability (
  app_key    TEXT PRIMARY KEY,
  level      TEXT NOT NULL CHECK (level IN ('yes','partial','no')),
  reason     TEXT,
  category   TEXT,
  enabled    INTEGER NOT NULL DEFAULT 0,
  decided_ms INTEGER NOT NULL,
  CHECK (level <> 'no' OR enabled = 0)
);

CREATE TABLE IF NOT EXISTS segment (
  seg_id   INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id   INTEGER NOT NULL REFERENCES app(app_id),
  pid      INTEGER,
  kind     TEXT    NOT NULL CHECK (kind IN ('fg','bg')),
  start_ms INTEGER NOT NULL,
  end_ms   INTEGER NOT NULL,
  dur_ms   INTEGER NOT NULL,
  day      TEXT    NOT NULL,
  flags    INTEGER NOT NULL DEFAULT 0,
  CHECK (end_ms >= start_ms),
  CHECK (dur_ms = end_ms - start_ms)
);
CREATE INDEX IF NOT EXISTS idx_seg_day_kind ON segment(day, kind);
CREATE INDEX IF NOT EXISTS idx_seg_app_day  ON segment(app_id, day, kind);
CREATE INDEX IF NOT EXISTS idx_seg_start    ON segment(start_ms);

CREATE TABLE IF NOT EXISTS event (
  ev_id  INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_ms  INTEGER NOT NULL,
  type   TEXT    NOT NULL,
  pid    INTEGER,
  app_id INTEGER,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_ts ON event(ts_ms);
"""

# 允许出现在库里的字段名 —— audit 会拿它对照，确认没有内容型字段混进来
ALLOWED_COLUMNS = {
    "app": {"app_id", "app_key", "exe_path", "exe_name", "display_name",
            "version", "publisher", "first_seen_ms", "last_seen_ms"},
    "app_capability": {"app_key", "level", "reason", "category", "enabled", "decided_ms"},
    "segment": {"seg_id", "app_id", "pid", "kind", "start_ms", "end_ms", "dur_ms", "day", "flags"},
    "event": {"ev_id", "ts_ms", "type", "pid", "app_id", "detail"},
    "meta": {"key", "value"},
}
