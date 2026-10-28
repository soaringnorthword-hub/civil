# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：挡墙台账登记助手"""
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = []
datas += collect_data_files('rapidocr_onnxruntime')
datas += collect_data_files('onnxruntime')
# 剔除 onnxruntime 自带的示例数据与测试资源，减小体积
datas = [d for d in datas
         if 'onnxruntime/datasets' not in d[0].replace('\\', '/')
         and 'onnxruntime/test' not in d[0].replace('\\', '/')
         and not d[0].replace('\\', '/').endswith('.pdb')]


hiddenimports = []
hiddenimports += collect_submodules('rapidocr_onnxruntime')

excludes = ['matplotlib', 'scipy', 'pandas', 'PyQt5', 'PySide2', 'PySide6',
            'IPython', 'jupyter', 'notebook', 'pytest', 'setuptools',
            'tkinter.test', 'lib2to3', 'pip', 'wheel']

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TzLedger',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='TzLedger',
)
