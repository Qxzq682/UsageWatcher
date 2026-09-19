# -*- coding: utf-8 -*-
"""打包版（noconsole 修复版）守护进程冒烟，查完即删。

流程：停源码守护进程 → exe 隔离 APPDATA 跑 12 秒 → 正确路径写 stop.flag →
等优雅退出 → 查库（app/segment 应 >0）→ 重启源码守护进程。
"""
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 打包目录可参数化（--dist3 / --dist2 / 任意 dist 目录名），默认 dist3（D1 瘦身版）
_args = sys.argv[1:]
_dist = "dist3"
if "--dist2" in _args:
    _dist = "dist2"
elif "--dist3" in _args:
    _dist = "dist3"
elif any(a.startswith("--dist") for a in _args):
    _dist = next(a[2:] for a in _args if a.startswith("--dist"))
EXE = ROOT / _dist / "UsageWatcher" / "UsageWatcher.exe"
SANDBOX = ROOT / "_pkg_smoke_env9"      # 全新目录：全程零删除操作（本轮删除被沙箱粘性拦截）
FLAG = SANDBOX / "UsageWatcher" / "stop.flag"
DB = SANDBOX / "UsageWatcher" / "usage.db"

print("== 1. 停源码守护进程 ==")
real_flag = Path(os.environ["APPDATA"]) / "UsageWatcher" / "stop.flag"
real_flag.write_text("", encoding="utf-8")   # 写入即停；残留 flag 由 app.py 启动时自己清（app.py:84）
for _ in range(12):
    out = subprocess.run(["tasklist", "/fi", "IMAGENAME eq pythonw.exe"],
                         capture_output=True, text=True, encoding="gbk", errors="replace")
    if "pythonw.exe" not in (out.stdout or ""):
        print("   已退出")
        break
    time.sleep(1)

print("== 2. exe 隔离跑 12 秒 ==")


def _nudge_input() -> None:
    """注入一次 1 像素鼠标移动（真实输入事件），解除系统空闲状态。

    BUG-32 空闲截断（无输入 5 分钟闩上、轮询不产段）会让"深夜/无人时
    发版"的冒烟必挂 seg=0——冒烟前提不该是"人恰好坐在电脑前"。
    SendInput 才算真实输入（SetCursorPos 不更新 GetLastInputInfo）。
    """
    import ctypes

    PUL = ctypes.POINTER(ctypes.c_ulong)

    class _KI(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ulong), ("wScan", ctypes.c_ulong),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", PUL)]

    class _MI(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", _KI), ("mi", _MI)]

        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

    inp = _INPUT(type=0)                      # INPUT_MOUSE
    inp.mi = _MI(1, 1, 0, 0x0001, 0, None)    # MOUSEEVENTF_MOVE，位移 1 像素
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


_nudge_input()
env = dict(os.environ, APPDATA=str(SANDBOX))
proc = subprocess.Popen([str(EXE)], env=env, creationflags=subprocess.CREATE_NO_WINDOW)
print("   pid =", proc.pid)
time.sleep(12)

print("== 3. 优雅停（正确 stop 路径）==")
FLAG.parent.mkdir(parents=True, exist_ok=True)
FLAG.write_text("", encoding="utf-8")
try:
    rc = proc.wait(timeout=20)
    print("   退出码 =", rc)
except subprocess.TimeoutExpired:
    proc.kill()
    print("   [x] 20 秒未退出，强杀")

print("== 4. 查库 ==")
n_app = n_seg = 0
if DB.exists():
    con = sqlite3.connect(str(DB))
    n_app = con.execute("SELECT COUNT(*) FROM app").fetchone()[0]
    n_seg = con.execute("SELECT COUNT(*) FROM segment").fetchone()[0]
    con.close()
print("   app=%d segment=%d" % (n_app, n_seg))

print("== 5. 重启源码守护进程 ==")
pythonw = r"C:\Users\Q\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
subprocess.Popen([pythonw, str(ROOT / "src" / "app.py")], cwd=str(ROOT),
                 creationflags=subprocess.CREATE_NO_WINDOW)
time.sleep(3)
out = subprocess.run(["tasklist", "/fi", "IMAGENAME eq pythonw.exe"],
                     capture_output=True, text=True, encoding="gbk", errors="replace")
alive = "pythonw.exe" in (out.stdout or "")
print("   守护进程回来了:", alive)

passed = alive and n_app > 0 and n_seg > 0
print("\n结果:", "通过" if passed else "失败", "(app=%d seg=%d alive=%s)" % (n_app, n_seg, alive))
if alive and n_app > 0 and n_seg == 0:
    print("   [!] 零片段但进程正常——若刚注入的鼠标移动未生效（空闲截断仍闩上），")
    print("       请在电脑前活动后重跑；若反复出现则可能是采集回归，需排查。")
sys.exit(0 if passed else 1)
