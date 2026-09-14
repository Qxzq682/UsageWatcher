# 第三方组件许可证

本项目自身以 **MIT** 许可证发布（见 `LICENSE`）。
但它依赖以下第三方组件，**这些组件各自的许可证义务并不因为本项目用 MIT 而免除**，
所以单独列出。发布二进制分发（打包成 exe）时请一并保留本文件。

---

## 1. PySide6（Qt for Python）— 需要重点注意的一项

| 项目 | 内容 |
|---|---|
| 组件 | PySide6-Essentials / shiboken6 |
| 版本 | 6.11.1 |
| 许可证 | `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`（另有商业许可） |
| 本项目采用 | **LGPL-3.0** |

### 本项目为此履行的义务

1. **附许可证全文**：LGPL-3.0 全文见 https://www.gnu.org/licenses/lgpl-3.0.txt
   （并配合 GPL-3.0 全文使用：https://www.gnu.org/licenses/gpl-3.0.txt）
2. **允许用户替换 Qt 库**：本项目打包为 **onedir** 结构，Qt 的动态库以独立文件形式
   放在 `dist\软件使用时长监测\_internal\` 下（如 `Qt6Core.dll`、`Qt6Gui.dll`、
   `Qt6Widgets.dll` 等）。用户可以直接替换这些文件而不需要修改或重新编译本项目，
   满足 LGPL-3.0 关于「可替换库」（relink）的要求。
3. **未修改 Qt 源码**：本项目没有对 Qt/PySide6 做任何修改。
4. 本项目**动态链接** Qt，未做静态链接。

### 用户注意

如果你替换了 `_internal` 下的 Qt 动态库，请自行确认替换版本的许可证与兼容性。

---

## 2. 其余依赖

| 组件 | 版本 | 许可证 | 义务 |
|---|---|---|---|
| psutil | 7.2.2 | BSD-3-Clause | 保留版权声明与免责声明 |
| pywin32 | 312 | PSF（Python Software Foundation License） | 保留版权声明 |
| shiboken6 | 6.11.1 | 同 PySide6（LGPL-3.0 / GPL / 商业） | 同第 1 节 |

### 开发期（不随程序分发）

| 组件 | 版本 | 许可证 | 说明 |
|---|---|---|---|
| PyInstaller | 6.21.0 | GPLv2 或更高，**附带例外条款：允许用它打包并分发非自由程序** | **打包产物不受 GPL 传染**。这一点常被误解，特此写明。 |
| pyinstaller-hooks-contrib | 2026.6 | Apache-2.0 / GPL-2.0 | 仅打包期使用 |

---

## 3. 许可证文本获取方式

各依赖的完整许可证文本随其安装包一同分发，也可从下列位置获取：

- Qt / PySide6：https://www.qt.io/licensing/open-source-lgpl-obligations
- LGPL-3.0：https://www.gnu.org/licenses/lgpl-3.0.txt
- GPL-3.0：https://www.gnu.org/licenses/gpl-3.0.txt
- psutil：https://github.com/giampaolo/psutil/blob/master/LICENSE
- pywin32：https://github.com/mhammond/pywin32/blob/main/LICENSE.txt
- PyInstaller：https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt
