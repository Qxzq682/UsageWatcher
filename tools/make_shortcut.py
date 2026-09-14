r"""在桌面创建 / 删除快捷方式。

⚠ 必须在**沙箱外**运行（双击 建桌面快捷方式.bat），
   沙箱内写桌面 + 调用 Shell COM 会被安全策略直接杀掉。

做法要点：
  · 目标直接指向 pythonw.exe + 脚本路径，**不经过 .bat**：
    经 .bat 会闪一个控制台窗口，而且参数里带空格的路径容易出错。
  · 参数里含空格的路径必须加双引号（本项目根目录名带空格）。
  · 写完读回来校验，不只看"创建成功"的返回值。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SHORTCUTS = (
    ("软件使用时长监测.lnk", "src\\app.py", "", "开始后台采集使用时长"),
    ("软件使用时长监测·统计.lnk", "src\\app.py", "--gui", "打开统计窗口"),
)


def find_python() -> tuple:
    """返回 (python.exe, pythonw.exe)。"""
    candidates = [
        ROOT / ".venv" / "Scripts" / "python.exe",
        Path.home() / ".workbuddy" / "binaries" / "python" / "envs" / "default" / "Scripts" / "python.exe",
    ]
    for path in candidates:
        if path.exists():
            windowless = path.with_name("pythonw.exe")
            return path, (windowless if windowless.exists() else path)
    return Path(sys.executable), Path(sys.executable)


def create() -> int:
    import win32com.client

    python, pythonw = find_python()
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = Path.home() / "OneDrive" / "Desktop"
    script = str(ROOT / "src" / "app.py")

    shell = win32com.client.Dispatch("WScript.Shell")
    print("解释器：%s" % pythonw)
    print("桌面  ：%s" % desktop)
    print()
    for name, _rel, extra, description in SHORTCUTS:
        target = str(desktop / name)
        link = shell.CreateShortCut(target)
        link.TargetPath = str(pythonw)
        # 含空格的路径一定要加引号，否则会被当成两个参数
        link.Arguments = '"%s"%s' % (script, (" " + extra) if extra else "")
        link.WorkingDirectory = str(ROOT)
        link.Description = description
        link.IconLocation = "%s,0" % python
        link.Save()
        # 读回来核实（只信返回值等于没验证）
        check = shell.CreateShortCut(target)
        ok = Path(target).exists() and script in check.Arguments
        print("  [%s] %s" % ("√" if ok else "x", target))
        if ok:
            print("       目标：%s %s" % (check.TargetPath, check.Arguments))
    return 0


def remove() -> int:
    desktop = Path.home() / "Desktop"
    removed = 0
    for name, _rel, _extra, _desc in SHORTCUTS:
        path = desktop / name
        if path.exists():
            path.unlink()
            print("  已删除 %s" % path)
            removed += 1
    print("共删除 %d 个" % removed)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remove", action="store_true", help="删除桌面快捷方式")
    args = parser.parse_args()
    try:
        return remove() if args.remove else create()
    except Exception as exc:
        print("[x] 失败：%s" % exc)
        print("    如果是在沙箱里运行，请改为双击 建桌面快捷方式.bat（沙箱外执行）。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
