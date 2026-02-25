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

from screenshot_app import main

if __name__ == "__main__":
    main()
