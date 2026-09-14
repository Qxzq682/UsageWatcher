r"""守护进程自检：跑几秒，然后把「采集链路通没通、为什么记/不记某个程序」摊开打印。

为什么放在 tools/ 而不是 src/：这是**诊断工具**，不是产品功能。
放在 src/ 会让生产代码为了测试输出而变胖。

用法：python.exe tools\smoke_daemon.py [--seconds 8]
     也可以经 src\app.py --smoke 间接调用。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import app         # noqa: E402
import apps        # noqa: E402
import track       # noqa: E402
import winutil     # noqa: E402


def fmt_duration(ms: int) -> str:
    minutes = int(ms // 60000)
    if minutes < 60:
        return "%d 分" % minutes
    return "%d 小时 %02d 分" % (minutes // 60, minutes % 60)


def run(seconds: float = 8.0) -> int:
    """跑一次完整链路自检。

    为什么要把决策链打出来：自检时前台很可能是 LockApp.exe / ApplicationFrameHost.exe
    这类系统程序，按默认策略本来就不采集 —— 只打印"记录了 0 个应用"会让人误以为是 bug。
    """
    daemon = app.Daemon()
    if not daemon.start():
        print("[x] 守护进程启动失败")
        return 1

    def stopper() -> None:
        time.sleep(seconds)
        daemon.stop_event.set()
        daemon.window.post_quit()

    threading.Thread(target=stopper, daemon=True).start()
    daemon.window.run(on_tick=lambda: None,
                      should_stop=daemon.stop_event.is_set, tick_ms=500)
    daemon.stop_and_flush()

    today = track.day_of(app._now_ms())
    rows = daemon.store.app_totals(today, today, limit=8)
    db_path = daemon.store.path
    errors = daemon.store.error_count

    print("自检结果")
    print("  数据库          : %s" % db_path)
    print("  采集错误计数    : %d  （0 表示整条链路没有异常）" % errors)
    print("  前台识别        : ", end="")
    fg_pid = winutil.pid_of_window(winutil.foreground_hwnd())
    if not fg_pid:
        print("无（可能正在锁屏 / 没有活动窗口）")
    else:
        info = daemon.resolver.resolve(fg_pid)
        cap = apps.capability_of(info, daemon.resolver.fail_count(info.app_key))
        collected = cap.enabled and cap.level != "no"
        print("%s  (pid=%d)" % (info.display_name or info.exe_name, fg_pid))
        print("  归类 / 判定     : %s / %s（默认%s）"
              % (cap.category, cap.level, "采集" if collected else "不采集"))
        if not collected:
            print("  不采集的原因    : %s"
                  % (cap.reason or "该项默认不勾选（可在「监测范围」里手动勾选）"))
    print("  今日已记录应用  : %d 个" % len(rows))
    for row in rows:
        print("      %-26s 前台 %s / 后台 %s"
              % (row["display_name"], fmt_duration(row["fg_ms"]), fmt_duration(row["bg_ms"])))
    daemon.shutdown()
    return 0 if errors == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=8.0)
    args = parser.parse_args()
    return run(args.seconds)


if __name__ == "__main__":
    raise SystemExit(main())
