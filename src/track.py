"""片段生成与合并。纯逻辑，零 I/O、零 Qt —— 因此可以脱离环境做单元测试。

离散采样 → 连续区间 (app_key, pid, kind, start_ms, end_ms)。

两条流：
  · 前台：事件驱动，按"前台变化点"切段，边界精确
  · 后台：采样驱动，用滞后计数抗抖动、抗碎片

最后统一 finalize()：合并相邻同类 → 丢弃过短残渣。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

import config

# ---------------------------------------------------------------- 契约


@dataclass
class Segment:
    """一段时间区间。"""

    app_key: str
    pid: int
    kind: str        # 'fg' 前台 | 'bg' 后台活跃
    start_ms: int
    end_ms: int
    flags: int = 0   # 见 config.FLAG_*

    @property
    def dur_ms(self) -> int:
        return max(self.end_ms - self.start_ms, 0)


# ---------------------------------------------------------------- 工具


def day_of(ms: int) -> str:
    """本地日期 YYYY-MM-DD。"""
    return time.strftime("%Y-%m-%d", time.localtime(ms / 1000.0))


def split_by_day(seg: Segment) -> List[Segment]:
    """把跨零点的片段切成每天一段。

    不做这一步，一段 22:00→次日 02:00 的使用会被整天记在前一天，
    按天/按周统计就全错了。
    """
    if day_of(seg.start_ms) == day_of(seg.end_ms - 1):
        return [seg]
    out: List[Segment] = []
    cursor = seg.start_ms
    while cursor < seg.end_ms:
        current_day = day_of(cursor)
        next_midnight = _next_midnight_ms(cursor)
        end = min(seg.end_ms, next_midnight)
        out.append(Segment(seg.app_key, seg.pid, seg.kind, cursor, end, seg.flags))
        if end <= cursor:
            break
        cursor = end
        if day_of(cursor) == current_day:
            break
    return out


def _next_midnight_ms(ms: int) -> int:
    lt = time.localtime(ms / 1000.0)
    base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return int((base + 86400) * 1000)


def union_ms(intervals: Iterable) -> int:
    """区间并集的总毫秒数（sweep 合并）。

    同一应用有几十个进程时，各自的活跃区间会**互相重叠**，
    直接求和会虚高好几倍（Chrome 尤其明显）。所以后台时长必须算并集。
    """
    spans = sorted((int(s), int(e)) for s, e in intervals if e > s)
    if not spans:
        return 0
    total = 0
    cur_start, cur_end = spans[0]
    for start, end in spans[1:]:
        if start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    return total + (cur_end - cur_start)


def build_day_grid(day: str, day_start_ms: int, segs: list) -> dict:
    """数据健康视图网格（v0.5，纯函数）：5 分钟一格标记 + 断档归因。

    segs = [(kind, start_ms, end_ms, flags)]。slots 288 个：0=无记录
    1=仅后台 2=含前台；gaps 只收覆盖窗口内 >=5 分钟的空档（更小属正常
    采样间隔），归因看左侧片段截断 flags：FLAG_LOCK=锁屏停记、
    FLAG_IDLE=空闲截断、否则=低活动。"""
    step = 5 * 60 * 1000
    slots = [0] * (86400 * 1000 // step)
    clean = []
    for kind, start, end, flags in segs:
        clean.append((start, max(end, start), flags))
        # 半开区间 [start, end)：end 恰在格边界时不侵占下一格
        for k in range(max((start - day_start_ms) // step, 0),
                       min((end - day_start_ms - 1) // step, len(slots) - 1) + 1):
            slots[k] = max(slots[k], 2 if kind == "fg" else 1)
    set_at = [i for i, v in enumerate(slots) if v]
    gaps = []
    if clean and set_at:
        cover = (day_start_ms + set_at[0] * step, day_start_ms + set_at[-1] * step + step)
        for (a_start, a_end, a_flags), (b_start, _be, _bf) in zip(clean, clean[1:]):
            if b_start - a_end < step or a_end < cover[0] or b_start > cover[1]:
                continue
            reason = ("lock" if a_flags & config.FLAG_LOCK else
                      "idle" if a_flags & config.FLAG_IDLE else "quiet")
            gaps.append({"start_ms": a_end, "end_ms": b_start, "reason": reason})
    return {"day": day, "slots": slots, "gaps": gaps,
            "first_ms": day_start_ms + set_at[0] * step if set_at else 0,
            "last_ms": day_start_ms + (set_at[-1] + 1) * step if set_at else 0}


# ---------------------------------------------------------------- 前台追踪


class FgTracker:
    """按前台变化点切段。事件驱动的边界是毫秒级精确的。"""

    def __init__(self) -> None:
        self._app_key: Optional[str] = None
        self._pid: int = 0
        self._start_ms: int = 0

    def on_fg(self, app_key: str, pid: int, ts_ms: int, guessed: bool = False) -> List[Segment]:
        if self._app_key is not None and ts_ms < self._start_ms:
            # 迟到的 WinEvent 经 _tick_to_epoch_ms 换算出倒退的时间戳（事件排队越久越早），
            # 不夹住会把新段起点压进旧段区间 → 跨应用前台重叠被重复计时（BUG-26）
            ts_ms = self._start_ms
        if app_key == self._app_key and pid == self._pid:
            return []
        out: List[Segment] = []
        if self._app_key is not None:
            flags = config.FLAG_GUESS if guessed else 0
            out.append(Segment(self._app_key, self._pid, "fg", self._start_ms, ts_ms, flags))
        self._app_key, self._pid, self._start_ms = app_key, pid, ts_ms
        return out

    def force_close(self, ts_ms: int, flag: int) -> List[Segment]:
        if self._app_key is None:
            return []
        seg = Segment(self._app_key, self._pid, "fg", self._start_ms,
                      max(ts_ms, self._start_ms), flag)
        self._app_key = None
        self._pid = 0
        return [seg]

    def current(self) -> Optional[str]:
        return self._app_key


# ---------------------------------------------------------------- 后台追踪


class BgTracker:
    """采样驱动的后台活跃片段，带滞后计数。

    滞后计数解决两个问题：
      · 连续 ACTIVE_N 轮活跃才开段 → 挡掉一次性的瞬时抖动
      · 连续 IDLE_N 轮不活跃才收尾 → 避免把一次采样空档切成碎片
    开段时把起点**回补**到首个活跃采样点，收尾时右边界**外扩**一个采样间隔，
    这样采样间隔内的静默也被覆盖，误差控制在 ±1 个周期内。
    """

    def __init__(self, interval_ms: Optional[int] = None) -> None:
        self._interval_ms = interval_ms or int(config.BG_SAMPLE_INTERVAL_S * 1000)
        self._state: dict = {}   # (app_key, pid) -> 状态字典

    def on_sample(self, app_key: str, pid: int, ts_ms: int, active: bool) -> List[Segment]:
        key = (app_key, pid)
        st = self._state.setdefault(key, {"run": 0, "first": 0, "start": None, "last": 0})
        out: List[Segment] = []
        if active:
            if st["run"] == 0:
                st["first"] = ts_ms
            st["run"] += 1
            st["last"] = ts_ms
            if st["start"] is None and st["run"] >= config.ACTIVE_N:
                st["start"] = st["first"]
        else:
            st["run"] = 0
            if st["start"] is not None and (ts_ms - st["last"]) >= config.IDLE_N * self._interval_ms:
                out.append(self._close(app_key, pid, st))
        return out

    def close_pid(self, pid: int, ts_ms: int) -> List[Segment]:
        """进程退出时收尾（否则那段活跃会永远悬着不落库）。"""
        out: List[Segment] = []
        for (app_key, key_pid), st in list(self._state.items()):
            if key_pid == pid and st["start"] is not None:
                out.append(self._close(app_key, pid, st, flag=config.FLAG_EXIT))
        self._state = {k: v for k, v in self._state.items() if k[1] != pid}
        return out

    def close_all(self, ts_ms: int, flag: int) -> List[Segment]:
        """锁屏 / 休眠 / 退出前，把所有在开的片段闭合掉。"""
        out: List[Segment] = []
        for (app_key, pid), st in list(self._state.items()):
            if st["start"] is not None:
                out.append(self._close(app_key, pid, st, flag=flag))
        self._state.clear()
        return out

    def forget(self, pid: int) -> None:
        self._state = {k: v for k, v in self._state.items() if k[1] != pid}

    def _close(self, app_key: str, pid: int, st: dict, flag: int = 0) -> Segment:
        seg = Segment(app_key, pid, "bg", st["start"], st["last"] + self._interval_ms, flag)
        st["start"] = None
        st["last"] = 0
        st["first"] = 0
        st["run"] = 0
        return seg


# ---------------------------------------------------------------- 收尾


def finalize(segments: Iterable, merge_gap_ms: Optional[int] = None,
             min_ms: Optional[int] = None) -> List[Segment]:
    """合并相邻同类片段，丢弃过短残渣。

    后台采样会产出大量零碎片段，必须在这里吸收掉，否则数据库里
    全是几百毫秒的渣，统计和 UI 都没法看。合并容忍按 kind 分流：
    前台零间隙（FG_MERGE_GAP_MS，缝隙=别的应用占用过，BUG-31），
    后台 MERGE_GAP_MS；merge_gap_ms 参数只覆盖后台那条线（测试用）。
    """
    gap = config.MERGE_GAP_MS if merge_gap_ms is None else merge_gap_ms
    floor_ms = config.MIN_SEG_MS if min_ms is None else min_ms

    ordered = sorted(segments, key=lambda s: (s.app_key, s.pid, s.kind, s.start_ms))
    merged: List[Segment] = []
    for seg in ordered:
        if merged:
            last = merged[-1]
            same_group = (last.app_key, last.pid, last.kind) == (seg.app_key, seg.pid, seg.kind)
            # 前台边界是事件驱动的精确切点（首尾相接），零容忍：缝隙意味着中间
            # 被别的应用真实占用过，焊平会产出跨应用嵌入重叠（BUG-31）。
            # 后台是采样驱动（有间隙正常），且重叠由统计并集消化，保留容忍。
            seg_gap = config.FG_MERGE_GAP_MS if seg.kind == "fg" else gap
            if same_group and seg.start_ms - last.end_ms <= seg_gap:
                last.end_ms = max(last.end_ms, seg.end_ms)
                last.flags |= seg.flags
                continue
        merged.append(Segment(seg.app_key, seg.pid, seg.kind, seg.start_ms, seg.end_ms, seg.flags))
    return [s for s in merged if s.dur_ms >= floor_ms]


def clamp_fg_overlaps(segments: List[Segment]) -> tuple:
    """落库前的最后保险：前台段按互斥语义不允许任何重叠。

    正常情况下 on_fg 夹单调（BUG-26）+ finalize 前台零间隙合并（BUG-31）
    已杜绝重叠；若这里仍发现，说明存在未知路径 —— 截短先出现的段保住
    真实切换点，并返回截短次数供 error_count 计数（R8：异常必须可见）。
    """
    ordered = sorted((s for s in segments if s.kind == "fg"), key=lambda s: s.start_ms)
    clamped = 0
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.start_ms < prev.end_ms:
            prev.end_ms = cur.start_ms
            clamped += 1
    return segments, clamped
