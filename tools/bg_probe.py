r"""后台活跃判定探针 —— 用于**实机标定阈值**。

为什么需要它：`CPU_ACTIVE_PCT` 与 `IO_ACTIVE_BPS` 的默认值（2.0% / 32KB/s）
只是合理起点，不同机器、不同软件差异很大。用它把"挂后台的软件"真实的
CPU 与磁盘 I/O 打出来，再决定要不要调 `src\config.py`。

用法（双击 `验证-后台判定.bat`，或命令行）：
    python.exe tools\bg_probe.py                 # 实时观察，Ctrl+C 退出
    python.exe tools\bg_probe.py --rounds 6      # 只跑 6 轮
    python.exe tools\bg_probe.py --selftest      # 受控实验：自建忙碌/挂机子进程做对照
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import apps        # noqa: E402
import collect     # noqa: E402
import config      # noqa: E402
import winutil     # noqa: E402

_HERE = Path(__file__).resolve().parent


def _known_exes() -> set:
    """把 Python 自身当作"已知应用"，好让无窗口的对照子进程进入候选集合。

    注意要用**真解释器**路径：venv 的 python.exe 是启动器壳，
    壳会另起 `versions\\...\\python.exe` 来干活，两者路径不同（实测）。
    """
    paths = {sys.executable, getattr(sys, "_base_executable", "")}
    return {p.lower() for p in paths if p}


def _spawn_child(script: str, args: list) -> tuple:
    """起子进程，并从它自己写的 pid 文件里取回**真实 PID**。"""
    pid_file = os.path.join(tempfile.gettempdir(), f"uw_pid_{os.getpid()}_{os.path.basename(script)}.txt")
    if os.path.exists(pid_file):
        os.remove(pid_file)
    proc = subprocess.Popen([sys.executable, str(_HERE / script), pid_file, *args])
    real_pid = 0
    for _ in range(100):        # 最多等 5 秒
        if os.path.exists(pid_file):
            try:
                text = open(pid_file, encoding="utf-8").read().strip()
                if text:
                    real_pid = int(text)
                    break
            except (OSError, ValueError):
                pass
        time.sleep(0.05)
    return proc, real_pid, pid_file


def _describe(pid: int) -> str:
    path = winutil.process_image_path(pid)
    if not path:
        return f"pid={pid} <读不到路径>"
    return f"{os.path.basename(path)}  (pid={pid})"


def run_probe(rounds: int, only_pid: set) -> dict:
    """跑若干轮采样并打印明细。返回 {pid: [active, ...]}。"""
    resolver = apps.Resolver()
    sampler = collect.BgSampler(resolver)
    known = _known_exes()
    history: dict = {}

    print("阈值：CPU >= %.1f%%  或  磁盘 I/O >= %d B/s" % (
        config.CPU_ACTIVE_PCT, config.IO_ACTIVE_BPS))
    print("采样间隔：活跃 %.0fs / 空闲 %.0fs（无输入超过 %d 秒会自动降频）" % (
        config.BG_SAMPLE_INTERVAL_S,
        config.BG_SAMPLE_INTERVAL_IDLE_S,
        config.IDLE_DOWNGRADE_AFTER_S,
    ))
    print()

    for rnd in range(1, rounds + 1):
        time.sleep(sampler.interval())
        samples, exited = sampler.sample(int(time.time() * 1000), known)
        for s in samples:
            history.setdefault(s.pid, []).append(s.active)
        rows = [s for s in samples if not only_pid or s.pid in only_pid]
        print("[第 %d 轮] 候选 %d 个 · 采样 %d 条 · 退出 %d 个 · 采集错误 %d" % (
            rnd, len(sampler._candidates), len(samples), len(exited), sampler.error_count))
        for s in sorted(rows, key=lambda x: (not x.active, x.pid)):
            cpu_pct, io_rate = sampler.rates(s.pid)
            print("     %s  %-34s cpu=%6.2f%%  io=%8.0f B/s" % (
                "活跃" if s.active else "  --", _describe(s.pid), cpu_pct, io_rate))
        print()
    return history


_STALE_GLOBS = ("uw_probe_io*.bin", "uw_diag_io*.bin")


def _sweep_stale() -> None:
    """清扫以前运行留下的孤儿测速文件（09-14 复盘：曾积出 17.8GB）。

    旧版本的清理只挂在父进程 finally 上，父进程被硬杀就留下孤儿，
    而且孤儿子进程还会把文件写满 —— 所以每次启动先扫一遍 Temp。
    文件被占用就跳过（可能正好有一轮在跑），不报错。
    """
    import glob
    for pattern in _STALE_GLOBS:
        for path in glob.glob(os.path.join(tempfile.gettempdir(), pattern)):
            try:
                os.remove(path)
                print("[清扫] 已删除陈旧自检文件 %s" % os.path.basename(path))
            except OSError:
                pass


def _terminate_real(pid: int) -> None:
    """按真实 PID 终止子进程。

    关键：Popen 返回的是 venv 启动器壳的 pid（壳随即退出），
    terminate 壳等于什么都没杀 —— 必须用子进程自己写进 pid 文件的真实 PID。
    （09-14 复盘：这正是以前每次自检都留下 10GB 文件的根因之一。）
    """
    if pid <= 0:
        return
    try:
        psutil.Process(pid).terminate()
    except psutil.Error:
        pass


def run_selftest(rounds: int) -> int:
    """受控实验：一个高磁盘 I/O 子进程 + 一个纯挂机子进程，做对照。"""
    _sweep_stale()
    # 写盘时长只取"够观察"的量：轮数 × 采样间隔 + 余量。
    # 以前固定 120 秒，在 NVMe 上就是 ~10GB —— 纯浪费。
    seconds = max(rounds * int(config.BG_SAMPLE_INTERVAL_S) + 10, 20)
    out_file = os.path.join(tempfile.gettempdir(), "uw_probe_io.bin")
    busy_proc, busy_pid, busy_pidfile = _spawn_child(
        "_child_busy.py", [out_file, str(seconds), str(os.getpid())])
    idle_proc, idle_pid, idle_pidfile = _spawn_child(
        "_child_idle.py", [str(seconds), str(os.getpid())])
    print("对照子进程真实 PID：忙碌 = %s   挂机 = %s" % (busy_pid or "?", idle_pid or "?"))
    print("（忙碌进程持续写盘，上限 256MB / %d 秒；挂机进程只 sleep）\n" % seconds)
    try:
        history = run_probe(rounds, {busy_pid, idle_pid})
    finally:
        # 先按真实 PID 终止（壳的 pid 早就死了，terminate 它毫无作用）
        _terminate_real(busy_pid)
        _terminate_real(idle_pid)
        for p in (busy_proc, idle_proc):
            try:
                p.wait(timeout=5)
            except Exception:
                pass
        for f in (out_file, busy_pidfile, idle_pidfile):
            for _attempt in range(3):        # 子进程退出需要一点时间，重试几次
                try:
                    os.remove(f)
                    break
                except OSError:
                    time.sleep(0.4)

    busy_seq = history.get(busy_pid, [])
    idle_seq = history.get(idle_pid, [])
    print("=" * 68)
    print("受控实验结果")
    print("  高磁盘 I/O 子进程 active 序列 : %s" % busy_seq)
    print("  纯挂机   子进程 active 序列 : %s" % idle_seq)
    ok_busy = any(busy_seq)
    ok_idle = bool(idle_seq) and not any(idle_seq)
    print()
    print("  [%s] 忙碌进程被正确判为「活跃」" % ("√" if ok_busy else "x"))
    print("  [%s] 挂机进程被正确判为「不活跃」" % ("√" if ok_idle else "x"))
    print()
    if ok_busy and ok_idle:
        print("结论：当前阈值能区分「真在干活」和「只是挂着」—— 阈值合理，无需调整。")
        return 0
    print("结论：阈值需要调整，改 src\\config.py 里的 CPU_ACTIVE_PCT / IO_ACTIVE_BPS。")
    if not ok_busy:
        print("      忙碌进程没被判活跃 → 阈值太高，或该进程 I/O 未被计入。")
    if not ok_idle:
        print("      挂机进程被判活跃 → 阈值太低，会把「只是挂着」也算成在用。")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--pid", type=int, default=0)
    args = parser.parse_args()

    if args.selftest:
        return run_selftest(args.rounds)

    print("=" * 68)
    print(" 后台活跃判定探针 —— 观察你关心的软件挂后台时到底是「活跃」还是「只是挂着」")
    print(" 做法：把微信 / 浏览器 / 播放器切到后台，看它落在哪一栏；Ctrl+C 退出")
    print("=" * 68)
    try:
        run_probe(args.rounds, {args.pid} if args.pid else set())
    except KeyboardInterrupt:
        print("\n[已停止]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
