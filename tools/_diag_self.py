"""诊断：让进程「自己测量自己」的 CPU 与磁盘 I/O 计数。

目的：确认 Windows 上 GetProcessIoCounters（psutil.io_counters 的底层）
到底会不会统计到本进程的文件读写。结论直接决定「后台活跃」能不能靠 I/O 判定。

用法：python.exe _diag_self.py [持续秒数]
"""

import os
import sys
import tempfile
import time

import psutil


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    me = psutil.Process()
    path = os.path.join(tempfile.gettempdir(), "uw_self_io.bin")

    print("pid = %d" % os.getpid())
    print("测量方式：进程自己调用 psutil.Process().io_counters() / cpu_times()")
    print()
    print("%6s  %10s  %14s  %14s  %10s" % ("秒", "自身CPU(s)", "read_bytes", "write_bytes", "已写块数"))
    print("-" * 68)

    blob = b"x" * (8 * 1024 * 1024)
    end = time.time() + seconds
    blocks = 0
    start = time.time()
    with open(path, "wb") as fh:
        while time.time() < end:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
            blocks += 1
            if time.time() - start >= 1.0:
                ct = me.cpu_times()
                io = me.io_counters()
                print("%6.1f  %10.3f  %14d  %14d  %10d" % (
                    time.time() - start, ct.user + ct.system,
                    io.read_bytes, io.write_bytes, blocks))
                start = time.time()

    ct = me.cpu_times()
    io = me.io_counters()
    print("-" * 68)
    print("最终: CPU 累计 %.3fs / write_bytes %d / 实际写了 %d 块 (%.1f MB)" % (
        ct.user + ct.system, io.write_bytes, blocks, blocks * 8.0))
    print()
    print("判读：")
    print("  · write_bytes 明显增长  → I/O 判定可用 ✓")
    print("  · write_bytes 几乎不动  → GetProcessIoCounters 不统计缓冲写，I/O 判定不可靠 ✗")
    try:
        os.remove(path)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
