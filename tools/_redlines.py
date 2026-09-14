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
    """
    hits: list = []
    for path in sorted(Path(folder).glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for number, line in enumerate(source.splitlines(), 1):
            code = line.split("#", 1)[0]
            if not code.strip():
                continue
            for match in _PATTERN.finditer(code):
                token = match.group(1)
                # 允许出现在字符串字面量之外的判断太复杂，这里只排除"仅出现在字符串里"的误报：
                if _only_in_string(code, token):
                    continue
                hits.append((path.name, number, token, line.strip()))
    return hits


def _only_in_string(code: str, token: str) -> bool:
    """判断这个命中是不是落在引号里（比如系统进程名 "smss.exe"）。

    简单可靠的依据：命中位置的引号数量为奇数 → 处在字符串内部。
    """
    index = code.find(token)
    while index != -1:
        if code[:index].count('"') % 2 == 1 or code[:index].count("'") % 2 == 1:
            return True
        index = code.find(token, index + 1)
    return False


def is_forbidden_column(column: str) -> bool:
    return column.strip().lower() in FORBIDDEN_COLUMNS
