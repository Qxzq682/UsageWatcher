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
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'numpy', 'matplotlib'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='UsageWatcher',
    debug=False,
    strip=False,
    upx=False,          # D1 阶段再评估压缩
    console=True,       # 诊断期先开；定位后改回 False
    icon=None,          # TODO(D1)：补图标
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='UsageWatcher',
)
