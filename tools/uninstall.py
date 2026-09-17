r"""UsageWatcher 卸载向导（v0.2 B3）。

双击 卸载.bat 运行。做四件事，每步都先说明再做：

  1. 停止监测程序 —— 先写 stop.flag 让守护进程优雅退出（不丢正在用的
     那一段），超时或剩余的统计窗口进程才 terminate（都是自家受控进程，
     不碰任何无关进程，不提权、不注入）。
  2. 删除开机自启 —— 复用 winutil.set_autostart(False)，只动
     HKCU\...\Run 里自己的那一项（R6），删完复查。
  3. 删除桌面快捷方式 —— 送回收站，不直接删。
  4. 删除数据目录 %APPDATA%\UsageWatcher —— 送回收站（可恢复）。
     失败（被占用等）会如实报告并给出手动删除指引（R8）。

程序文件本身（本项目目录 / 打包 dist）不在这里删 —— 让用户自己决定，
脚本绝不递归删除项目目录。

用法：uninstall.py [--yes]   --yes 跳过逐项确认（仍是先预览、一次确认）
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config          # noqa: E402
import winutil         # noqa: E402
import psutil          # noqa: E402

SHORTCUT_NAMES = ("软件使用时长监测.lnk", "软件使用时长监测·统计.lnk")

FO_DELETE = 3
FOF_ALLOWUNDO = 0x0040        # 送回收站而不是直接删
FOF_NOCONFIRMATION = 0x0010   # 不弹系统确认框（确认在向导里做）
FOF_SILENT = 0x0004


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def recycle(path: Path) -> bool:
    """单个路径送回收站（可恢复）。个人数据一律回收站，不走永久删除。
    实测 rc 可能非 0 而删除已生效 → 以"文件消失"为最终事实（R8：不许把
    成功报成失败吓用户，也不许把失败报成成功）。"""
    op = _SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = str(path)
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return (rc == 0 and not op.fAnyOperationsAborted) or not path.exists()


def desktop_dir() -> Path:
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = Path.home() / "OneDrive" / "Desktop"
    return desktop


def dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total / 1024 / 1024


def find_own_processes() -> list:
    """本项目相关进程：打包 exe，或源码解释器跑本项目 src\\app.py（绝对路径）。
    用完整脚本路径匹配而非裸 "app.py"，避免其它进程命令行恰好提到
    app.py 字样时误伤；本脚本自己是 uninstall.py，不会匹配到。"""
    marker = str(ROOT / "src" / "app.py").lower()
    own = []
    for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
        try:
            exe = (proc.info["exe"] or "").lower()
            cl = " ".join(proc.info["cmdline"] or []).lower()
            if os.path.basename(exe) == "usagewatcher.exe" or marker in cl:
                own.append(proc)
        except psutil.Error:
            continue
    return own


def stop_all() -> None:
    procs = find_own_processes()
    if not procs:
        print("· 没有发现正在运行的监测程序。")
        return
    for proc in procs:
        print("· 发现监测进程 pid=%d" % proc.pid)
    try:
        config.stop_flag_path().write_text("stop", encoding="utf-8")
        print("· 已写停止标志，等待守护进程优雅退出（最多 5 秒）…")
    except OSError as exc:
        print("[!] 写停止标志失败（稍后会强制结束）：%s" % exc)
    deadline = time.time() + 5.0
    while time.time() < deadline:
        alive = [p for p in procs if p.is_running()]
        if not alive:
            return
        procs = alive
        time.sleep(0.2)
    for proc in find_own_processes():   # 残留（统计窗口 / 超时者）直接结束
        try:
            proc.terminate()
            print("· 已结束残留进程 pid=%d" % proc.pid)
        except psutil.Error as exc:
            print("[!] 结束进程失败 pid=%d：%s" % (proc.pid, exc))
    time.sleep(0.5)


def confirm(text: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input("%s [y/N] " % text).strip().lower() in ("y", "yes", "是")
    except EOFError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="UsageWatcher 卸载向导")
    parser.add_argument("--yes", action="store_true", help="跳过逐项确认")
    args = parser.parse_args()

    data_dir = config.data_dir()
    shortcuts = [desktop_dir() / name for name in SHORTCUT_NAMES]
    has_autostart = winutil.autostart_enabled()
    existing = [p for p in shortcuts if p.exists()]

    print("=" * 62)
    print(" UsageWatcher 卸载向导")
    print("=" * 62)
    print("将执行：")
    print("  1. 停止监测程序（守护进程优雅退出 + 关统计窗口）")
    print("  2. 删除开机自启（HKCU Run 中自己的项）：%s" %
          ("已开启，将删除" if has_autostart else "本来就未开启"))
    print("  3. 删除桌面快捷方式（送回收站）：%s" %
          ("、".join(p.name for p in existing) or "未发现"))
    print("  4. 删除数据目录（送回收站，含全部历史记录）：")
    print("     %s（%.1f MB）" % (data_dir, dir_size_mb(data_dir)))
    print("程序文件本身不会被删除；数据送回收站，后悔可还原。")
    print("=" * 62)
    if not confirm("确认开始卸载？", args.yes):
        print("已取消，什么都没动。")
        return 1

    print("\n[1/4] 停止监测程序")
    stop_all()

    print("\n[2/4] 删除开机自启")
    if has_autostart:
        if winutil.set_autostart(False) and not winutil.autostart_enabled():
            print("· 自启项已删除并复查通过。")
        else:
            print("[x] 自启项删除失败（可能被安全软件拦截），"
                  "请到「隐私与设置」页取消勾选，或手动删除注册表 Run 项。")
    else:
        print("· 未开启自启，跳过。")

    print("\n[3/4] 删除桌面快捷方式")
    if not existing:
        print("· 未发现快捷方式，跳过。")
    for path in existing:
        if recycle(path) and not path.exists():
            print("· 已送回收站：%s" % path.name)
        else:
            print("[x] 删除失败：%s（可手动删除）" % path)

    print("\n[4/4] 删除数据目录")
    if not data_dir.exists():
        print("· 数据目录不存在（本来就没有），跳过。")
    elif recycle(data_dir) and not data_dir.exists():
        print("· 已送回收站：%s" % data_dir)
    else:
        print("[x] 数据目录删除失败（可能仍被占用）。请关闭统计窗口后手动删除：")
        print("    %s" % data_dir)

    print("\n卸载完成。监测程序已停止、记录已随数据目录进入回收站。")
    print("程序文件在本目录，不需要了可以直接删除整个文件夹。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        raise SystemExit(130)
