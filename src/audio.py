"""音频会话检测（v0.2 A1）：哪些进程正在出声。零 Qt、零网络。

链路（pycaw，MIT 许可，基于 comtypes 的 Core Audio 完整包装）：
默认渲染端点 → IAudioSessionManager2 → 会话枚举 → 每会话
IAudioMeterInformation.GetPeakValue()（瞬时峰值 0.0~1.0）。

线程契约：COM 是每线程状态，ensure_init() 线程本地幂等；
active_pids() 内部会自动完成首次初始化，谁都能安全调用。
已初始化为其它套间模式（如 comtypes import 时主线程 STA）也视为就绪。

失败处理（R8）：枚举失败计入 error_count 并保留上次结果，
不静默吞掉也不让异常打断采集；error_count 供自测/诊断读取。

判活口径（两个消费者）：
- active_pids()：此刻正在出声的 pid —— 后台判活的第三指标
  （CPU/磁盘之外，"只听不出声就判不活跃"盲区由此闭环）
- recently_audible_pids()：AUDIO_EXEMPT_WINDOW_S 内出过声的 pid
  —— AFK 豁免用，视频对白间隙不让闸门翻转（须绑定前台 pid 判断，
  否则"人走了音乐还放着"会让 BUG-32 复发）
"""
import threading
import time

import comtypes
import config

error_count = 0            # R8：枚举失败必须可见
_tls = threading.local()   # COM 初始化是每线程状态
_current: set = set()      # 此刻出声的 pid
_last_sound: dict = {}     # pid -> 最后一次出声的 monotonic 时刻
_refreshed_at = 0.0


def ensure_init() -> None:
    """当前线程的 COM 初始化（幂等）。MTA：工作线程无消息泵，STA 会卡。"""
    if getattr(_tls, "inited", False):
        return
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass    # 已按其它模式初始化（comtypes import 时主线程 STA）—— 直接可用
    _tls.inited = True


def is_audible(peak: float) -> bool:
    """单点判定（纯函数，便于回归）。"""
    return peak >= config.AUDIO_ACTIVE_PEAK


def active_pids() -> set:
    """此刻正在出声的 pid 集合（内部按 AUDIO_POLL_INTERVAL_S 节流）。"""
    now = time.monotonic()
    if now - _refreshed_at >= config.AUDIO_POLL_INTERVAL_S:
        _refresh(now)
    return set(_current)


def recently_audible_pids() -> set:
    """窗口内出过声的 pid（AFK 豁免用）。失败时退化到最近一次成功结果。"""
    active_pids()   # 顺带按节流刷新
    now = time.monotonic()
    return {p for p, t in _last_sound.items()
            if now - t <= config.AUDIO_EXEMPT_WINDOW_S}


def _refresh(now_mono: float) -> None:
    """重新枚举音频会话。设备瞬断时保留上次结果，只记错误（R8）。"""
    global _current, _refreshed_at, error_count
    ensure_init()
    try:
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        now_sound: set = set()
        for s in AudioUtilities.GetAllSessions():
            pid = s.ProcessId
            if not pid:
                continue    # 系统音等无主会话，不归因给任何应用
            try:
                meter = s._ctl.QueryInterface(IAudioMeterInformation)
                peak = meter.GetPeakValue()
            except (OSError, comtypes.COMError):
                continue
            if is_audible(peak):
                now_sound.add(pid)
                _last_sound[pid] = now_mono
        _current = now_sound
        cutoff = now_mono - config.AUDIO_EXEMPT_WINDOW_S * 4
        for pid in [p for p, t in _last_sound.items() if t < cutoff]:
            _last_sound.pop(pid, None)   # 防记忆表无限增长
    except Exception:
        error_count += 1
    _refreshed_at = now_mono
