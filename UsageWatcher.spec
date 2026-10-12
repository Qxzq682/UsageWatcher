# -*- mode: python ; coding: utf-8 -*-
# UsageWatcher 打包配置（PyInstaller 6.x，onedir + 无控制台）。
# 构建：python -m PyInstaller --noconfirm --clean UsageWatcher.spec
# 注意：lite 增量更新依赖"同一 PyInstaller 版本构建"，升级版本必须发全量包。
# 当前版本：6.21.0

a = Analysis(
    ['src\\app.py'],
    pathex=['src'],
    # v0.6：全局样式表与双语言隐私说明是运行时资源（ui_charts.asset_path 定位）
    datas=[('assets\\style.qss', 'assets'),
           ('assets\\privacy_zh.html', 'assets'),
           ('assets\\privacy_en.html', 'assets')],
    # ui_main/ui_scope/ui_overlay 是函数内 import（守护模式永不加载 Qt），
    # pycaw 是 audio.py 函数内 import —— 显式声明防漏收
    hiddenimports=['ui_main', 'ui_scope', 'ui_overlay', 'pycaw.pycaw'],
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

# v0.3 多语言：保留通用语言的 Qt 界面翻译（对话框按钮随界面语言），
# 冷门语言仍剔（这是 D1 剔除 7MB 的主要构成）。只留 qtbase_*.qm。
_QM_KEEP = {"qtbase_zh_cn", "qtbase_zh_tw", "qtbase_ja", "qtbase_ko",
            "qtbase_ru", "qtbase_de", "qtbase_fr", "qtbase_es",
            "qtbase_pt", "qtbase_pt_br", "qtbase_it"}

def _keep(entry) -> bool:
    """entry 是 (dest_name, source, kind)。返回 False = 从包里剔除。"""
    name = entry[0].lower().replace("\\", "/")
    if "opengl32sw" in name:
        return False   # 20MB 软件OpenGL；QPainter 无 GPU 路径用不上
    if "/translations/" in name:
        stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return stem in _QM_KEEP   # v0.3：恢复通用语言（qtbase 对话框按钮）；冷门语言仍删
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
