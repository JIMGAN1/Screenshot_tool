# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[('C:\\ProgramData\\miniconda3\\Library\\bin\\tcl86t.dll', '.'), ('C:\\ProgramData\\miniconda3\\Library\\bin\\tk86t.dll', '.'), ('C:\\Users\\10658\\.conda\\envs\\JT_conda\\Library\\bin\\ffi.dll', '.'), ('C:\\Users\\10658\\.conda\\envs\\JT_conda\\Library\\bin\\ffi-8.dll', '.'), ('C:\\Users\\10658\\.conda\\envs\\JT_conda\\Library\\bin\\libbz2.dll', '.'), ('C:\\Users\\10658\\.conda\\envs\\JT_conda\\Library\\bin\\liblzma.dll', '.'), ('C:\\Users\\10658\\.conda\\envs\\JT_conda\\Library\\bin\\libexpat.dll', '.'), ('C:\\Users\\10658\\.conda\\envs\\JT_conda\\Library\\bin\\expat.dll', '.')],
    datas=[],
    hiddenimports=['tkinter', 'PIL.Image', 'mss', 'pyperclip', 'ctypes'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ScreenshotTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['JT.ico'],
)
