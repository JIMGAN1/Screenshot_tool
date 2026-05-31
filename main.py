"""
截图工具 - 主程序入口
"""
import sys
import os

# 处理打包后的路径问题
if hasattr(sys, 'frozen') and sys.frozen:
    # 打包后的环境
    app_dir = os.path.dirname(sys.executable)
    os.chdir(app_dir)
    sys.path.insert(0, app_dir)

# Windows 下立即隐藏控制台窗口（防止启动时闪黑窗口）
if sys.platform == 'win32':
    import ctypes
    kernel32 = ctypes.WinDLL('kernel32')
    user32 = ctypes.WinDLL('user32')
    SW_HIDE = 0
    hWnd = kernel32.GetConsoleWindow()
    if hWnd:
        user32.ShowWindow(hWnd, SW_HIDE)

from screenshot_app import main

if __name__ == "__main__":
    main()
