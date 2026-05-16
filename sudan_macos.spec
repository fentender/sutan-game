# -*- mode: python ; coding: utf-8 -*-
"""
苏丹的游戏 - Mod 合并管理器
macOS PyInstaller 打包配置

用法: pyinstaller sudan_macos.spec --noconfirm
"""
import importlib.util
import os

# 自动检测 sultan_core .so 路径
_sultan_core_spec = importlib.util.find_spec('sultan_core')
_sultan_core_binaries = []
if _sultan_core_spec and _sultan_core_spec.submodule_search_locations:
    _pkg_dir = _sultan_core_spec.submodule_search_locations[0]
    for f in os.listdir(_pkg_dir):
        if f.endswith('.so') or f.endswith('.dylib'):
            _sultan_core_binaries.append((os.path.join(_pkg_dir, f), 'sultan_core'))
elif _sultan_core_spec and _sultan_core_spec.origin:
    _sultan_core_binaries.append((_sultan_core_spec.origin, '.'))

a = Analysis(
    ['app_entry.py'],
    pathex=[],
    binaries=_sultan_core_binaries,
    datas=[
        ('app.icns', '.'),
        ('docs/用户使用手册.md', 'docs'),
        ('schemas', 'schemas'),
        ('history_config_pack', 'history_config_pack'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'rapidfuzz.fuzz',
        'rapidfuzz.process',
        'rapidfuzz.utils',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6.QtWebEngine',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtNetwork',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DRender',
        'PySide6.QtMultimedia',
        'PySide6.QtSvg',
        'PySide6.QtSvgWidgets',
        'PySide6.QtOpenGL',
        'PySide6.QtOpenGLWidgets',
        'PySide6.QtBluetooth',
        'PySide6.QtDBus',
        'PySide6.QtDesigner',
        'PySide6.QtHelp',
        'PySide6.QtPdf',
        'PySide6.QtPdfWidgets',
        'PySide6.QtSql',
        'PySide6.QtTest',
        'PySide6.QtXml',
        'PySide6.QtVirtualKeyboard',
        'numpy',
        'numpy.libs',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

# 过滤不需要的 Qt 框架
_exclude_frameworks = {
    'QtQuick',
    'QtQml',
    'QtPdf',
    'QtOpenGL',
    'QtNetwork',
    'QtSvg',
    'QtVirtualKeyboard',
    'QtQmlModels',
    'QtQmlMeta',
    'QtQmlWorkerScript',
}
a.binaries = [b for b in a.binaries
              if not any(fw in b[0] for fw in _exclude_frameworks)]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SuDanModMerger',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon='app.icns',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SuDanModMerger',
)

app = BUNDLE(
    coll,
    name='SuDanModMerger.app',
    icon='app.icns',
    bundle_identifier='com.sudan.modmerger',
)
