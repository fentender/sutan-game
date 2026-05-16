# -*- mode: python ; coding: utf-8 -*-
"""
苏丹的游戏 - Mod 合并管理器
PyInstaller 打包配置（Windows）

用法: pyinstaller sudan.spec --noconfirm
"""
import importlib.util
import os

# 自动检测 sultan_core .pyd 路径
_sultan_core_spec = importlib.util.find_spec('sultan_core')
_sultan_core_binaries = []
if _sultan_core_spec and _sultan_core_spec.submodule_search_locations:
    _pkg_dir = _sultan_core_spec.submodule_search_locations[0]
    for f in os.listdir(_pkg_dir):
        if f.endswith('.pyd') or f.endswith('.dll'):
            _sultan_core_binaries.append((os.path.join(_pkg_dir, f), 'sultan_core'))
elif _sultan_core_spec and _sultan_core_spec.origin:
    _sultan_core_binaries.append((_sultan_core_spec.origin, '.'))

a = Analysis(
    ['app_entry.py'],
    pathex=[],
    binaries=_sultan_core_binaries,
    datas=[
        ('app.ico', '.'),
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

# 过滤掉不需要的大体积 DLL
_exclude_dlls = {
    'opengl32sw.dll',
    'Qt6Quick.dll',
    'Qt6Qml.dll',
    'Qt6Pdf.dll',
    'Qt6OpenGL.dll',
    'Qt6Network.dll',
    'Qt6Svg.dll',
    'Qt6VirtualKeyboard.dll',
    'Qt6QmlModels.dll',
    'Qt6QmlMeta.dll',
    'Qt6QmlWorkerScript.dll',
    'd3dcompiler_47.dll',
}
a.binaries = [b for b in a.binaries if b[0].split('\\')[-1].split('/')[-1] not in _exclude_dlls]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SuDanModMerger',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    icon='app.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SuDanModMerger',
)
