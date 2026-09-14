r"""前台采集验证脚本（P1）—— 只打印，不写数据库。

用法：
    双击 `验证-前台采集.bat`
    或  python.exe tools\smoke_collect.py [--seconds 30]

验证要点：
    1. Alt-Tab 切窗口时**毫秒级**打印出新前台程序（事件驱动生效）
    2. 最小化窗口被判为「非前台」（IsIconic 判掉了）
    3. 以管理员身份运行的程序也能打印出正确路径（不需要提权即可读到）
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import collect          # noqa: E402
import sysmsg           # noqa: E402
import winutil          # noqa: E402


def fmt_time(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0)
    return dt.strftime("%H:%M:%S.") + f"{ts_ms % 1000:03d}"


def describe(pid: int) -> str:
    if pid <= 0:
        return "非前台（无窗口 / 窗口已最小化）"
    path = winutil.process_image_path(pid)
    if not path:
        return f"<读不到路径> pid={pid}   ← 系统拒绝访问，正常现象（该程序不可监测）"
    info = winutil.file_version_info(path)
    name = info.get("display_name") or os.path.basename(path)
    return f"{name}  |  pid={pid}\n{'':24}路径: {path}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=0, help="运行多少秒后自动退出（0=一直跑）")
    args = parser.parse_args()

    deadline = time.monotonic() + args.seconds if args.seconds > 0 else None
    state = {"pid": None, "since_ms": None}

    def handle(sample: collect.Sample, source: str) -> None:
        if sample.pid == state["pid"]:
            return
        now_ms = sample.ts_ms
        if state["since_ms"] is not None and state["pid"] is not None:
            held = now_ms - state["since_ms"]
            print(f"{'':24}—— 上一个前台持续了 {held} ms")
        state["pid"] = sample.pid
        state["since_ms"] = now_ms
        print(f"[{fmt_time(now_ms)} {source}] {describe(sample.pid)}")

    def on_tick() -> None:
        # 先消费事件（毫秒级精度），再轮询兜底
        for sample in watcher.drain():
            handle(sample, "事件")
        fallback = watcher.poll()
        if fallback is not None:
            handle(fallback, "轮询")

    print("=" * 78)
    print(" 前台采集验证 —— Alt-Tab 切换窗口，观察打印是否即时且准确")
    print(" 最小化窗口应显示「非前台」；Ctrl+C 退出")
    print("=" * 78)

    hwnd = winutil.foreground_hwnd()
    watcher = collect.FgWatcher(lambda sample: None)
    if not watcher.install():
        print("[x] SetWinEventHook 注册失败，只能靠轮询（精度降为 1 秒）")
    else:
        print("[√] WinEvent 钩子注册成功（毫秒级精度）")
    print(f"    当前前台 hwnd={hex(hwnd) if hwnd else 0}")
    print()

    def should_stop() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    try:
        sysmsg.pump_loop(on_tick, should_stop)
    except KeyboardInterrupt:
        pass
    finally:
        watcher.uninstall()
        print("\n[x] 已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
