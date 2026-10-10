"""应用身份与可监测性判定。零 Qt 依赖。

本模块是**外部不可信数据的唯一边界**：
exe 路径、产品名、发布者都来自别人的文件元数据，一律按不可信输入处理（R3）。

分层：collect → apps → track → data → ui_*
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Optional

import config
import winutil

# ---------------------------------------------------------------- 契约


@dataclass
class AppInfo:
    """一个应用（按 exe 维度）。扁平结构，不嵌套。"""

    app_key: str          # 小写全路径；读不到路径时用 'name:<exe名>'
    exe_path: str         # "" 表示系统拒绝读取
    exe_name: str         # 文件基名，如 WeChat.exe
    display_name: str     # 产品名，如 微信
    version: str
    publisher: str


@dataclass
class Capability:
    """可监测性。level='no' 时 UI 必须锁死且不可被任何配置绕过。"""

    app_key: str
    level: str            # 'yes' | 'partial' | 'no'
    reason: str           # 中文原因，直接显示给用户
    category: str         # third_party | windows | system | suggest_exclude
    enabled: bool         # 建议默认值；最终以用户选择为准（level='no' 恒为 False）


# ---------------------------------------------------------------- 不可信输入处理

_MAX_TEXT = 200


def sanitize(text: Optional[str]) -> str:
    """清洗外部字符串（R3）：把控制字符换成空格 + 折叠空白 + 截断。

    这些字符串会进数据库、进 UI、导出到 CSV，不能原样信任。
    注意是"换成空格"而不是"删掉"：删掉会让 'line1\\nline2' 粘连成 'line1line2'。
    """
    if not text:
        return ""
    spaced = "".join(" " if ch < " " else ch for ch in str(text))
    return " ".join(spaced.split())[:_MAX_TEXT]


# ---------------------------------------------------------------- 分类常量

# 系统与 SYSTEM 服务：实测这些在普通权限下 OpenProcess 必被拒
_SYSTEM_EXES = {
    "system", "system idle process", "registry", "memory compression", "secure system",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "lsaiso.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe", "spoolsv.exe",
    "searchindexer.exe", "securityhealthservice.exe", "wudfhost.exe", "wmiprvse.exe",
    "dashost.exe", "aggregatorhost.exe", "dllhost.exe", "sihost.exe",
    "taskhostw.exe", "ctfmon.exe", "conhost.exe", "runtimebroker.exe", "wlanext.exe",
    "unsecapp.exe", "audiodg.exe", "msmpeng.exe", "nissrv.exe", "sgrmbroker.exe",
}

# 浏览器：能统计窗口总时长，但无法区分标签页/网页
_BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "vivaldi.exe", "360se.exe", "360chrome.exe", "qqbrowser.exe", "sogouexplorer.exe",
    "iexplore.exe",
}

# 多进程应用：后台时长必须按时间轴并集合并，否则严重虚高
_MULTIPROCESS_EXES = {
    "chrome.exe", "msedge.exe", "code.exe", "code - insiders.exe", "discord.exe",
    "slack.exe", "teams.exe", "ms-teams.exe", "wechat.exe", "qq.exe", "telegram.exe",
    "electron.exe", "firefox.exe", "brave.exe", "steamwebhelper.exe", "obs64.exe",
}

# 建议排除：不是不能测，是"测了没意义或有害"
_SUGGEST_EXCLUDE_EXES = {
    "thunder.exe", "thunderplatform.exe", "idman.exe", "onedrive.exe", "baidunetdisk.exe",
    "syncthing.exe", "qbittorrent.exe", "utorrent.exe", "aria2c.exe", "motrix.exe",
    "everything.exe", "wallpaper32.exe", "wallpaper64.exe", "sogouinput.exe",
    "armourycrate.usersessionhelper.exe", "armourycrate.service.exe",
}

# UWP / 商店应用宿主
_UWP_HOST_EXES = {"applicationframehost.exe", "wwahost.exe"}

# 微软自带区（路径判定，主判据）
_SYSTEM_ROOT = os.environ.get("SystemRoot", r"C:\Windows").lower()
_PROGRAM_FILES = os.environ.get("ProgramFiles", r"C:\Program Files").lower()
_PROGRAM_FILES_X86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)").lower()

_WINDOWS_PREFIXES = (
    _SYSTEM_ROOT + "\\",
    os.path.join(_PROGRAM_FILES, "windowsapps").lower() + "\\",
    os.path.join(_PROGRAM_FILES, "windows nt").lower() + "\\",
    os.path.join(_PROGRAM_FILES, "windows defender").lower(),
    os.path.join(_PROGRAM_FILES, "internet explorer").lower(),
    os.path.join(_PROGRAM_FILES_X86, "windows nt").lower() + "\\",
)


def _looks_windows_owned(path_lower: str) -> bool:
    return any(path_lower.startswith(p) for p in _WINDOWS_PREFIXES)


# ---------------------------------------------------------------- 归类归并（v0.4）

# 通用结构目录名：爬根时跳过 —— 它们是工程结构，不是应用名。
# 黑名单宁缺勿滥：漏一个只会"少归并"（无害），错一个可能"错归并"。
_GENERIC_DIRS = {
    "bin", "bin64", "bin32", "win64", "win32", "x64", "x86", "release", "debug",
    "common", "runtime", "extracted", "plugins", "plugin", "thirdparty", "third_party",
    "third-party", "binaries", "binary", "cef", "locale", "locales", "resources",
    "resource", "static", "webcache", "cache", "data", "libs", "lib", "dll", "drivers",
    "game", "games", "client", "server", "launcher", "update", "updates", "installer",
    "install", "redist", "crashreporter", "crashes", "logs", "config", "settings",
    "apps", "packages", "container", "files",
    "application", "office6",   # 真库冒烟：EdgeWebView\Application、WPS office6
    "content",                  # UE 引擎资源目录（selftest 补测暴露）
}
# 文件"存放地"而非"应用名"：出现在路径任一段就说明这条 exe 是被随手放的
# （如 D:\下载\xxx.exe），向上爬没有证据意义 —— 真库冒烟实证：D:\下载 下
# 两个无关安装包曾因此被误合。名单宁小勿滥，只收确证的存放目录。
_STORAGE_DIRS = {
    "下载", "downloads", "download", "文档", "documents", "桌面", "desktop",
    "图片", "pictures", "视频", "videos", "音乐", "music", "安装包",
    "新建文件夹", "new folder", "temp", "tmp",
}
_VERSION_RE = re.compile(r"^v?\d+([._-]\d+)*$")

# 内置预设（先于爬根，命中即归并）。模式一律小写：
#   ("path", 子串, 组名) = 路径含子串     ("exe", 基名, 组名) = exe 基名相等
# 跨树家族（微信本体与内嵌浏览器不在同一目录树）只能靠预设；
# 同树家族预设 + 爬根双保险。纯数据表，用户以后的"即席合并"（v0.5）走同一条路。
_MERGE_PRESETS = (
    ("path", "wuthering waves", "鸣潮"),
    ("path", "delta force", "三角洲行动"),
    ("path", "nvidia corporation", "NVIDIA"),
    ("path", "tencent\\xwechat", "微信"),
    ("exe", "weixin.exe", "微信"),
    ("exe", "wechatappex.exe", "微信"),
    ("exe", "wechat.exe", "微信"),
)


def _squash(name: str) -> str:
    """目录名规范化比较：去空格/连字符/下划线 + 小写（DeltaForce == Delta Force）。"""
    return name.lower().replace(" ", "").replace("-", "").replace("_", "")


def crawl_root(exe_path: str) -> str:
    """从 exe 路径向上爬，返回「应用根」路径（小写反斜杠串）；爬不出返回 ""。

    跳过通用结构目录与纯版本号目录，停在第一个"像应用名"的目录；
    根目录名与其父目录规范化同名时继续上浮（...\\Delta Force\\Delta Force\\DeltaForce
    → Delta Force）。纯函数零 I/O（R3：路径是不可信输入，爬错的最坏结果是
    "少归并"，不会把两个无关应用合成一个 —— 组只在多成员时才生效，见下）。
    """
    parts = [p for p in exe_path.lower().replace("/", "\\").split("\\") if p]
    if len(parts) < 3:                       # 至少 盘符\目录\file.exe 才谈得上爬根
        return ""
    joined = "\\".join(parts)
    if _looks_windows_owned(joined):
        return ""    # Windows 自有路径（system32/systemapps/windowsapps）不爬根
    if any(p.startswith(".") or p in _STORAGE_DIRS for p in parts[:-1]):
        return ""    # 点开头目录 / 文件存放地：exe 是工具链或随手放的，无归并证据
    k = len(parts) - 2                       # exe 的父目录
    while k >= 1 and (parts[k] in _GENERIC_DIRS or _VERSION_RE.match(parts[k])
                      or parts[k].isdigit()):
        k -= 1
    if k < 1:
        return ""
    while k - 1 >= 1 and _squash(parts[k - 1]) == _squash(parts[k]):
        k -= 1                               # 与父目录同名 → 上浮到更外层
    return "\\".join(parts[:k + 1])


def merge_key(exe_path: str, exe_name: str) -> str:
    """归类组键：预设命中返回组名（如"微信"），否则返回爬根路径键，都不中返回 ""。"""
    path_l = (exe_path or "").lower()
    name_l = (exe_name or "").lower()
    for kind, pattern, group in _MERGE_PRESETS:
        if (kind == "path" and pattern in path_l) or (kind == "exe" and name_l == pattern):
            return group
    return crawl_root(exe_path or "")


def build_merge_map(items: dict, overrides: Optional[dict] = None) -> dict:
    """{app_key: (exe_path, exe_name)} → {app_key: 组显示名}（纯函数，UI/查询共用）。

    优先级：用户即席规则（v0.5，overrides={exe路径小写: 组名或""}）>
    内置预设 > 爬根。预设组直接生效；爬根组只在**多于一个成员**时生效 ——
    单成员组不改名（否则 WorkBuddy 会被显示成目录名 "work buddy"，毫无意义
    还难看）。爬根组显示名取根目录原名（保留大小写）。
    用户规则值为 "" 表示"强制不归并"（把爬根/预设归并的成员拆出来）。"""
    by_key: dict = {}
    for app_key, (path, name) in items.items():
        key = merge_key(path, name)
        if key:
            by_key.setdefault(key, []).append(app_key)
    out: dict = {}
    for key, members in by_key.items():
        if "\\" in key:                       # 爬根键（含路径分隔）→ 需多成员
            if len(members) < 2:
                continue
            origin = items[members[0]][0] or key
            pos = origin.lower().find(key)
            display = origin[pos:pos + len(key)].rstrip("\\").split("\\")[-1] \
                if pos >= 0 else key.rsplit("\\", 1)[-1]
        else:                                 # 预设组名
            display = key
        for app_key in members:
            out[app_key] = display
    for app_key, (path, _name) in items.items():
        rule = (overrides or {}).get((path or "").lower())
        if rule is None:
            continue
        if rule:
            out[app_key] = rule               # 用户合并：覆盖预设与爬根
        else:
            out.pop(app_key, None)            # 用户拆组：从任何归并中移除
    return out


# ---------------------------------------------------------------- 判定逻辑


def classify(info: AppInfo) -> str:
    """归类：third_party / windows / system / suggest_exclude（顺序即优先级）。"""
    name = info.exe_name.lower()
    if name in _SUGGEST_EXCLUDE_EXES:
        return "suggest_exclude"
    if name in _SYSTEM_EXES or not info.exe_path:
        return "system"
    if _looks_windows_owned(info.exe_path.lower()):
        return "windows"
    return "third_party"


def capability_of(info: AppInfo, fail_count: int) -> Capability:
    """三态判定。

    `fail_count` 是"连续读路径失败次数"——连续达到阈值才定性 no，
    避免偶发的权限抖动把一个正常程序误判成不可监测（计划 §3）。
    """
    category = classify(info)
    name = info.exe_name.lower()

    if not info.exe_path and fail_count < config.UNMONITORABLE_FAIL_N:
        # 还没到定性门槛，先按"暂不可监测"处理但不落永久结论
        return Capability(info.app_key, "no", "暂时读不到该进程信息，仍在重试", category, False)

    if not info.exe_path:
        if name in _SYSTEM_EXES:
            reason = "系统进程 / 系统服务，Windows 拒绝访问（本工具不监测系统组件）"
        else:
            reason = "系统拒绝读取该进程信息（受保护进程或安全软件自保护）"
        return Capability(info.app_key, "no", reason, category, False)

    if name in _UWP_HOST_EXES:
        return Capability(
            info.app_key, "partial",
            "UWP 宿主进程，可能无法归因到具体商店应用", category, True,
        )
    if name in _BROWSER_EXES:
        return Capability(
            info.app_key, "partial",
            "能统计窗口总时长，但无法区分标签页 / 具体网页（同一进程）", category, True,
        )
    if name in _MULTIPROCESS_EXES:
        return Capability(
            info.app_key, "partial",
            "多进程应用，后台时长按时间轴合并计算（避免重复计数）", category, True,
        )
    if info.exe_path and "\\windowsapps\\" in info.exe_path.lower():
        return Capability(
            info.app_key, "partial",
            "微软商店（UWP）应用，归因依赖系统行为，可能不完全准确", category, True,
        )

    enabled = category == "third_party"
    reason = "" if enabled else "Windows 自带或建议排除（默认不监测，可手动勾选）"
    return Capability(info.app_key, "yes", reason, category, enabled)


# ---------------------------------------------------------------- 解析与缓存


class Resolver:
    """PID → AppInfo，带内存缓存（缓存只存元数据，不持有进程句柄）。"""

    def __init__(self) -> None:
        self._by_key: dict = {}      # app_key -> AppInfo
        self._pid_key: dict = {}     # pid -> (app_key, 记录时刻)；TTL 防 PID 复用张冠李戴
        self._fails: dict = {}       # app_key -> 连续失败次数

    def resolve(self, pid: int, fallback_name: str = "") -> AppInfo:
        cached = self._pid_key.get(pid)
        cached_key = cached[0] if cached and time.monotonic() - cached[1] < config.PID_KEY_TTL_S else None
        if cached_key and cached_key in self._by_key:
            return self._by_key[cached_key]

        path = winutil.process_image_path(pid)
        if path:
            key = path.lower()
            info = self._by_key.get(key)
            if info is None:
                raw = winutil.file_version_info(path)
                info = AppInfo(
                    app_key=key,
                    exe_path=sanitize(path),
                    exe_name=sanitize(os.path.basename(path)),
                    display_name=sanitize(raw.get("display_name")) or sanitize(os.path.basename(path)),
                    version=sanitize(raw.get("version")),
                    publisher=sanitize(raw.get("publisher")),
                )
                self._by_key[key] = info
            self._fails[key] = 0
        else:
            name = sanitize(fallback_name) or f"pid-{pid}"
            key = f"name:{name.lower()}"
            self._fails[key] = self._fails.get(key, 0) + 1
            info = self._by_key.get(key) or AppInfo(
                app_key=key, exe_path="", exe_name=name, display_name=name,
                version="", publisher="",
            )
            self._by_key[key] = info

        self._pid_key[pid] = (key, time.monotonic())
        return info

    def fail_count(self, app_key: str) -> int:
        return self._fails.get(app_key, 0)

    def forget_pid(self, pid: int) -> None:
        self._pid_key.pop(pid, None)
