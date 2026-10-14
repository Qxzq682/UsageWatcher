"""右键即席合并（v0.5）：应用表格右键 → 合并到组 / 新建组 / 拆出组。

规则持久化在 config.json 的 merge_overrides：{exe路径小写: 组名或""}，
"" = 强制不归并。apps.build_merge_map 已实现覆盖语义（用户规则 > 预设 > 爬根），
本模块只负责 UI 交互与读写配置。分层：ui_* 末端，不许 SQL。"""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox

import config
import i18n


def _overrides() -> dict:
    ov = config.load().get("merge_overrides")
    return ov if isinstance(ov, dict) else {}


def _save(ov: dict) -> None:
    try:
        config.save({"merge_overrides": ov}, keys=("merge_overrides",))
    except OSError:
        QMessageBox.warning(None, i18n.t("set_fail_title"), i18n.t("cfg_save_fail"))


def members_of(row: dict, merge_map: dict) -> list:
    """一个统计行涉及的 app_key 列表：组行 = 组内全部成员，单应用 = 自己。

    判定用 app_key 精确匹配（行的 app_key 是组代表成员，必在 merge_map 里），
    不按显示名比较 —— 否则未归并应用的原始名恰与组名同名时会误认成组
    （外部检测报告 v0.5 D2）。"""
    group = merge_map.get(row["app_key"])
    if group and group == row["display_name"]:
        return [key for key, val in merge_map.items() if val == group]
    return [row["app_key"]]


def _assign(keys: list, app_paths: dict, group: str, on_done) -> None:
    ov = _overrides()
    for key in keys:
        path = (app_paths.get(key) or ("", ""))[0].lower()
        if path:
            ov[path] = group
    _save(ov)
    on_done()


def show_menu(parent, global_pos, row: dict, merge_map: dict,
              app_paths: dict, on_done) -> None:
    """在表格行上弹出合并菜单。on_done() 在规则变化后被调用（宿主 reload）。"""
    keys = members_of(row, merge_map)
    menu = QMenu(parent)
    menu.addAction(i18n.t("limit_menu"), lambda: limit_dialog(parent, row))
    menu.addSeparator()
    sub = menu.addMenu(i18n.t("merge_into"))
    for group in sorted({v for v in merge_map.values() if v != row["display_name"]}):
        sub.addAction(group, lambda g=group: _assign(keys, app_paths, g, on_done))
    sub.addAction(i18n.t("merge_new"),
                  lambda: _new_group(parent, keys, app_paths, on_done))
    if row["display_name"] in merge_map.values():
        menu.addAction(i18n.t("merge_ungroup") % row["display_name"],
                       lambda: _assign(keys, app_paths, "", on_done))
    menu.exec(global_pos)


def limit_dialog(parent, row: dict) -> None:
    """单应用用时提醒设置（v0.7）：app_limits[app_key] = 分钟，0 = 删除。
    设置非零阈值时自动打开总开关 —— 否则"设了却不生效"是惊喜缺陷。
    总开关仍可在设置页关掉（关掉只停提醒，不删各应用的阈值）。"""
    limits = config.load().get("app_limits")
    limits = dict(limits) if isinstance(limits, dict) else {}
    key = row["app_key"]
    hours, ok = QInputDialog.getDouble(
        parent, i18n.t("limit_menu"),
        i18n.t("limit_prompt", row["display_name"]),
        limits.get(key, 0) / 60.0, 0, 24, 1)
    if not ok:
        return
    if hours <= 0:
        limits.pop(key, None)
    else:
        limits[key] = int(round(hours * 60))
    try:
        config.save({"app_limits": limits,
                     "limits_enabled": True if hours > 0
                     else config.load().get("limits_enabled", False)},
                    keys=("app_limits", "limits_enabled"))
    except OSError:
        QMessageBox.warning(parent, i18n.t("set_fail_title"), i18n.t("cfg_save_fail"))


def _new_group(parent, keys: list, app_paths: dict, on_done) -> None:
    name, ok = QInputDialog.getText(parent, i18n.t("merge_new_title"),
                                    i18n.t("merge_new_prompt"))
    name = name.strip()
    if not ok or not name:
        return
    if len(name) > 40 or any(ch in name for ch in "\r\n\t"):
        QMessageBox.warning(parent, i18n.t("set_fail_title"),
                            i18n.t("merge_bad_name"))
        return
    _assign(keys, app_paths, name, on_done)
