# -*- mode: python ; coding: utf-8 -*-
# UsageWatcher 打包配置（PyInstaller 6.x，onedir + 无控制台）。
# 构建：python -m PyInstaller --noconfirm --clean UsageWatcher.spec
# 注意：lite 增量更新依赖"同一 PyInstaller 版本构建"，升级版本必须发全量包。
# 当前版本：6.21.0

a = Analysis(
    ['src\\app.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    # ui_main/ui_scope 是函数内 import（守护模式永不加载 Qt），
    # pycaw 是 audio.py 函数内 import —— 显式声明防漏收
    hiddenimports=['ui_main', 'ui_scope', 'pycaw.pycaw'],
    # ssl/_ssl 明确排除：R4 零网络红线，程序根本不该有 TLS 能力
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'numpy', 'matplotlib',
              'ssl', '_ssl',
              'PySide6.QtNetwork', 'PySide6.QtSvg', 'PySide6.QtPdf',
              'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtUiTools',
              'PySide6.QtTest'],
    noarchive=False,
)

# ---------------------------------------------------------- D1 瘦身过滤
# 依据 dist2 实测体积构成剔除零使用项，94MB → 约 50MB。任何一项被误删，
# 构建冒烟（tools/_pkg_smoke2.py）会当场暴露。

def _keep(entry) -> bool:
    """entry 是 (dest_name, source, kind)。返回 False = 从包里剔除。"""
    name = entry[0].lower().replace("\\", "/")
    if "opengl32sw" in name:
        return False   # 20MB 软件OpenGL；QPainter 无 GPU 路径用不上
    if "/translations/" in name and not name.endswith("qtbase_zh_cn.qm"):
        return False   # 只留中文 QMessageBox 按钮翻译，其余几十种语言全删
    if "qt6network" in name or "qtnetwork" in name:
        return False   # Qt 网络模块（含插件），零联网程序不带走
    if "qt6svg" in name or "qsvg" in name or "qsvgicon" in name:
        return False   # SVG 渲染；界面无 SVG 资源，exe 图标走 win32 原生
    if "libcrypto" in name or "libssl" in name:
        return False   # _ssl/_hashlib 链路的 OpenSSL；零联网不需要
    if "/plugins/tls/" in name or "/plugins/networkinformation/" in name \
            or "/plugins/generic/" in name:
        return False   # TLS 后端 / 网络状态 / 触摸板等无关插件
    if "/imageformats/" in name and "qico" not in name:
        return False   # 界面只显示 exe 内嵌图标（ico），gif/jpeg/webp/tiff 全删
    return True

a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='UsageWatcher',
    debug=False,
    strip=False,
    upx=False,          # UPX 压缩会显著提高杀软误报率，刻意不用
    console=False,      # 托盘常驻程序，无控制台；app.py 已兜底 stdout/stderr 为 None 的情况
    icon='assets\\app_icon.ico',   # v0.2.1：exe 文件图标（AI 生成 + tools/_make_ico.py 加工）
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='UsageWatcher',
)
