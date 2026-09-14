r"""请求守护进程优雅停止（给 停止.bat 调用）。

做法：写一个停止标志文件，守护进程自己发现后走正常收尾流程
（闭合在开片段 → 写库 → 退出）。不用强杀，也就不会丢掉"用户正在用的那一段"。

这是「零 IPC」设计下的通道：不开端口、不建命名管道，只放一个文件。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import config          # noqa: E402
from bench_mem import find_daemon    # noqa: E402


def main() -> int:
    proc = find_daemon()
    if proc is None:
        print("当前没有在运行的监测程序。")
        return 0

    print("正在请求优雅停止（PID %d）…" % proc.pid)
    try:
        config.stop_flag_path().write_text("stop", encoding="utf-8")
    except OSError as exc:
        print("写停止标志失败：%s" % exc)
        return 1

    for _ in range(25):        # 最多等 5 秒
        time.sleep(0.2)
        if not proc.is_running():
            print("已优雅退出，数据已写入数据库。")
            return 0

    print("[!] 优雅退出超时，改为强制结束 —— 最后未闭合的片段可能丢失。")
    proc.terminate()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
