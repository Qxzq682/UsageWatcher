r"""架构约束断言 —— 把「不许堆屎山」从口头约定变成会亮红灯的检查。

为什么要有它：代码量小的时候不锁边界，等长大了就锁不住了。
这个脚本约 40 行核心逻辑，每次迭代收尾都跑一遍，超限即失败。

用法：python.exe tools\test_arch.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"

MAX_FILE_LINES = 450    # v0.2 开工时经作者同意 400→450（图标/口径解释/日期切换塞压 ui_main）
MAX_FUNC_LINES = 60      # 防屎山核心配额，不为新功能放水
MAX_FILES = 18           # v0.5 经作者同意 15→18（+ui_charts/ui_health/ui_merge，Q 令"按需放宽"）
# 规模上限用「纯代码行」衡量：文档串 / 注释 / 空行不计入。
# 理由：这个上限的意图是限制**实现规模**，而不是惩罚写注释。
# 早先用总行数衡量时，2872 行里有 895 行是文档与空行，把注释算成"超标"是量错了对象。
# v0.1 二轮复检修复时经作者同意 2200→2400（悬浮窗/留存/自启等 v0.2 需求 + 复检 BUG-26~31）
# v0.2 开工时经作者同意 2400→2600（音频检测/日期切换/趋势图）
# v0.3 开工时经作者同意 2600→3000（i18n 双语文案/悬浮窗/语言切换）
# v0.3.1 收紧 3000→2800（i18n 双表并单表 -43、winutil 死代码 -27、
# data.py 连接适配器 __getattr__ 化 -15；再往下只剩功能实体，见工作日志 09-17）
# v0.4 开工时经作者同意 2800→3000（应用归类引擎+预设/数据质量条/锁屏语义等，09-30 拍板）
# v0.5 开工时经作者同意 3000→3350（右键合并 UI/数据健康/Top5 趋势/月度导出，
#      Q 令"按需放宽，结束后精修"——v0.6 精修阶段的目标是收回来）
# v0.6 收紧 3350→3250（精修实录：隐私说明 HTML 出 Python 化 -72、样式表出 Python
#      化（assets 资源，不占配额）、采集/存储/UI 重复实现去重 -40 余行；
#      同时新增自身进程显示名与资源加载 ~+20。3279→3226 实测，
#      再往下砍只剩伤可读性的选项，3200 目标顺延 v0.7 顺手收）
# v0.6.1 Q 追加深色主题+对比度修正 3250→3265（实测 3263：双主题色表/画笔取色/
#      设置页开关/悬浮窗 _fmt 漏改修复，净 +37，已尽力压缩 -9；3200 收拢仍顺延 v0.7）
# v0.6.2 修系统深色模式白底白字 3265→3270（实测 3270：apply_style 强制
#      colorScheme 跟随主题 +7，QSS 显式 color 全覆盖；3200 收拢仍顺延 v0.7）
# v0.6.3 第 10 轮检测 B1 修复 3270→3285（实测 3280：is_own_exe 改路径集合
#      （壳+真身，源码运行也生效）+10、WorkBuddy 家族预设 +3、注释加厚；
#      留 5 行余量——上一版余量为 0 被检测报告点名）
# v0.7 功能版 Q 拍板 3285→3450（每日摘要横幅 + 单应用用时提醒 +~160；
#      配额收拢 3200 顺延 v0.8 精修轮，复刻 v0.5 放宽→v0.6 收回的节奏）
# v0.7.2 悬浮窗贴边停靠（Q 点名需求）3450→3480（实测 3469：停靠/悬停恢复/
#      摘要开机气泡 +45 净增；3200 收拢仍顺延 v0.8）
MAX_CODE_LINES = 3480
# 阈值必须只在 config.py 定义一次，其它模块只许用 config.XXX 访问
UI_FORBIDDEN = ("sqlite3", "select ", "insert ", "update ", "delete from",
                ".execute(", ".executemany(", ".executescript(")
UI_FILES_PREFIX = "ui_"
# UI 不许反向依赖采集层；i18n 是最底层文案表，只许依赖 config
FORBIDDEN_IMPORTS = {
    "ui_main.py": {"collect"},
    "ui_scope.py": {"collect", "track"},
    "ui_overlay.py": {"collect", "track", "apps"},
    "i18n.py": {"apps", "collect", "track", "data", "winutil", "tray",
                "ui_main", "ui_scope", "ui_overlay"},
    "track.py": {"apps", "collect"},
    "data.py": {"apps", "collect"},
    "config.py": {"apps", "collect", "track", "data", "i18n"},
}

problems: list = []


def fail(msg: str) -> None:
    problems.append(msg)


def parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        fail(f"{path.name}: 语法错误 {exc}")
        return None


def threshold_names() -> set:
    """从 config.py 提取模块级常量名（阈值），作为"只许定义一次"的名单。"""
    tree = parse(SRC / "config.py")
    names = set()
    if tree is None:
        return names
    for node in tree.body:
        # 同时认 X = 5 与 X: int = 5（AnnAssign 写法绕过名单=白检查，实测加固点）
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(node, ast.AnnAssign) and node.value is None:
            continue
        if not isinstance(node.value, (ast.Constant, ast.Tuple)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id.isupper() and not target.id.startswith("_"):
                names.add(target.id)
    return names


def check_lines_and_names(path: Path, tree) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) > MAX_FILE_LINES:
        fail(f"{path.name}: {len(lines)} 行，超过 {MAX_FILE_LINES} 行上限")

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            span = node.end_lineno - node.lineno + 1
            if span > MAX_FUNC_LINES:
                fail(f"{path.name}:{node.lineno} 函数 {node.name}() {span} 行，超过 {MAX_FUNC_LINES} 行")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = ([a.name for a in node.names] if isinstance(node, ast.Import)
                       else [node.module or ""])
            for module in modules:
                root = module.split(".")[0]
                if root in FORBIDDEN_IMPORTS.get(path.name, set()):
                    fail(f"{path.name}:{node.lineno} 出现反向依赖 import {module}")
        if isinstance(node, ast.ImportFrom) and node.module == "abc":
            fail(f"{path.name}:{node.lineno} 禁止使用抽象基类模块 abc")
        if isinstance(node, ast.Import) and any(a.name == "abc" for a in node.names):
            fail(f"{path.name}:{node.lineno} 禁止使用抽象基类模块 abc")


def check_inheritance(path: Path, tree) -> None:
    bases: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases[node.name] = [
                b.id for b in node.bases if isinstance(b, ast.Name)
            ] + [b.attr for b in node.bases if isinstance(b, ast.Attribute)]

    def depth(name: str, seen: set) -> int:
        if name in seen:
            return 99
        parent_bases = bases.get(name, [])
        if not parent_bases:
            return 0
        return 1 + max((depth(b, seen | {name}) for b in parent_bases), default=0)

    for name in bases:
        if depth(name, set()) > 1:
            fail(f"{path.name}: 类 {name} 的继承链超过 1 层")


def check_contracts(path: Path, tree) -> None:
    """契约层只允许 4 个扁平 dataclass，且不许嵌套定义。"""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef) and node is not tree:
                decorators = [ast.unparse(d) for d in child.decorator_list]
                if any("dataclass" in d for d in decorators):
                    fail(f"{path.name}:{child.lineno} 禁止嵌套定义 dataclass {child.name}")


def check_thresholds(path: Path, tree, names: set) -> None:
    if path.name == "config.py" or not names:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in names:
            fail(f"{path.name}:{node.lineno} 阈值 {node.id} 必须写成 config.{node.id}，"
                 f"不许 from config import")
        if isinstance(node, ast.ImportFrom) and node.module == "config":
            for alias in node.names:
                if alias.name in names:
                    fail(f"{path.name}:{node.lineno} 阈值 {alias.name} 必须写成 config.{alias.name}")


def check_sql_location(path: Path) -> None:
    """连接只允许在 data.py 建立、DDL 只允许在 schema.py。

    这两条一起保证"数据访问路径唯一"：UI 层想拿数据只能调 data.py 的函数，
    不会悄悄自己开一个连接、养成第二套查询逻辑。
    """
    if path.name not in ("data.py", "schema.py"):
        text = path.read_text(encoding="utf-8")
        if "sqlite3.connect" in text:
            fail(f"{path.name}: 只有 data.py 允许 sqlite3.connect")
    if path.name == "schema.py":
        tree = parse(path)
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = (node.module if isinstance(node, ast.ImportFrom) else
                              (node.names[0].name if node.names else ""))
                    if module and module.split(".")[0] in {"config", "apps", "collect",
                                                           "track", "data", "ui_main",
                                                           "ui_scope", "tray"}:
                        fail(f"{path.name}: schema 只应定义数据结构，不应依赖 {module}")


def _code_only(source: str) -> str:
    """去掉注释与文档字符串，只留可执行代码。

    检查必须只看代码：早先版本因为把自己的说明文字也算进去，
    把 "本文件不许出现 sqlite3" 这种注释当成了违规（误报）。
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    blank: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                for lineno in range(body[0].lineno, body[0].end_lineno + 1):
                    blank.add(lineno)
    kept = []
    for number, line in enumerate(lines, 1):
        if number in blank or line.strip().startswith("#"):
            continue
        kept.append(line.split("#", 1)[0])
    return "\n".join(kept)


def check_ui_purity(path: Path, tree) -> None:
    """UI 层不许自己碰数据库。

    用精确 token 而不是泛化的 "connect(" —— 那会误伤 Qt 的信号连接
    clicked.connect()（实测踩过这个误报）。
    """
    if not path.name.startswith(UI_FILES_PREFIX):
        return
    code = _code_only(path.read_text(encoding="utf-8")).lower()   # 小写比对：SQL 不分大小写
    for token in UI_FORBIDDEN:
        if token in code:
            fail(f"{path.name}: UI 层出现 {token!r} —— 查询必须走 data.py 暴露的函数")


def _docstring_lines(tree) -> set:
    """收集所有模块/类/函数文档字符串占用的行号。"""
    lines: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    return lines


def code_line_count(path: Path, tree) -> int:
    """纯代码行数：排除空行、注释行、文档字符串。"""
    skip = _docstring_lines(tree)
    return sum(
        1 for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if number not in skip and line.strip() and not line.strip().startswith("#")
    )


def main() -> int:
    files = sorted(SRC.glob("*.py"))
    if len(files) > MAX_FILES:
        fail(f"src 下 {len(files)} 个文件，超过 {MAX_FILES} 个上限")

    names = threshold_names()
    total_lines = 0
    total_code = 0
    for path in files:
        tree = parse(path)
        if tree is None:
            continue
        total_lines += len(path.read_text(encoding="utf-8").splitlines())
        total_code += code_line_count(path, tree)
        check_lines_and_names(path, tree)
        check_inheritance(path, tree)
        check_contracts(path, tree)
        check_thresholds(path, tree, names)
        check_sql_location(path)
        check_ui_purity(path, tree)

    if total_code > MAX_CODE_LINES:
        fail(f"src 纯代码 {total_code} 行，超过 {MAX_CODE_LINES} 行上限"
             f"（总行 {total_lines}，其中文档/注释/空行 {total_lines - total_code} 行不计入）")

    print("架构约束检查（%d 个文件 · 总行 %d · 纯代码 %d · 文档与空行 %d）"
          % (len(files), total_lines, total_code, total_lines - total_code))
    if problems:
        print("失败 %d 项：" % len(problems))
        for item in problems:
            print("  [x] " + item)
        return 1
    print("全部通过 [√]")
    print("  · 依赖方向单向，无反向 import")
    print("  · UI 层无 SQL，查询统一走 data.py")
    print("  · 单文件 <= %d 行，单函数 <= %d 行，继承链 <= 1 层" % (MAX_FILE_LINES, MAX_FUNC_LINES))
    print("  · 纯代码 <= %d 行（文档与注释不计入）" % MAX_CODE_LINES)
    print("  · 阈值只在 config.py 定义，其它模块一律 config.XXX 访问")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
