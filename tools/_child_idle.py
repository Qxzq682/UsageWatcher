"""受控实验用：纯挂机子进程（仅用于阈值标定，不属于交付功能）。

用法：python.exe _child_idle.py <pid文件> <持续秒数> [父进程pid]

带父进程存活性检查：父进程死了就提前退出（和 _child_busy.py 同一套防线），
不留孤儿进程。
"""

import os
import sys
import time

import psutil


def parent_alive(parent_pid: int) -> bool:
    if parent_pid <= 0:
        return True
    try:
        return psutil.pid_exists(parent_pid)
    except psutil.Error:
        return False


def main() -> int:
    pid_file, seconds = sys.argv[1], int(sys.argv[2])
    parent_pid = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    with open(pid_file, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    end = time.time() + seconds
    while time.time() < end:
        if not parent_alive(parent_pid):
            break
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
