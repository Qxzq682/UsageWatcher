"""全局配置与阈值 —— 本项目唯一的调参入口。

写在最前面的三条约束（每次改本文件前先读一遍）：
1. 所有阈值只允许在这里定义一次（SSOT）。其它模块**必须**用 `config.XXX` 访问，
   不许 `from config import XXX`，否则会被 tools/test_arch.py 判为重复定义。
2. 不引入配置类继承 / 多层默认值合并 —— 一个扁平 dict 足够，多了就是屎山。
3. 本模块不得 import PySide6（内存目标前提）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "UsageWatcher"
APP_TITLE = "软件使用时长监测"
APP_VERSION = "0.1.0"

# ---------------------------------------------------------------- 路径

# 数据只写用户私有目录 %APPDATA%，不写程序目录（Program Files 无写权限）
_DATA_SUBDIR = APP_NAME


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / _DATA_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "usage.db"


def config_path() -> Path:
    return data_dir() / "config.json"


def audit_report_path() -> Path:
    return data_dir() / "audit_report.txt"


def stop_flag_path() -> Path:
    """停止标志文件。

    这是「零 IPC」设计下的优雅停止通道：停止脚本只创建这个文件，
    守护进程自己发现后走正常收尾流程（闭合在开片段 → 写库 → 退出），
    不用强杀进程，也就不会丢掉"用户正在用的那一段"。
    """
    return data_dir() / "stop.flag"


# ---------------------------------------------------------------- 采集阈值

FG_POLL_INTERVAL_S = 1.0          # 前台轮询兜底周期
BG_SAMPLE_INTERVAL_S = 5.0        # 后台采样周期（活跃时）
BG_SAMPLE_INTERVAL_IDLE_S = 15.0  # 空闲自适应降频后的采样周期
IDLE_DOWNGRADE_AFTER_S = 300      # 无输入超过这么久就降频
CPU_ACTIVE_PCT = 2.0              # 单核百分比阈值（psutil 口径，可 >100）
IO_ACTIVE_BPS = 32768             # 磁盘读写字节/秒 阈值（32KB/s）
AUDIO_ACTIVE_PEAK = 0.01          # 音频会话峰值 ≥ 此值算"正在出声"（实测播音 0.27/静默 0.000）
AUDIO_POLL_INTERVAL_S = 1.0       # 音频会话枚举节流（COM 调用约 1ms，1s 足够）
AUDIO_EXEMPT_WINDOW_S = 30        # AFK 豁免记忆窗：前台应用 30s 内出过声就不截断（防对白间隙翻转）
ACTIVE_N = 2                      # 连续 N 轮活跃才开段
IDLE_N = 3                        # 连续 N 轮不活跃才收尾
MERGE_GAP_MS = 10000              # 后台相邻同类片段间隔 <= 此值就合并
FG_MERGE_GAP_MS = 0               # 前台合并零容忍：缝隙=别的应用真实占用过，
                                  # 焊平会产出跨应用嵌入重叠（BUG-31）
FG_IDLE_CUTOFF_S = 300            # 无输入空闲超过这么久就截断前台段：人不在这，
                                  # 前台窗口挂着不等于"正在使用"（BUG-32）
MIN_SEG_MS = 3000                 # 合并后仍短于此值的片段丢弃
MAX_CANDIDATES = 120              # 候选进程上限
CANDIDATE_REBUILD_S = 10.0        # 候选集合重建周期
FLUSH_INTERVAL_S = 60.0           # 攒批写库周期
UNMONITORABLE_FAIL_N = 3          # 连续 N 次读路径失败才定性「不可监测」
APP_SEEN_FLUSH_S = 300            # 缓存命中时至少间隔这么久才回写一次 last_seen_ms
PID_KEY_TTL_S = 600               # pid->app 缓存有效期（Windows 会复用 PID，防张冠李戴）
UI_REFRESH_INTERVAL_S = 5         # 统计窗口自动刷新周期（片段落库有延迟，太快没意义）

# 片段 flags 位含义（与 data.py 的 CHECK 无关，仅语义）
FLAG_RESUME = 1   # 跨唤醒被截断
FLAG_LOCK = 2     # 锁屏/暂停被截断
FLAG_EXIT = 4     # 进程退出收尾
FLAG_GUESS = 8    # 边界靠轮询推测（非事件驱动）
FLAG_IDLE = 16    # 无输入空闲（AFK）被截断

EVENT_KEEP_DAYS = 30   # event 表保留天数
SEGMENT_KEEP_DAYS = 365  # segment 表保留天数：超龄片段在维护时清理，防止库无限膨胀
VACUUM_MIN_INTERVAL_S = 7 * 86400   # 两次 VACUUM 的最小间隔

# ---------------------------------------------------------------- 后台维护

EMPTY_WORKING_SET_INTERVAL_S = 300   # 每 5 分钟（仅写完库后）压缩一次工作集

# ---------------------------------------------------------------- 用户配置

# 只放"用户会改、且改了有意义"的项；阈值不开放给配置文件，避免两处真理
DEFAULTS: dict = {
    "paused": False,
    "rank_limit": 50,
}


def load() -> dict:
    """读用户配置；文件不存在或损坏都返回默认值（不让配置问题拖垮程序）。"""
    cfg = dict(DEFAULTS)
    path = config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for key in DEFAULTS:
        if key in raw:
            cfg[key] = raw[key]
    return cfg


def save(cfg: dict) -> None:
    """只写已知键，避免把随机内容落盘。"""
    clean = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    path = config_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
