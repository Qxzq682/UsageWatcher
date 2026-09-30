"""安全红线名单与匹配器 —— audit.py 与 selftest.py 共用，避免同一份名单写两遍。

修订记录（都是被误报教出来的）：
· 早先用**子串**匹配，结果 "mss" 撞上了 "smss.exe"、把系统进程名当成了屏幕采集库。
  → 改成按**词边界**匹配（正则 \\b），只看完整标识符。
· 早先名单里有个裸的 "key"，撞上了 meta 表的 key 字段。
  → 泛化词一律不进名单，只留明确指向内容的词。
"""

from __future__ import annotations

import re
from pathlib import Path

# 出现即违规：网络能力
NETWORK_TOKENS = (
    "socket", "urllib", "urllib3", "http", "requests", "ftplib", "smtplib",
    "httpx", "aiohttp", "websocket", "paramiko", "telnetlib",
)

# 出现即违规：内容型数据采集能力
CONTENT_TOKENS = (
    "GetWindowText", "GetWindowTextW", "WinHttp", "win32clipboard",
    "pyperclip", "ImageGrab", "pynput", "keyboard", "mss", "sounddevice",
    "pyaudio", "grab", "read_clipboard",
)

FORBIDDEN_TOKENS = NETWORK_TOKENS + CONTENT_TOKENS

# 数据库里不该出现的字段名（只留明确指向内容的，泛化词不列）
FORBIDDEN_COLUMNS = (
    "title", "url", "content", "clipboard", "screenshot",
    "keystroke", "keylog", "history", "password", "page_title",
)

# 常见网络库（用于核对依赖清单）
NETWORK_LIBS = ("requests", "urllib3", "httpx", "aiohttp", "websocket", "paramiko")

_PATTERN = re.compile(r"\b(%s)\b" % "|".join(re.escape(t) for t in FORBIDDEN_TOKENS))


def scan_source(folder: Path) -> list:
    """扫描目录下的 .py，返回 [(文件名, 行号, 命中词, 该行内容)]。

    只扫代码：注释与文档字符串里的说明文字不算 —— 我们的注释里本来就会
    写到"绝不调用 GetWindowText"这类话，那是说明不是调用。
    （v0.4 修订：旧版用"单行引号奇偶"判断字符串，对**多行 docstring 中间行**
    失效——中间行没有引号，docstring 里写 "socket 收发" 被误报成网络调用。
    换成字符级状态机，跨行三引号 / 转义 / 行注释一次处理对。）
    """
    hits: list = []
    for path in sorted(Path(folder).glob("*.py")):
        source = path.read_text(encoding="utf-8")
        in_triple = None                 # 跨行三引号状态：None 或 '"""' / "'''"
        for number, line in enumerate(source.splitlines(), 1):
            code, in_triple = _code_only(line, in_triple)
            if not code.strip():
                continue
            for match in _PATTERN.finditer(code):
                hits.append((path.name, number, match.group(1), line.strip()))
    return hits


def _code_only(line: str, in_triple):
    """剥离字符串字面量与注释，返回 (剩余代码, 新的跨行三引号状态)。

    状态机逐字符处理：三引号（含跨行）、单双引号、反斜杠转义、# 行注释。
    f-string 内嵌表达式会被整体当字符串剥掉 → 只可能漏报不会误报（安全方向）。"""
    out: list = []
    quote = None                         # 当前行内单/双引号状态
    i = 0
    n = len(line)
    while i < n:
        if in_triple:
            if line.startswith(in_triple, i):
                in_triple = None
                i += 3
            else:
                i += 1
            continue
        if quote:
            ch = line[i]
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        triple = line[i:i + 3]
        if triple in ('"""', "'''"):
            in_triple = triple
            i += 3
            continue
        ch = line[i]
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "#":
            break
        out.append(ch)
        i += 1
    return "".join(out), in_triple
    return False


def is_forbidden_column(column: str) -> bool:
    return column.strip().lower() in FORBIDDEN_COLUMNS
