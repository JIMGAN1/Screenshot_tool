"""
打包脚本 - 生成单个精简 EXE 文件

说明：
- 使用临时版本信息文件，避免硬编码版本号，便于后续版本管理。
- 仅保留必要参数，删除多余的隐藏依赖、排除模块和硬编码 DLL 路径，以减小体积并提高可移植性。
- 依赖由 PyInstaller 自动分析，当前代码中用到的模块（tkinter / Pillow / mss / numpy / pywin32 等）
  都是显式导入的，一般无需额外声明。
"""

import os
import subprocess
import sys
import tempfile

# 项目目录
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

# 版本号
VERSION = "3.7.0"

# GitHub信息
GITHUB_URL = "https://github.com/JIMGAN1/screenshot-tool"


def create_temp_version_file(version):
    """创建临时版本信息文件（在当前目录）"""
    # 解析版本号
    version_parts = version.split('.')
    while len(version_parts) < 4:
        version_parts.append('0')
    
    # 创建临时文件 - 指定在当前目录
    temp_file = tempfile.NamedTemporaryFile(
        mode='w', 
        suffix='.txt', 
        prefix='version_',  # 添加前缀方便识别
        dir=PROJECT_DIR,     # 指定在当前目录创建
        delete=False, 
        encoding='utf-8'
    )
    
    # 版本信息内容 - 使用f-string
    content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({version_parts[0]}, {version_parts[1]}, {version_parts[2]}, {version_parts[3]}),
    prodvers=({version_parts[0]}, {version_parts[1]}, {version_parts[2]}, {version_parts[3]}),
    mask=0x3f,
    flags=0x0,
    OS=0x00040004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [StringStruct(u'CompanyName', u'JIMGAN1'),
           StringStruct(u'FileDescription', u'快捷截图工具'),
           StringStruct(u'FileVersion', u'{version}'),
           StringStruct(u'InternalName', u'ScreenshotTool'),
           StringStruct(u'LegalCopyright', u'源码:{GITHUB_URL}'),
           StringStruct(u'OriginalFilename', u'ScreenshotTool.exe'),
           StringStruct(u'ProductName', u'快捷截图'),
           StringStruct(u'ProductVersion', u'{version}')]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [2052, 1200])])
  ]
)
"""
    
    temp_file.write(content)
    temp_file.close()
    
    # 打印临时文件位置
    print(f"[INFO] 临时文件创建在：{temp_file.name}")
    return temp_file.name


def build():
    """使用 PyInstaller 打包为单个 EXE"""
    # 简单提示当前环境，建议在 JT_conda 虚拟环境中执行
    conda_env = os.environ.get("CONDA_DEFAULT_ENV", "")
    if conda_env and conda_env != "JT_conda":
        print(f"[WARN] 当前 Conda 环境为：{conda_env}，建议在 JT_conda 环境中执行打包（conda activate JT_conda）")

    # 获取版本号
    version = sys.argv[1] if len(sys.argv) > 1 else VERSION
    
    # 创建临时版本文件（在当前目录）
    version_file = create_temp_version_file(version)
    print(f"[OK] 创建临时版本文件：{version_file}")

    # ---- 排除不需要的标准库和第三方模块，大幅减小体积 ----
    excludes = [
        # 测试 / 调试 / 文档相关
        "unittest", "test", "tests", "pytest", "doctest", "pdb", "pydoc",
        "pydoc_data", "lib2to3",
        # 网络 / 邮件 / 数据库（本项目不需要）
        # 注意：urllib 不能排除，因为 pathlib → urllib（而 zipfile.path → pathlib）
        #       http 不能排除，因为 urllib 依赖 http.client
        "email", "xml", "xmlrpc", "ftplib", "imaplib",
        "smtplib", "poplib", "nntplib", "telnetlib",
        "sqlite3", "_sqlite3", "dbm",
        # 科学计算 / 数据处理（numpy 核心 C 扩展与子模块耦合紧密，只排除安全的部分）
        "numpy.testing", "numpy.f2py", "numpy.distutils", "numpy.doc",
        # 编译 / 打包相关
        "distutils", "setuptools", "pip", "pkg_resources",
        # 其他不需要的标准库
        # 注意：zipfile 是 PyInstaller 运行时钩子(pyi_rth_inspect)的必需依赖，不能排除
        # 注意：logging 不能排除，因为 PIL.Image 依赖 logging
        # 注意：locale 不能排除，因为 subprocess → locale
        # 注意：gettext 不能排除，因为 argparse → gettext
        "asyncio", "concurrent", "multiprocessing",
        # 注意：fractions 不能排除，因为 PIL.PngImagePlugin 依赖 fractions
        # 注意：decimal 不能排除，因为 fractions 依赖 decimal
        "argparse", "csv",
        "calendar", "difflib",
        "pickletools", "shelve", "webbrowser",
        "tarfile", "bz2", "lzma",
        "curses", "tty", "pty", "cgi", "cgitb",
        "turtle", "turtledemo", "tkinter.tix",
        # Pillow 不需要的格式插件
        "PIL.ImageQt",
    ]

    # 需要从打包中剔除的大体积二进制文件关键字
    # 注意：libopenblas / libgfortran / libquadmath / libgcc_s 不能排除，
    #       因为 numpy._multiarray_umath.pyd 运行时依赖这些 DLL
    # 注意：_multiarray_tests 不能排除，因为 numpy.core._add_newdocs 初始化时会导入它
    binary_excludes = [
        # 暂无需要排除的二进制文件
    ]

    # ---- 生成 .spec 文件内容，以便精确控制二进制依赖 ----
    icon_path = os.path.join(PROJECT_DIR, "JT.ico")
    has_icon = os.path.exists(icon_path)

    spec_content = f'''# -*- mode: python ; coding: utf-8 -*-
# 自动生成的 spec 文件 - 用于精简打包

excludes = {excludes!r}
binary_excludes = {binary_excludes!r}

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[{f"(r'{icon_path}', '.')" if has_icon else ""}],
    hiddenimports=['secrets', 'PIL.IcoImagePlugin', 'PIL.BmpImagePlugin'],
    hookspath=[],
    hooksconfig={{}},
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
    version=r'{version_file}',
    icon=[r'{icon_path}'] if {has_icon} else [],
)
'''

    spec_file = os.path.join(PROJECT_DIR, "ScreenshotTool.spec")
    with open(spec_file, "w", encoding="utf-8") as f:
        f.write(spec_content)
    print(f"生成 spec 文件：{spec_file}")

    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--clean",            # 清理临时文件
        "--noconfirm",        # 不询问覆盖
        # "--windowed",
        "--upx-dir", r"D:\Programs\Python\Python312\Scripts",  # UPX 压缩路径
        spec_file,            # 使用 spec 文件打包
    ]

    print("开始打包...")
    print(f"命令：{' '.join(cmd)}")
    print(f"工作目录：{PROJECT_DIR}")

    try:
        subprocess.run(cmd, check=True)
        exe_path = os.path.join(PROJECT_DIR, "dist", "ScreenshotTool.exe")
        print("\n打包完成！")
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"EXE 文件位置：{exe_path}")
        print(f"EXE 文件大小：{size_mb:.2f} MB")
        print(f"GitHub: {GITHUB_URL}")
    except subprocess.CalledProcessError as e:
        print(f"\n打包失败：{e}")
    except FileNotFoundError:
        print("\n错误：找不到 PyInstaller，请先安装：pip install pyinstaller")
    finally:
        # 清理临时文件（确保无论成功失败都会清理）
        if os.path.exists(version_file):
            os.remove(version_file)
            print(f"[OK] 已清理临时文件：{version_file}")


if __name__ == "__main__":
    build()