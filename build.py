"""
打包脚本 - 生成单个精简 EXE 文件

说明：
- 使用临时版本信息文件，避免硬编码版本号，便于后续版本管理。
- 仅保留必要参数，删除多余的隐藏依赖、排除模块和硬编码 DLL 路径，以减小体积并提高可移植性。
- 依赖由 PyInstaller 自动分析，当前代码中用到的模块（tkinter / Pillow / mss / pyperclip / pywin32 等）
  都是显式导入的，一般无需额外声明。
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime

# 项目目录
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

# 版本号
VERSION = "1.2.2"

# GitHub信息
GITHUB_URL = "https://github.com/JIMGAN1/screenshot-tool"


def create_temp_version_file(version):
    """创建临时版本信息文件（在当前目录）"""
    # 解析版本号
    version_parts = version.split('.')
    while len(version_parts) < 4:
        version_parts.append('0')
    # 获取当前年份
    current_year = datetime.now().year
    
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
    print(f"📝 临时文件创建在：{temp_file.name}")
    return temp_file.name


def build():
    """使用 PyInstaller 打包为单个 EXE"""
    # 简单提示当前环境，建议在 JT_conda 虚拟环境中执行
    conda_env = os.environ.get("CONDA_DEFAULT_ENV", "")
    if conda_env and conda_env != "JT_conda":
        print(f"⚠ 当前 Conda 环境为：{conda_env}，建议在 JT_conda 环境中执行打包（conda activate JT_conda）")

    # 获取版本号
    version = sys.argv[1] if len(sys.argv) > 1 else VERSION
    
    # 创建临时版本文件（在当前目录）
    version_file = create_temp_version_file(version)
    print(f"✅ 创建临时版本文件：{version_file}")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",  # 单文件
        "--noconsole",  # GUI 模式，不弹出控制台
        "--name",
        "ScreenshotTool",  # 输出文件名
        "--clean",  # 清理临时文件
        "--noconfirm",  # 不询问覆盖
        "--strip",  # 去除调试符号，减小体积
        "--version-file", version_file,  # 使用临时文件
    ]

    # 图标可选：如果同目录下有 JT.ico 则使用图标
    icon_path = os.path.join(PROJECT_DIR, "JT.ico")
    if os.path.exists(icon_path):
        # 作为主程序图标嵌入，用于托盘图标
        cmd += ["--icon", icon_path]
        # 同时作为资源文件一起打包进 EXE，运行时从内部解包目录加载托盘图标
        # Windows 下 --add-data 的分隔符为 ';'
        cmd += ["--add-data", f"{icon_path};."]

    # 入口脚本（内部再导入 screenshot_app.main）
    cmd.append("main.py")

    print("开始打包...")
    print(f"命令：{' '.join(cmd)}")
    print(f"工作目录：{PROJECT_DIR}")

    try:
        subprocess.run(cmd, check=True)
        exe_path = os.path.join(PROJECT_DIR, "dist", "ScreenshotTool.exe")
        print("\n打包完成！")
        print(f"EXE 文件位置：{exe_path}")
        print(f"GitHub: {GITHUB_URL}")
    except subprocess.CalledProcessError as e:
        print(f"\n打包失败：{e}")
    except FileNotFoundError:
        print("\n错误：找不到 PyInstaller，请先安装：pip install pyinstaller")
    finally:
        # 清理临时文件（确保无论成功失败都会清理）
        if os.path.exists(version_file):
            os.remove(version_file)
            print(f"✅ 已清理临时文件：{version_file}")


if __name__ == "__main__":
    build()