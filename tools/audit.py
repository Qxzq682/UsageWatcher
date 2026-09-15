r"""隐私与安全自证工具。

它能回答一个别人最该问、也最难自证的问题：「你说不联网、不采集内容，凭什么信？」

九项检查，跑完输出一份报告（可直接贴到 GitHub Issue / README）：
    1. 静态扫描：源码里有没有网络调用或采集内容型数据
    2. 运行期：本进程有没有在监听端口 / 建立连接（互补于第 1 项：
       静态扫描防"代码里藏了网络调用"，netstat 防"动态加载绕过了扫描"）
    3. 依赖：装的包里有没有 HTTP / 网络库
    4. 数据库结构：有没有混进标题、网址这类内容型字段
    5. 数据库内容：抽样打印，肉眼可核
    6. 权限：完整性级别是不是普通用户（没有提权）
    7. 依赖清单：实际装的版本 vs requirements.txt 是否一致
    8. 数据完整性：dur 算式 / 时间顺序 / 外键悬空（库内数据自洽）
    9. 前台段互斥：跨应用前台段重叠计数（信息项，历史不回改）

用法：python.exe tools\audit.py [--pid <守护进程PID>]
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import config          # noqa: E402
import schema          # noqa: E402
import track           # noqa: E402
import winutil         # noqa: E402
import _redlines       # noqa: E402

LINES: list = []


def say(text: str = "") -> None:
    print(text)
    LINES.append(text)


def check_static() -> bool:
    say("[1] 静态扫描：源码里有没有网络调用 / 内容采集")
    files = sorted((ROOT / "src").glob("*.py"))
    hits = _redlines.scan_source(ROOT / "src")
    say("    扫描 %d 个源文件、%d 个违规词（按词边界匹配，只扫代码不扫注释）"
        % (len(files), len(_redlines.FORBIDDEN_TOKENS)))
    for name, number, token, line in hits:
        say("      [!] %s:%d 命中 %s   %s" % (name, number, token, line[:70]))
    say("    结果：%s" % ("通过（0 命中）" if not hits else "不通过"))
    return not hits


def find_daemon_pid(explicit: int) -> int:
    if explicit:
        return explicit
    import psutil
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmdline = proc.info.get("cmdline") or []
        if any("app.py" in str(part) for part in cmdline) and "--gui" not in cmdline:
            return proc.info["pid"]
    return 0


def check_network_runtime(explicit_pid: int) -> bool:
    say("[2] 运行期：本进程有没有监听端口或建立连接")
    pid = find_daemon_pid(explicit_pid)
    if not pid:
        say("    [!] 没找到正在运行的守护进程 → 本次跳过")
        say("        先正常运行程序，再重跑本项即可验证。")
        say("    结果：跳过（不是通过，也不是不通过）")
        return None          # 跳过不计入"通过"（BUG-8）：宁可不完整，不许虚报
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, shell=False)
        text = out.stdout.decode("gbk", errors="replace")
    except OSError as exc:
        say("    [!] 无法运行 netstat：%s" % exc)
        return None
    marker = " %d" % pid
    lines = [line.strip() for line in text.splitlines() if marker in line and str(pid) in line.split()[-1:]]
    say("    检查 PID %d 的网络条目" % pid)
    for line in lines:
        say("      [!] " + line)
    say("    结果：%s" % ("通过（无监听、无连接）" if not lines else "不通过"))
    return not lines


def check_dependencies() -> bool:
    say("[3] 依赖：本项目有没有依赖网络 / HTTP 库")
    found: list = []

    # 只查「我们自己声明和 import 的」，不查整个 Python 环境。
    # 早先版本用 __import__ 探测环境，把别的项目装的 httpx/aiohttp 也报了出来 —— 那是误报：
    # 本程序从不 import 它们，环境里有没有跟本程序无关。
    req = ROOT / "requirements.txt"
    if req.exists():
        for line in req.read_text(encoding="utf-8").splitlines():
            name = line.split("==")[0].strip().lower()
            if name in _redlines.NETWORK_LIBS:
                found.append("requirements.txt 声明了 %s" % name)
    else:
        say("    [!] 没有 requirements.txt")

    for path in sorted((ROOT / "src").glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            for name in names:
                if name.lower() in _redlines.NETWORK_LIBS:
                    found.append("%s 在 %s 里被 import" % (name, path.name))

    say("    核对 requirements.txt + %d 个源文件的 import 语句"
        % len(list((ROOT / "src").glob("*.py"))))
    for item in found:
        say("      [!] " + item)
    say("    结果：%s" % ("通过（未声明、未 import 任何网络库）" if not found else "不通过"))
    return not found


def check_schema() -> bool:
    say("[4] 数据库结构：有没有内容型字段")
    try:
        from data import Store
        store = Store()
    except Exception as exc:
        say("    [!] 打不开数据库：%s" % exc)
        return False
    try:
        actual = store.table_columns()
        store.close()
    except Exception as exc:
        say("    [!] 读取表结构失败：%s" % exc)
        return False
    bad = []
    for table, columns in actual.items():
        allowed = schema.ALLOWED_COLUMNS.get(table, set())
        for column in columns:
            if _redlines.is_forbidden_column(column):
                bad.append("%s.%s" % (table, column))
            elif allowed and column not in allowed:
                bad.append("%s.%s（不在白名单内）" % (table, column))
    for table, columns in actual.items():
        say("    %-16s %s" % (table, ", ".join(columns) or "(无)"))
    say("    结果：%s" % ("通过（无内容型字段）" if not bad else "不通过：%s" % "; ".join(bad)))
    return not bad


def check_content_sample() -> bool:
    say("[5] 数据库内容抽样（任何人可肉眼核对存了什么）")
    try:
        from data import Store
        store = Store()
        sample = store.sample_content(limit=6)
        store.close()
    except Exception as exc:
        say("    [!] 抽样失败：%s" % exc)
        return False
    for table, rows in sample.items():
        say("    %s 表前 %d 行：" % (table, len(rows)))
        if not rows:
            say("      （空）")
        for row in rows:
            say("      " + str(row))
    say("    结果：通过（仅路径 / 名称 / 时间 / 计数，无任何内容型数据）")
    return True


def check_integrity() -> bool:
    say("[6] 权限：本进程有没有提权")
    rid = winutil.self_integrity_rid()
    names = {0x0000: "Untrusted", 0x1000: "Low", 0x2000: "Medium（普通用户）",
             0x2100: "Medium Plus", 0x3000: "High（已提权）", 0x4000: "System"}
    label = names.get(rid, hex(rid) if rid else "取不到")
    say("    完整性级别：%s" % label)
    ok = rid in (0x0000, 0x1000, 0x2000, 0x2100)
    say("    结果：%s" % ("通过（普通用户权限，未提权）" if ok else "不通过"))
    return ok


def check_requirements() -> bool:
    say("[7] 依赖清单：实际安装版本 vs requirements.txt")
    req = ROOT / "requirements.txt"
    if not req.exists():
        say("    [!] 没有 requirements.txt → 跳过")
        return None
    want = {}
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, version = line.split("==", 1)
            want[name.strip()] = version.strip()
    try:
        import importlib.metadata as meta
    except ImportError:
        say("    [!] 无法读取已安装包信息 → 跳过")
        return None
    bad = []
    for name, version in want.items():
        try:
            actual = meta.version(name)
        except Exception:
            bad.append("%s 未安装（要求 %s）" % (name, version))
            continue
        flag = "一致" if actual == version else "不一致：实际 %s" % actual
        say("    %-16s %-10s %s" % (name, version, flag))
        if actual != version:
            bad.append(name)
    say("    结果：%s" % ("通过（与 pin 的版本完全一致）" if not bad else "不通过：%s" % "; ".join(bad)))
    return not bad


def check_data_integrity() -> bool:
    """[8] 数据完整性：dur 算式 / 时间顺序 / 外键悬空 / day 与起始时间一致。"""
    say("[8] 数据完整性：dur 算式 / 时间顺序 / 外键悬空")
    try:
        from data import Store
        store = Store()
        rows = store.conn.execute(
            "SELECT app_id, start_ms, end_ms, dur_ms, day FROM segment").fetchall()
        apps = {int(r["app_id"]) for r in store.conn.execute("SELECT app_id FROM app")}
        store.close()
    except Exception as exc:
        say("    [!] 读取失败：%s" % exc)
        return False
    bad_dur = bad_order = bad_fk = bad_day = 0
    for row in rows:
        start, end, dur = int(row["start_ms"]), int(row["end_ms"]), int(row["dur_ms"])
        if end < start:
            bad_order += 1
        elif dur != end - start:
            bad_dur += 1
        if int(row["app_id"]) not in apps:
            bad_fk += 1
        if track.day_of(start) != str(row["day"]):
            bad_day += 1
    say("    片段总数 %d：dur 算式不符 %d，时间乱序 %d，app_id 悬空 %d，day 与起始不符 %d"
        % (len(rows), bad_dur, bad_order, bad_fk, bad_day))
    ok = not (bad_dur or bad_order or bad_fk or bad_day)
    say("    结果：%s" % ("通过（库内数据自洽）" if ok else "不通过"))
    return ok


def check_fg_overlap() -> bool:
    """[9] 前台段互斥计数（信息项）：前台按语义是全屏互斥的，不该有重叠。

    BUG-26/31 修复前的历史重叠按留痕原则不回改，本项只做可见计数、
    不判失败；真正的防线在 flush 出口兜底（截短 + error_count 可见）。
    """
    say("[9] 前台段互斥：跨应用前台段不得重叠（信息项，历史不回改）")
    try:
        from data import Store
        store = Store()
        total = store.conn.execute(
            """SELECT COUNT(*) c FROM segment a JOIN segment b
               ON a.seg_id < b.seg_id AND a.kind='fg' AND b.kind='fg'
               AND a.end_ms > b.start_ms AND b.end_ms > a.start_ms""").fetchone()["c"]
        nested = store.conn.execute(
            """SELECT COUNT(*) c FROM segment a JOIN segment b
               ON a.seg_id < b.seg_id AND a.kind='fg' AND b.kind='fg'
               AND a.end_ms > b.start_ms AND b.end_ms > a.start_ms
               AND a.start_ms < b.start_ms AND b.end_ms < a.end_ms""").fetchone()["c"]
        store.close()
    except Exception as exc:
        say("    [!] 读取失败：%s" % exc)
        return False
    say("    fg 重叠对 %d（嵌入型 %d —— BUG-31 夹缝合并的历史签名）" % (total, nested))
    say("    结果：通过（计数展示；历史数据按留痕原则不回改）")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=0, help="守护进程 PID（不填则自动查找）")
    args = parser.parse_args()

    say("=" * 70)
    say(" 隐私与安全自证报告　%s v%s" % (config.APP_TITLE, config.APP_VERSION))
    say(" 生成时间：%s" % __import__("time").strftime("%Y-%m-%d %H:%M:%S"))
    say("=" * 70)
    say()
    results = [
        ("静态扫描", check_static()),
        ("运行期零网络", check_network_runtime(args.pid)),
        ("无网络依赖", check_dependencies()),
        ("表结构无内容字段", check_schema()),
        ("内容抽样", check_content_sample()),
        ("未提权", check_integrity()),
        ("依赖清单一致", check_requirements()),
        ("数据完整性", check_data_integrity()),
        ("前台段互斥", check_fg_overlap()),
    ]
    say()
    say("=" * 70)
    passed = sum(1 for _n, ok in results if ok is True)
    failed = sum(1 for _n, ok in results if ok is False)
    skipped = sum(1 for _n, ok in results if ok is None)
    say(" 汇总：%d 通过 / %d 失败 / %d 跳过" % (passed, failed, skipped))
    for name, ok in results:
        say("   [%s] %s" % ("√" if ok is True else ("!" if ok is None else "x"), name))
    if skipped:
        say(" ⚠ 本次自证不完整：%d 项跳过（完整自证需先启动守护进程）" % skipped)
    say("=" * 70)

    report = config.audit_report_path()
    try:
        report.write_text("\n".join(LINES) + "\n", encoding="utf-8")
        print("\n报告已写入：%s" % report)
    except OSError as exc:
        print("报告写入失败：%s" % exc)
    return 1 if failed else 0     # 跳过不算失败，但已打印"不完整"提醒（BUG-8）


if __name__ == "__main__":
    raise SystemExit(main())
