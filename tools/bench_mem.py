r"""内存 / CPU 基准 —— 验证「常驻内存压到 40MB 以内」这个目标有没有做到。

两种用法：
    python.exe tools\bench_mem.py                 # 自己拉一个守护进程来测，测完关掉
    python.exe tools\bench_mem.py --attach        # 测量当前正在运行的守护进程
    python.exe tools\bench_mem.py --seconds 30    # 测 30 秒

为什么 CPU 用「累计 CPU 时间的差分」而不是 cpu_percent：
    cpu_percent(interval=None) 返回的是"自上次调用以来的均值"，
    任何第三方调用都会偷走基线、让下一次读数变成接近 0（本项目实测踩过）。
    累计计数差分没有这个隐患，可重复、可对照。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import psutil      # noqa: E402
import config      # noqa: E402


def _matches(proc) -> bool:
    try:
        cmdline = proc.cmdline()
    except psutil.Error:
        return False
    return (any("app.py" in str(part) for part in cmdline)
            and "--gui" not in cmdline
            and proc.is_running())


def find_daemon() -> "psutil.Process | None":
    """找出真正的守护进程。

    ⚠ 本机 venv 的 python.exe 是「启动器壳」：它会另起真正的解释器后立刻退出，
    于是会出现**两个** cmdline 都含 app.py 的进程。早先版本取创建最早的，
    结果测到了那个已经死掉的壳（psutil 缓存句柄还会返回冻结值，看起来"正常"）
    → 工作集只有 10.5MB 这种明显不对的数字（实测踩过）。

    判据：真解释器的**父进程不匹配**；壳的父进程（我们的测试脚本）是别的进程，
    而壳自己下面还挂着一个匹配的子进程。
    """
    candidates = []
    for proc in psutil.process_iter(["pid", "create_time"]):
        try:
            if _matches(proc):
                candidates.append(proc)
        except psutil.Error:
            continue
    if not candidates:
        return None

    # 排除"有匹配子进程"的那些 —— 它们就是壳
    real = []
    for proc in candidates:
        try:
            has_matching_child = any(_matches(child) for child in proc.children())
        except psutil.Error:
            has_matching_child = False
        if not has_matching_child:
            real.append(proc)
    pool = real or candidates
    # 多个时取创建最晚的（真正的解释器总是后于壳创建）
    pool.sort(key=lambda p: p.create_time(), reverse=True)
    return pool[0]


def sample(proc, seconds: float, label: str) -> dict:
    print("采样 %s（PID %d），共 %.0f 秒：" % (label, proc.pid, seconds))
    print("   %6s  %12s  %14s  %10s" % ("时刻", "工作集(MB)", "私有内存(MB)", "CPU%"))
    print("   " + "-" * 48)
    start = time.monotonic()
    prev_cpu = sum(proc.cpu_times()[:2])
    prev_time = start
    working: list = []
    cpus: list = []
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= seconds:
            break
        time.sleep(min(2.0, max(seconds - elapsed, 0.2)))
        if not proc.is_running():
            print("   （进程已退出）")
            break
        memory = proc.memory_info()
        cpu_total = sum(proc.cpu_times()[:2])
        now = time.monotonic()
        cpu_pct = (cpu_total - prev_cpu) / max(now - prev_time, 0.001) * 100
        prev_cpu, prev_time = cpu_total, now
        ws_mb = memory.rss / 1048576.0
        working.append(ws_mb)
        cpus.append(cpu_pct)
        print("   %5.1fs  %12.1f  %14.1f  %9.2f%%"
              % (time.monotonic() - start, ws_mb, memory.private / 1048576.0, cpu_pct))
    # 第一段包含启动期的初始化开销，不算进稳态
    steady = cpus[1:] if len(cpus) > 2 else cpus
    return {
        "ws_min": min(working) if working else 0.0,
        "ws_max": max(working) if working else 0.0,
        "ws_avg": sum(working) / len(working) if working else 0.0,
        "cpu_peak": max(cpus) if cpus else 0.0,
        "cpu_steady": sum(steady) / len(steady) if steady else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--attach", action="store_true", help="测量已在运行的守护进程")
    args = parser.parse_args()

    proc = find_daemon()
    launched = None
    if args.attach:
        if proc is None:
            print("没有找到正在运行的守护进程。先启动程序，或用不带 --attach 的方式运行本脚本。")
            return 1
    elif proc is None:
        print("正在启动守护进程…")
        launched = subprocess.Popen([sys.executable, str(ROOT / "src" / "app.py")],
                                    shell=False, close_fds=True)
        for _ in range(40):
            time.sleep(0.5)
            proc = find_daemon()
            if proc:
                break
        if proc is None:
            print("启动失败：没找到守护进程")
            return 1
        print("已启动，PID %d" % proc.pid)
    else:
        print("检测到已在运行的守护进程，直接测量它。")

    try:
        stats = sample(proc, args.seconds, "守护进程")
    finally:
        if launched is not None:
            print("\n正在关闭测试用的守护进程…")
            launched.terminate()
            try:
                proc.terminate()
            except psutil.Error:
                pass

    print()
    print("=" * 58)
    print("结论")
    print("  工作集：最小 %.1f MB / 平均 %.1f MB / 最大 %.1f MB"
          % (stats["ws_min"], stats["ws_avg"], stats["ws_max"]))
    print("  CPU   ：启动峰值 %.2f%%（含初始化）/ 稳态平均 %.2f%%"
          % (stats["cpu_peak"], stats["cpu_steady"]))
    ok_mem = stats["ws_max"] <= 40.0
    ok_cpu = stats["cpu_steady"] <= 1.0
    print("  [%s] 工作集 <= 40 MB（目标；对照：PySide6 常驻方案约 55 MB）"
          % ("√" if ok_mem else "x"))
    print("  [%s] 稳态 CPU <= 1%%（空闲时应接近 0；启动瞬间的尖峰属正常）"
          % ("√" if ok_cpu else "x"))
    print("=" * 58)
    return 0 if (ok_mem and ok_cpu) else 1


if __name__ == "__main__":
    raise SystemExit(main())
