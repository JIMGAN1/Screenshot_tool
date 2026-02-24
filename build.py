"""
打包脚本 - 生成单个 EXE 文件
"""
import os
import subprocess
import sys

# 项目目录
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)

# PyInstaller 命令
cmd = [
    sys.executable,
    '-m', 'PyInstaller',
    '--onefile',           # 单个文件
    '--noconsole',        # 无控制台窗口（GUI模式）
    '--name', 'ScreenshotTool',  # 输出文件名
    '--clean',             # 清理临时文件
    '--noconfirm',         # 不询问确认
    '--icon', 'JT.ico',    # 使用 JT.ico 作为图标
    '--strip',             # 去除调试信息，减小文件大小
    '--noupx',             # 不使用 UPX 压缩（避免潜在问题）
    '--hidden-import', 'tkinter',
    '--hidden-import', 'PIL.Image',
    '--hidden-import', 'mss',
    '--hidden-import', 'pyperclip',
    '--hidden-import', 'ctypes',
    # 添加 tkinter 依赖的 DLL
    '--add-binary', r'C:\ProgramData\miniconda3\Library\bin\tcl86t.dll;.',
    '--add-binary', r'C:\ProgramData\miniconda3\Library\bin\tk86t.dll;.',
    # 添加 ctypes 依赖的 DLL（使用 JT_conda 环境）
    '--add-binary', r'C:\Users\10658\.conda\envs\JT_conda\Library\bin\ffi.dll;.',
    '--add-binary', r'C:\Users\10658\.conda\envs\JT_conda\Library\bin\ffi-8.dll;.',
    '--add-binary', r'C:\Users\10658\.conda\envs\JT_conda\Library\bin\libbz2.dll;.',
    '--add-binary', r'C:\Users\10658\.conda\envs\JT_conda\Library\bin\liblzma.dll;.',
    '--add-binary', r'C:\Users\10658\.conda\envs\JT_conda\Library\bin\libexpat.dll;.',
    '--add-binary', r'C:\Users\10658\.conda\envs\JT_conda\Library\bin\expat.dll;.',
    'main.py'
]

print("开始打包...")
print(f"命令：{' '.join(cmd)}")
print(f"工作目录：{PROJECT_DIR}")

try:
    subprocess.run(cmd, check=True)
    print("\n打包完成！")
    print(f"EXE 文件位置：{os.path.join(PROJECT_DIR, 'dist', 'ScreenshotTool.exe')}")
except subprocess.CalledProcessError as e:
    print(f"\n打包失败：{e}")
except FileNotFoundError:
    print("\n错误：找不到 PyInstaller，请先安装：pip install pyinstaller")
