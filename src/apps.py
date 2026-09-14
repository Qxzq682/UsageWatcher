"""应用身份与可监测性判定。零 Qt 依赖。

本模块是**外部不可信数据的唯一边界**：
exe 路径、产品名、发布者都来自别人的文件元数据，一律按不可信输入处理（R3）。

分层：collect → apps → track → data → ui_*
"""

from __future__ import annotations

import os
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
        self._pid_key: dict = {}     # pid -> app_key
        self._fails: dict = {}       # app_key -> 连续失败次数

    def resolve(self, pid: int, fallback_name: str = "") -> AppInfo:
        cached_key = self._pid_key.get(pid)
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

        self._pid_key[pid] = key
        return info

    def fail_count(self, app_key: str) -> int:
        return self._fails.get(app_key, 0)

    def forget_pid(self, pid: int) -> None:
        self._pid_key.pop(pid, None)
