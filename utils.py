"""
截图工具 - 工具函数模块
"""
import os
import tkinter as tk
from datetime import datetime
from PIL import ImageGrab
from pathlib import Path


def get_next_filename() -> str:
    """
    获取下一个截图文件名
    使用当前时分秒生成短文件名，避免依赖配置文件。
    例如：screenshot_142530.png
    """
    time_str = datetime.now().strftime("%y%m%d%H%M%S")
    return f"JT_{time_str}.png"


def get_current_directory() -> Path:
    """获取当前目录（打包后使用 exe 所在目录）"""
    if hasattr(os.sys, 'frozen') and os.sys.frozen:
        # 打包后的环境
        return Path(os.sys.executable).parent
    else:
        # 开发环境
        return Path(__file__).parent


def save_screenshot(image: ImageGrab.Image, filename: str = None) -> str:
    """
    保存截图到文件
    :param image: PIL Image 对象
    :param filename: 文件名，如果为 None 则自动生成
    :return: 保存的文件路径
    """
    if filename is None:
        filename = get_next_filename()

    save_dir = get_current_directory()
    filepath = save_dir / filename

    try:
        image.save(filepath, 'PNG')
        return str(filepath)
    except Exception as e:
        print(f"保存截图失败：{e}")
        return None


def copy_to_clipboard(image: ImageGrab.Image):
    """
    复制图片到剪切板
    :param image: PIL Image 对象

    优先使用 Windows 原生剪贴板（pywin32），
    失败时再回退到 PowerShell 方案，提升在其他电脑上的可用性。
    """
    # --- 首选：pywin32，直接写入 CF_DIB ---
    try:
        from io import BytesIO
        import win32clipboard
        import win32con

        output = BytesIO()
        # Clipboard 需要位图（BMP / DIB），这里用 RGB BMP
        image.convert("RGB").save(output, "BMP")
        data = output.getvalue()[14:]  # 去掉 BMP 文件头，保留 DIB 部分
        output.close()

        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, data)
        win32clipboard.CloseClipboard()
        return
    except Exception as e:
        print(f"使用 pywin32 复制到剪切板失败，将尝试 PowerShell 方案：{e}")

    # --- 回退方案：使用 PowerShell + System.Windows.Forms ---
    try:
        # 使用 tkinter 的剪切板功能启动一个隐藏的窗口（保持与原逻辑兼容）
        root = tk.Tk()
        root.withdraw()

        # 保存为临时文件
        temp_path = Path(os.environ.get('TEMP', '.')) / "screenshot_temp.png"
        image.save(temp_path, 'PNG')

        # 使用 Windows 命令复制 - 隐藏 PowerShell 窗口
        import subprocess

        # 创建隐藏窗口的标志
        CREATE_NO_WINDOW = 0x08000000

        # 使用 PowerShell 隐藏窗口并复制到剪切板
        powershell_code = f'''
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $img = [System.Drawing.Image]::FromFile("{temp_path}")
        [System.Windows.Forms.Clipboard]::SetImage($img)
        '''

        process = subprocess.Popen(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', powershell_code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW
        )

        # 等待进程完成
        process.communicate()

        root.destroy()

        # 清理临时文件（延迟删除）
        try:
            os.remove(temp_path)
        except Exception:
            pass

    except Exception as e:
        print(f"复制到剪切板失败：{e}")


def get_pixel_color(x: int, y: int) -> str:
    """
    获取指定位置的像素颜色（16 进制格式）
    :param x: X 坐标
    :param y: Y 坐标
    :return: 颜色值，格式 #RRGGBB
    """
    try:
        # 使用 mss 获取单像素颜色（更快）
        import mss
        with mss.mss() as sct:
            monitor = {"left": x, "top": y, "width": 1, "height": 1}
            screenshot = sct.grab(monitor)
            pixel = screenshot.pixel(0, 0)
            return f"#{pixel[0]:02X}{pixel[1]:02X}{pixel[2]:02X}"
    except Exception as e:
        print(f"获取颜色失败：{e}")
        return "#000000"


def format_color(color_str: str) -> tuple:
    """
    格式化颜色字符串为 RGB 元组
    :param color_str: 格式 #RRGGBB
    :return: (R, G, B) 元组
    """
    if color_str.startswith('#'):
        color_str = color_str[1:]
    return (
        int(color_str[0:2], 16),
        int(color_str[2:4], 16),
        int(color_str[4:6], 16)
    )
