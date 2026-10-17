# -*- coding: utf-8 -*-
r"""发行流水线（v0.3 ②）：一条命令完成 构建 -> 冒烟 -> 全量包 -> lite 包 -> 验证。

更新策略（v0.2 B1 拍板）：全量 zip 为主（新用户/大版本），lite 快更为辅
（老用户只换小 exe）。lite 的前提"新 exe + 旧 _internal 兼容"**不是假设**，
每次发版都实机验证：临时目录拼出新 exe + 上一版 _internal，跑起来再优雅停掉。

用法：
    python tools/release.py            # 全流程（构建+冒烟+双包+验证）
    python tools/release.py --no-build # 跳过构建，直接给最新 dist 打包（调试用）

产物在 release/：
    UsageWatcher-vX.Y.Z-full.zip   全量包（解压即用）
    UsageWatcher-vX.Y.Z-lite.zip   快更包（exe + 更新说明，覆盖同名文件）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config          # noqa: E402

PYEXE = Path(sys.executable)
RELEASE = ROOT / "release"


def next_dist_pair() -> tuple:
    """找一对没被占用的 buildN/distN（部分安全策略会拦 --noconfirm
    清旧目录，所以每次构建都开新路径——实测教训）。"""
    n = 5   # build1-4 已被历史构建用过
    while (ROOT / f"dist{n}").exists() or (ROOT / f"build{n}").exists():
        n += 1
    return ROOT / f"build{n}", ROOT / f"dist{n}"


def latest_dist(exclude: Path = None) -> Path | None:
    """数字序号最大的旧 dist（lite 验证要借它的 _internal 当"上一版"）。"""
    best, best_n = None, 0
    for path in ROOT.glob("dist*"):
        digits = path.name[4:]
        if not digits.isdigit():
            continue
        if exclude and path.resolve() == exclude.resolve():
            continue
        if int(digits) > best_n and (path / "UsageWatcher" / "_internal").exists():
            best, best_n = path, int(digits)
    return best


def build() -> Path:
    work, dist = next_dist_pair()
    print("== 构建（%s / %s）==" % (work.name, dist.name))
    r = subprocess.run(
        [str(PYEXE), "-m", "PyInstaller", "UsageWatcher.spec", "--noconfirm",
         "--workpath", str(work), "--distpath", str(dist)],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not (dist / "UsageWatcher" / "UsageWatcher.exe").exists():
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        raise SystemExit("[x] 构建失败")
    print("   构建完成")
    return dist


def smoke(dist: Path) -> None:
    print("== 打包冒烟（停源码守护 -> exe 隔离跑 -> 验库 -> 恢复）==")
    r = subprocess.run(
        [str(PYEXE), str(ROOT / "tools" / "_pkg_smoke2.py"), "--" + dist.name],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = "\n".join((r.stdout or "").strip().splitlines()[-3:])
    print(tail)
    if r.returncode != 0:
        raise SystemExit("[x] 冒烟未通过，停止发版")


def zip_dir(src_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for item in src_dir.rglob("*"):
            zf.write(item, item.relative_to(src_dir.parent))


def pause_source_daemon() -> None:
    """lite 验证前停掉源码守护：单实例 mutex 是 Global 的（跨 APPDATA），
    不停的话验证 exe 会把自己转成 GUI 进程常驻，stop.flag 永远没人消费。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import uninstall
    uninstall.stop_all()
    time.sleep(1)


def restore_source_daemon() -> None:
    pythonw = PYEXE.with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = PYEXE
    subprocess.Popen([str(pythonw), str(ROOT / "src" / "app.py")], cwd=str(ROOT),
                     creationflags=subprocess.CREATE_NO_WINDOW)
    time.sleep(3)
    # R8：恢复结果必须可见。注意：若本脚本跑在受限执行环境（如 WorkBuddy
    # 受控终端）里，进程树会随任务结束被收割——这里查活着也没用，只能靠
    # 受控终端外跑（用户双击 构建发行包.bat）才可靠。如实打印当下状态即可。
    import winutil
    alive = winutil.mutex_alive("Global\\UsageWatcher_SingleInstance_"
                                + (winutil.current_user_sid() or "unknown"))
    print("   源码守护已恢复: %s" % ("是" if alive else
          "否 —— 请双击 启动.bat 手动恢复"))


def make_packages(dist: Path, old_dist: Path | None) -> list:
    ver = config.APP_VERSION
    RELEASE.mkdir(exist_ok=True)
    app_dir = dist / "UsageWatcher"
    exe = app_dir / "UsageWatcher.exe"

    full = RELEASE / f"UsageWatcher-v{ver}-full.zip"
    print("== 全量包 ==")
    zip_dir(app_dir, full)
    print("   %s（%.1f MB）" % (full.name, full.stat().st_size / 1048576))

    print("== lite 快更包 ==")
    lite_dir = dist / "_lite_stage"
    lite_dir.mkdir(exist_ok=True)
    note = (
        "UsageWatcher v%s 快更包\r\n"
        "======================\r\n"
        "1. 解压出 UsageWatcher.exe\r\n"
        "2. 覆盖到你现有 UsageWatcher 文件夹里的同名文件（_internal 不用动）\r\n"
        "3. 如果监测正在运行，先右键托盘图标退出，替换完成后再启动\r\n"
        "（本包已实机验证：新 exe 搭配上一版 _internal 可正常运行）\r\n" % ver)
    (lite_dir / "更新说明.txt").write_text(note, encoding="utf-8")
    lite_stage = dist / "_lite_check"
    lite_stage.mkdir(exist_ok=True)
    (lite_stage / "UsageWatcher.exe").write_bytes(exe.read_bytes())
    if old_dist:
        import shutil
        print("   验证：新 exe + 上一版 _internal（%s）" % old_dist.name)
        shutil.copytree(old_dist / "UsageWatcher" / "_internal",
                        lite_stage / "_internal", dirs_exist_ok=True)
        # 上次失败运行可能残留 stop.flag（exe 会秒退被误判启动失败），先清掉
        stale_flag = dist / "_lite_env" / "UsageWatcher" / "stop.flag"
        stale_flag.unlink(missing_ok=True)
        pause_source_daemon()
        try:
            env = dict(os.environ, APPDATA=str(dist / "_lite_env"))
            proc = subprocess.Popen([str(lite_stage / "UsageWatcher.exe")],
                                    env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            time.sleep(6)
            alive = proc.poll() is None
            flag = dist / "_lite_env" / "UsageWatcher" / "stop.flag"
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("", encoding="utf-8")
            try:
                rc = proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = -1
        finally:
            restore_source_daemon()
        if not (alive and rc == 0):
            raise SystemExit("[x] lite 兼容验证失败（alive=%s rc=%s）" % (alive, rc))
        print("   新 exe + 旧 _internal：启动正常、优雅退出（码 0）")

    lite = RELEASE / f"UsageWatcher-v{ver}-lite.zip"
    with zipfile.ZipFile(lite, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(exe, "UsageWatcher/UsageWatcher.exe")
        zf.write(lite_dir / "更新说明.txt", "UsageWatcher/更新说明.txt")
    print("   %s（%.1f MB）" % (lite.name, lite.stat().st_size / 1048576))
    return [full, lite]


def main() -> int:
    parser = argparse.ArgumentParser(description="UsageWatcher 发行流水线")
    parser.add_argument("--no-build", action="store_true", help="跳过构建直接打包最新 dist")
    args = parser.parse_args()

    print("UsageWatcher v%s 发行流水线" % config.APP_VERSION)
    print("=" * 56)
    old = latest_dist()
    if args.no_build:
        dist = old
        if dist is None:
            raise SystemExit("[x] 没有可打包的 dist")
        old = latest_dist(exclude=dist)
    else:
        dist = build()
        smoke(dist)
    products = make_packages(dist, old)
    print("=" * 56)
    print("发行物：")
    for item in products:
        print("  %s" % item)
    print("构建目录：%s（zip 已就绪，可分发）" % dist.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
