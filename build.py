"""
打包脚本 - 生成单个精简 EXE 文件

说明：
- 仅保留必要参数，删除多余的隐藏依赖、排除模块和硬编码 DLL 路径，以减小体积并提高可移植性。
- 依赖由 PyInstaller 自动分析，当前代码中用到的模块（tkinter / Pillow / mss / pyperclip / pywin32 等）
  都是显式导入的，一般无需额外声明。
"""

import os
import subprocess
import sys


# 项目目录
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)


def build():
    """使用 PyInstaller 打包为单个 EXE"""
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
        # 如果你本地安装了 UPX 并且想进一步压缩，可以把下面一行注释去掉
        # "--upx-dir", r"C:\path\to\upx",
    ]

    # 图标可选：如果同目录下有 JT.ico 则使用图标
    icon_path = os.path.join(PROJECT_DIR, "JT.ico")
    if os.path.exists(icon_path):
        cmd += ["--icon", icon_path]

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
    except subprocess.CalledProcessError as e:
        print(f"\n打包失败：{e}")
    except FileNotFoundError:
        print("\n错误：找不到 PyInstaller，请先安装：pip install pyinstaller")


if __name__ == "__main__":
    build()
