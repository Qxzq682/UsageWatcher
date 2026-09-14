"""受控实验用：持续写盘的高 I/O 子进程（仅用于阈值标定，不属于交付功能）。

用法：python.exe _child_busy.py <pid文件> <输出文件> <持续秒数> <父进程pid> [体积上限MB]

四重防线（09-14 复盘后加，起因是 Temp 里留下过 10GB 孤儿文件）：
1. 体积上限：默认最多写 256MB，写满即停 —— 磁盘再快也不会写出 GB 级文件；
2. 时间上限：原有的持续秒数；
3. 父进程存活性检查：每写一块就看一眼父进程还在不在，父进程死了立刻收工
   （以前父进程被硬杀后，孤儿子进程会把全部时长写满）；
4. 自清理：无论因为哪种原因停止，退出前自己删掉输出文件 ——
   就算父进程的 finally 被跳过，也不会留下孤儿文件。
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
    pid_file, out_file, seconds = sys.argv[1], sys.argv[2], int(sys.argv[3])
    parent_pid = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    max_mb = int(sys.argv[5]) if len(sys.argv) > 5 else 256

    with open(pid_file, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))

    blob = b"x" * (8 * 1024 * 1024)
    max_blocks = max_mb * 1024 * 1024 // len(blob)
    end = time.time() + seconds
    blocks = 0
    reason = "time"
    # 限速：把总量摊满整个时长（例如 256MB / 25s ≈ 10MB/s）。
    # 不限速的话 256MB 一两秒就写完，采样窗口里根本观测不到它。
    pace = max(seconds / (max_blocks + 1), 0.0)
    try:
        with open(out_file, "wb") as fh:
            while time.time() < end:
                if blocks >= max_blocks:
                    reason = "size-cap"
                    break
                if not parent_alive(parent_pid):
                    reason = "parent-gone"
                    break
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
                blocks += 1
                time.sleep(min(pace, max(end - time.time(), 0)))
    finally:
        # 自清理：文件句柄已关，删得掉。父进程那边也会删，谁先谁后都无妨。
        try:
            os.remove(out_file)
        except OSError:
            pass
    print("busy child wrote %d blocks (%s)" % (blocks, reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
