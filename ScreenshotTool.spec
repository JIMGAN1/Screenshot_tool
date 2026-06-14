# -*- mode: python ; coding: utf-8 -*-
# 自动生成的 spec 文件 - 用于精简打包

excludes = ['unittest', 'test', 'tests', 'pytest', 'doctest', 'pdb', 'pydoc', 'pydoc_data', 'lib2to3', 'email', 'xml', 'xmlrpc', 'ftplib', 'imaplib', 'smtplib', 'poplib', 'nntplib', 'telnetlib', 'sqlite3', '_sqlite3', 'dbm', 'numpy.testing', 'numpy.f2py', 'numpy.distutils', 'numpy.doc', 'distutils', 'setuptools', 'pip', 'pkg_resources', 'asyncio', 'concurrent', 'multiprocessing', 'argparse', 'csv', 'calendar', 'difflib', 'pickletools', 'shelve', 'webbrowser', 'tarfile', 'bz2', 'lzma', 'curses', 'tty', 'pty', 'cgi', 'cgitb', 'turtle', 'turtledemo', 'tkinter.tix', 'PIL.ImageQt']
binary_excludes = []

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[(r'D:\SourceCode\PY\screenshot_tool\JT.ico', '.')],
    hiddenimports=['secrets', 'PIL.IcoImagePlugin', 'PIL.BmpImagePlugin'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# 过滤掉不需要的大体积二进制文件（如 libopenblas ~30MB）
a.binaries = [b for b in a.binaries if not any(ex in b[0].lower() for ex in binary_excludes)]

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
    upx=True,
    upx_exclude=['vcruntime140.dll', 'python3.dll', 'ucrtbase.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=r'D:\SourceCode\PY\screenshot_tool\version_d7i1m9rm.txt',
    icon=[r'D:\SourceCode\PY\screenshot_tool\JT.ico'] if True else [],
)
