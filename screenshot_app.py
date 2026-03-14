"""
快捷截图 - 主界面类
"""
import tkinter as tk
import os
import sys
import ctypes
from ctypes import wintypes, Structure, POINTER, c_int, c_uint, byref, windll
import threading
import queue
# import time

import mss
from PIL import Image
import pystray
import keyboard

from capture_overlay import CaptureOverlay
from utils import save_screenshot, copy_to_clipboard


# ==================== Windows 虚拟键码 ====================
VK_SNAPSHOT = 0x2C  # PrintScreen
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_SHIFT = 0x10
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# 修饰键标志
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008


# ==================== 使用 keyboard 库实现全局快捷键 ====================

# 存储全局快捷键句柄
_keyboard_hotkey_handle = None
_keyboard_callback_func = None


def _install_keyboard_hook(callback_func):
    """
    安装全局快捷键监听，使用 keyboard 库
    :param callback_func: 回调函数，当按下键盘时调用
    :return: True 表示安装成功，False 表示失败
    """
    global _keyboard_hotkey_handle, _keyboard_callback_func
    try:
        # keyboard 库使用简单，直接注册一个监听器
        # 回调函数会在按键事件触发时被调用
        _keyboard_callback_func = callback_func
        keyboard.hook(callback_func)
        _keyboard_hotkey_handle = True
        print(f"[keyboard] 全局键盘监听已安装")
        return True
    except Exception as e:
        print(f"安装全局键盘监听失败: {e}")
        return False


def _uninstall_keyboard_hook():
    """卸载全局键盘监听"""
    global _keyboard_hotkey_handle, _keyboard_callback_func
    try:
        keyboard.unhook_all()
        _keyboard_hotkey_handle = None
        _keyboard_callback_func = None
    except Exception:
        pass


def _resource_path(relative_path: str) -> str:
    """获取资源文件路径，兼容开发环境和 PyInstaller 单文件环境."""
    # PyInstaller 在运行时会在 sys._MEIPASS 中解包资源文件
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

class ScreenshotApp:
    """快捷截图主界面"""

    def __init__(self, root):
        """
        初始化主界面
        :param root: Tkinter 根窗口
        """
        self.root = root
        self.auto_save = tk.BooleanVar(value=False)
        self.is_capturing = False
        # 是否在截图完成后恢复主界面（只有当用户主动“显示程序界面”后才为 True）
        self.restore_after_capture = False
        # 用于跟踪已打开的设置快捷键对话框，避免重复打开
        self._hotkey_dialog: tk.Toplevel | None = None
        # 创建独立于 root 的隐藏 Toplevel 父窗口，用于快捷键对话框，使其不受主窗口隐藏影响
        # 注意：必须在 root 被 withdraw 之前创建，否则可能会导致问题
        self._dialog_parent = None  # 延迟创建

        # 设置窗口基础属性
        self._setup_window()

        # 创建界面元素
        self._create_widgets()

        # 根据实际内容和 DPI 自动调整窗口大小和位置
        self._adjust_window_geometry()

        # 全界面可以拖动
        self.root.bind("<Button-1>", self.start_drag)
        self.root.bind("<B1-Motion>", self.drag)

        # 记录拖动起始位置
        self.x = 0
        self.y = 0

    def _setup_window(self):
        """设置窗口基础属性（不固定高度，避免高分屏文字被挤压）"""
        self.root.title("✂️快捷截图")
        self.root.resizable(False, False)

        # 移除窗口图标
        # try:
        #     self.root.iconbitmap('')
        # except:
        #     pass

        # 使用工具窗口样式，只保留关闭按钮（隐藏最小化和最大化），并设置图标
        try:
            self.root.attributes('-toolwindow', True)
        except Exception:
            pass
        self.root.attributes('-topmost', True)  # 始终置顶

        # 获取系统 DPI 缩放比例
        self._scale_factor = self._get_scale_factor()

        # # 设置左上角图标，与托盘图标保持一致风格
        # try:
        #     icon_path = _resource_path("JT.ico")
        #     if os.path.exists(icon_path):
        #         self.root.iconbitmap(icon_path)
        # except Exception:
        #     pass

    def _get_scale_factor(self):
        """获取系统 DPI 缩放比例"""
        try:
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            dpi = user32.GetDpiForSystem()
            return dpi / 96.0
        except Exception:
            return 1.0

    def _adjust_window_geometry(self):
        """根据控件实际大小和屏幕分辨率，自动调整窗口大小和位置

        这样在 125% / 150% 缩放、高 DPI 显示器上，文字不会被挤压或裁剪。
        """
        # 先让 Tk 计算所有控件所需的最小大小
        self.root.update_idletasks()

        # 当前按钮宽度，确保“截图”与“自动保存”宽度一致
        capture_width = self.capture_btn.winfo_reqwidth() if hasattr(self, "capture_btn") else 0
        auto_width = self.auto_save_cb.winfo_reqwidth() if hasattr(self, "auto_save_cb") else 0
        button_width = max(capture_width, auto_width)

        req_width = max(self.root.winfo_reqwidth(), button_width)
        req_height = self.root.winfo_reqheight()

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # 基础内边距：左右各5 * scale_factor
        padding = int(10 * self._scale_factor)
        # 最小宽度
        base_min_width = 180

        # 宽度：取实际需要宽度和最小宽度的较大值
        win_width = max(req_width + padding, int(base_min_width * self._scale_factor))

        # 高度直接用控件实际需要的高度，保证文字不被裁剪
        win_height = req_height

        # 位置：右上角，预留出一点距离（根据缩放调整）
        offset = int(30 * self._scale_factor)
        win_x = screen_width//10 * 9 - win_width - offset
        win_y = screen_height // 10

        self.root.geometry(f"{win_width}x{win_height}+{win_x}+{win_y}")


    def _create_widgets(self):
        """创建界面元素"""
        # 设置背景色
        bg_color = '#9DDEF7'#3ee0f5
        self.root.configure(bg=bg_color)

        # 主框架
        main_frame = tk.Frame(self.root, bg=bg_color)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=int(5 * self._scale_factor), pady=int(5 * self._scale_factor))

        # 按钮统一颜色
        btn_bg = '#00BBFD'  # 按钮初始颜色
        btn_fg = 'white'      # 字体颜色
        btn_checked_bg = '#0083FC'  # 勾选后深蓝色

        # 根据缩放比例计算字体大小 # 字体系统自动缩放了，不需要再缩放
        capture_btn_font_size = max(11, int(13)) 
        auto_save_font_size = max(9, int(11))

        # 截图按钮(高度扩大1.5倍)
        self.capture_btn = tk.Button(
            main_frame,
            text="截图",
            font=('Microsoft YaHei UI', capture_btn_font_size, 'bold'),
            bg=btn_bg,
            fg=btn_fg,
            activebackground="#0099f9", # 激活背景颜色
            activeforeground="#81FFC0", # 激活字体颜色
            # highlightcolor="#181a1b",#
            cursor='hand2',# 鼠标悬停时显示手型
            command=self._on_capture_click,
            # relief=tk.FLAT, 
            relief=tk.RAISED # 按钮样式
        )
        self.capture_btn.pack(fill=tk.X, pady=(0, int(3 * self._scale_factor)))

        # 启动后更新按钮文字（添加快捷键）
        self.root.after(500, self._update_capture_btn_text)

        # 自动保存复选框
        cb_frame = tk.Frame(main_frame, bg=bg_color)
        cb_frame.pack(fill=tk.X)

        self.auto_save_cb = tk.Checkbutton(
            cb_frame,
            text="自动保存",
            variable=self.auto_save,
            font=('Microsoft YaHei UI', auto_save_font_size, 'bold'),
            bg=btn_bg,
            fg=btn_fg,
            selectcolor=btn_checked_bg,
            activebackground=btn_bg,
            activeforeground=btn_fg,
            cursor='hand2',
            relief=tk.FLAT,
            command=self._on_auto_save_changed,# 绑定事件
            indicatoron=False,# 不显示默认的勾
        )
        self.auto_save_cb.pack(fill=tk.X)

    def _on_auto_save_changed(self):
        """自动保存状态改变"""
        if self.auto_save.get():
            self.capture_btn.config(bg='#0083FC')
        else:
            self.capture_btn.config(bg='#00BBFD')  # 恢复初始颜色

    def _update_capture_btn_text(self):
        """更新截图按钮文字，显示当前快捷键"""
        global CURRENT_HOTKEY
        if CURRENT_HOTKEY:
            self.capture_btn.config(text=f"截图{CURRENT_HOTKEY}")
        # 更新按钮文字后重新调整窗口宽度
        self.root.after(0, self._adjust_window_geometry)

    def _on_capture_click(self):
        """点击截图按钮"""
        if self.is_capturing:
            return

        self.is_capturing = True
        self._update_status("正在截图，请框选区域...")

        # 隐藏主窗口
        self.root.withdraw()

        # 创建覆盖层
        def on_capture(image, bbox):
            """截图完成回调"""
            try:
                # 复制到剪切板
                copy_to_clipboard(image)

                # 保存文件（如果需要）
                saved_path = None
                if self.auto_save.get():
                    saved_path = save_screenshot(image)

                # 显示主窗口
                self.root.after(0, self._on_capture_done, saved_path)

            except Exception as e:
                print(f"截图处理失败：{e}")
                self.root.after(0, self._on_capture_error, str(e))
            finally:
                self.is_capturing = False

        def on_cancel():
            """取消截图回调"""
            self.root.after(0, self._on_capture_cancelled)
            self.is_capturing = False

        # 创建覆盖层（稍后在主线程中）
        self.root.after(100, lambda: self._show_overlay(on_capture, on_cancel))

    def _show_overlay(self, on_capture, on_cancel):
        """显示覆盖层

        在创建覆盖层之前，预先截一张“干净”的全屏图，用于放大镜和取色，
        这样半透明遮罩就不会影响颜色数值。
        """
        try:
            # 使用 mss 的主监视器信息截取整屏图像（包含任务栏），
            # 作为放大镜/取色以及“桌面全屏截图”的真实基准尺寸。
            with mss.mss() as sct:
                monitor = sct.monitors[1]  # 主显示器
                screenshot = sct.grab(monitor)
                base_image = Image.frombytes(
                    "RGB", screenshot.size, screenshot.bgra, "raw", "BGRX"
                )

            # 把预先截好的底图传给覆盖层，后续所有取色/放大都基于这张图
            # 保留引用，避免对象被GC导致事件回调失效
            self.overlay = CaptureOverlay(self.root, on_capture, on_cancel, base_image)
        except Exception as e:
            print(f"创建覆盖层失败：{e}")
            self._on_capture_error(str(e))

    def _on_capture_done(self, saved_path):
        """截图完成"""
        # 只有在用户主动“显示程序界面”后，才在每次截图后恢复主窗口
        if self.restore_after_capture:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes('-topmost', True)

        if saved_path:
            filename = os.path.basename(saved_path)
            self._update_status(f"截图完成！已保存：{filename}\n并已复制到剪切板")
        else:
            self._update_status("截图完成！已复制到剪切板")

    def _on_capture_cancelled(self):
        """截图取消"""
        # 只有在用户主动“显示程序界面”后，才在每次截图后恢复主窗口
        if self.restore_after_capture:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes('-topmost', True)

        self._update_status("截图已取消")

    def _on_capture_error(self, error_msg):
        """截图错误"""
        # 只有在用户主动“显示程序界面”后，才在每次截图后恢复主窗口
        if self.restore_after_capture:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes('-topmost', True)

        self._update_status(f"截图失败：{error_msg}")
        # _show_message("错误", f"截图失败：{error_msg}")

    def _update_status(self, message):
        """更新状态信息"""
        # 由于去掉了状态栏，这里可以记录日志或执行其他操作
        print(message)
    
    def start_drag(self, event):
        """记录拖动起始点"""
        self.x = event.x
        self.y = event.y
    
    def drag(self, event):
        """处理拖动"""
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")


def _set_dpi_awareness():
    """避免高分屏缩放导致坐标/截图偏移"""
    try:
        # Windows 8.1 及以上
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            # 旧版本兼容
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    

def _ensure_single_instance() -> bool:
    """确保程序单实例运行，若已有实例则返回 False."""
    # 只在 Windows 下使用基于命名互斥量的单实例判断
    if not sys.platform.startswith("win"):
        return True

    mutex_name = "Global\\QuickScreenshotMutex"
    kernel32 = ctypes.windll.kernel32

    h_mutex = kernel32.CreateMutexW(None, False, mutex_name)
    if not h_mutex:
        # 创建互斥量失败时，放行程序，避免因权限问题完全无法使用
        return True

    ERROR_ALREADY_EXISTS = 183
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        # 已经有实例在运行，静默退出
        kernel32.CloseHandle(h_mutex)
        return False

    # 保存句柄，保持互斥量在进程生命周期内有效
    globals()["_instance_mutex_handle"] = h_mutex
    return True


def main():
    """主函数"""
    # 确保程序单实例运行
    if not _ensure_single_instance():
        return
    _set_dpi_awareness()
    root = tk.Tk()
    # 启动时不显示主界面，只在系统托盘中显示图标
    root.withdraw()

    # 全局变量：当前快捷键（供按钮和托盘菜单使用）
    global CURRENT_HOTKEY
    CURRENT_HOTKEY = ""

    # 创建应用（窗口位置会在初始化时设置好）
    app = ScreenshotApp(root)

    # 复用 ScreenshotApp 的缩放比例
    _message_scale_factor = app._scale_factor

    # 关闭按钮只隐藏窗口，不退出程序（保留托盘图标继续工作）
    def on_close():
        root.withdraw()
        # 用户主动关闭主窗口后，不再在截图完成时自动恢复主界面
        app.restore_after_capture = False

    root.protocol("WM_DELETE_WINDOW", on_close)

    # ----------------- 全局快捷键支持（Windows） -----------------
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008
    WM_HOTKEY = 0x0312

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", wintypes.POINT),
        ]

    # ==================== 自定义消息框（无图标）====================

    def _show_message(title, message, msg_type="info", parent=None):
        """
        显示无图标的自定义消息框
        msg_type: "info", "warning", "error"
        """
        dialog = tk.Toplevel()
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.configure(bg="#9DDEF7")

        # 根据缩放比例计算窗口尺寸
        base_width, base_height = 300, 120
        width = int(base_width * _message_scale_factor)
        height = int(base_height * _message_scale_factor)

        # 根据缩放比例计算字体大小
        base_font_size = 13
        font_size = max(10, int(base_font_size))

        # 根据缩放比例计算内边距
        base_wraplength = 256
        wraplength = int(base_wraplength * _message_scale_factor)

        dialog.geometry(f"{width}x{height}")

        # 先隐藏窗口，避免显示时产生残影
        dialog.withdraw()

        # 居中显示
        dialog.update_idletasks()
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        display_width = int(270 * _message_scale_factor)
        display_height = int(120 * _message_scale_factor)
        x = (sw - display_width) // 2
        y = (sh - display_height) // 2
        dialog.geometry(f"{display_width}x{display_height}+{x}+{y}")

        # 设置为工具窗口（不在任务栏显示）
        try:
            dialog.attributes("-toolwindow", True)
        except Exception:
            pass

        # 消息文本
        msg_label = tk.Label(
            dialog,
            text=message,
            bg="#9DDEF7",
            fg="#053D97",
            font=("Microsoft YaHei UI", font_size, "bold"),
            wraplength=wraplength,
            justify="center"
        )
        msg_label.pack(expand=True, fill=tk.BOTH, pady=(int(7 * _message_scale_factor), int(7 * _message_scale_factor)))

        # 确定按钮
        ok_btn = tk.Button(
            dialog,
            text="确 定",
            font=("Microsoft YaHei UI", font_size + 1, "bold"),
            bg="#3498db",
            fg="white",
            activebackground="#0099f9",
            activeforeground="#81FFC0",
            width=10,
            borderwidth=0,
            command=dialog.destroy
        )
        ok_btn.pack(pady=(0, int(13 * _message_scale_factor)))

        # 居中父窗口
        if parent:
            dialog.transient(parent)
            dialog.grab_set()

        # 设置完成后再显示窗口
        dialog.deiconify()

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.focus_force()
        dialog.wait_window()

    def _keysym_to_vk(name: str):
        """将 Tk / 字符串键名转换为虚拟键码（VK）。返回 int 或 None。"""
        if not name:
            return None
        n = name  # 直接使用原始输入，不转换大写

        # 字母（需要转为大写来获取VK）
        if len(n) == 1 and n.isalpha():
            return ord(n.upper())
        # 数字
        if len(n) == 1 and "0" <= n <= "9":
            return ord(n)

        # 功能键 F1-F24（支持大小写，如 F1, f1）
        if len(n) >= 2 and n[0].upper() == "F" and n[1:].isdigit():
            fnum = int(n[1:])
            if 1 <= fnum <= 24:
                return 0x70 + (fnum - 1)

        # 数字键盘 0-9（支持大小写）
        if n.upper().startswith("NUMPAD") and len(n) >= 7:
            num = n[-1]
            if "0" <= num <= "9":
                return 0x60 + int(num)

        # pynput 可能的键名映射（Tkinter 原始格式）
        pynput_mapping = {
            # 数字键盘
            "KP_0": 0x60,
            "KP_1": 0x61,
            "KP_2": 0x62,
            "KP_3": 0x63,
            "KP_4": 0x64,
            "KP_5": 0x65,
            "KP_6": 0x66,
            "KP_7": 0x67,
            "KP_8": 0x68,
            "KP_9": 0x69,
            "KP_Add": 0x6B,  # +
            "KP_Subtract": 0x6D,  # -
            "KP_Multiply": 0x6A,  # *
            "KP_Divide": 0x6F,  # /
            "KP_Decimal": 0x6E,  # .
            # 符号键（常见符号）
            "minus": 0xBD,  # -
            "equal": 0xBB,  # =
            "bracketleft": 0xDB,  # [
            "bracketright": 0xDD,  # ]
            "backslash": 0xDC,  # \
            "semicolon": 0xBA,  # ;
            "quoteright": 0xDE,  # '
            "comma": 0xBC,  # ,
            "period": 0xBE,  # .
            "slash": 0xBF,  # /
            "grave": 0xC0,  # `
            # 特殊键（Tkinter 原始格式）
            "BackSpace": 0x08,
            "Tab": 0x09,
            "Return": 0x0D,
            "Escape": 0x1B,
            "space": 0x20,
            "Prior": 0x21,
            "Next": 0x22,
            "End": 0x23,
            "Home": 0x24,
            "Left": 0x25,
            "Up": 0x26,
            "Right": 0x27,
            "Down": 0x28,
            "Insert": 0x2D,
            "Delete": 0x2E,
            # 功能键（备选）
            "Print": 0x2C,
            "Scroll_Lock": 0x91,
            "Pause": 0x13,
            "Num_Lock": 0x90,
            "Caps_Lock": 0x14,
        }

        # 先尝试直接匹配
        if n in pynput_mapping:
            return pynput_mapping[n]
        # 再尝试大写匹配（兼容旧注册表数据如 END, HOME 等）
        n_upper = n.upper()
        for key, vk in pynput_mapping.items():
            if key.upper() == n_upper:
                return vk

        # 特殊键（Tkinter 原始格式及简写）
        special = {
            "Print": 0x2C,
            "PrintScreen": 0x2C,
            "PrtSc": 0x2C,
            "SysRq": 0x2C,
            "Tab": 0x09,
            "Escape": 0x1B,
            "space": 0x20,
            "Cancel": 0x13,     # Pause/Break (Tkinter 返回 Cancel)
            "Pause": 0x13,      # Pause/Break
            "Scroll_Lock": 0x91,
            "ScrLk": 0x91,       # Scroll Lock
            "Back": 0x08,       # Backspace (退格键)
            "BackSpace": 0x08,  # Backspace
            "Return": 0x0D,     # Enter (回车键)
            "Enter": 0x0D,      # Enter
            "Insert": 0x2D,     # Insert
            "Ins": 0x2D,        # Insert
            "Delete": 0x2E,     # Delete
            "Del": 0x2E,        # Delete
            "Prior": 0x21,      # Page Up
            "PgUp": 0x21,       # Page Up
            "Page_Up": 0x21,    # Page Up
            "Next": 0x22,       # Page Down
            "PgDn": 0x22,       # Page Down
            "Page_Down": 0x22,  # Page Down
        }
        if n in special:
            return special[n]
        # 大写匹配
        for key, vk in special.items():
            if key.upper() == n_upper:
                return vk

        return None

    def _format_hotkey_string(mods: set, key_name: str) -> str:
        # 特殊键的显示名称简写映射
        display_name_map = {
            "Cancel": "Pause",      # Pause/Break (Tkinter 返回 Cancel)
            "Pause": "Pause",
            "BackSpace": "Back",
            "Tab": "Tab",
            "Return": "Enter",
            "Escape": "Esc",
            "Insert": "Ins",
            "Delete": "Del",
            "Home": "Home",
            "End": "End",
            "Prior": "PgUp",
            "Next": "PgDn",
            "Page_Up": "PgUp",
            "Page_Down": "PgDn",
            "Left": "Left",
            "Up": "Up",
            "Right": "Right",
            "Down": "Down",
            "Print": "PrtSc",
            "PrintScreen": "PrtSc",
            "Sys_Req": "PrtSc",
            "Scroll_Lock": "ScrLk",
            "Num_Lock": "NumLk",
            "Caps_Lock": "CapsLk",
            "ScrLk": "ScrLk",
            "NumLk": "NumLk",
            "CapsLk": "CapsLk",
        }

        parts = []
        if "Ctrl" in mods:
            parts.append("Ctrl")
        if "Alt" in mods:
            parts.append("Alt")
        if "Shift" in mods:
            parts.append("Shift")
        if "Win" in mods:
            parts.append("Win")

        # 字母按键只在单字符时大写，优先匹配特殊键映射（支持不同大小写）
        special_display = None
        upper_key = key_name.upper()
        for tk_key, display in display_name_map.items():
            if tk_key.upper() == upper_key:
                special_display = display
                break

        if special_display:
            key_name = special_display
        elif len(key_name) == 1 and key_name.isalpha():
            key_name = key_name.upper()

        parts.append(key_name)
        return "+".join(parts)

    def _parse_hotkey_string(s: str):
        """
        将类似 'Ctrl+Alt+S' 的字符串解析为 (mod_flags, vk, display_str)。
        解析失败返回 None。
        """
        if not s:
            return None
        tokens = [p.strip() for p in s.split("+") if p.strip()]
        if not tokens:
            return None

        mods_set = set()
        key_token = None
        for t in tokens:
            upper = t.upper()
            if upper in ("CTRL", "CONTROL"):
                mods_set.add("Ctrl")
            elif upper == "ALT":
                mods_set.add("Alt")
            elif upper == "SHIFT":
                mods_set.add("Shift")
            elif upper in ("WIN", "WINKEY", "WINDOWS"):
                mods_set.add("Win")
            else:
                key_token = t

        if not key_token:
            return None

        vk = _keysym_to_vk(key_token)
        if vk is None:
            print(f"无法将 '{key_token}' 转换为虚拟键码")
            return None

        mod_flags = 0
        if "Ctrl" in mods_set:
            mod_flags |= MOD_CONTROL
        if "Alt" in mods_set:
            mod_flags |= MOD_ALT
        if "Shift" in mods_set:
            mod_flags |= MOD_SHIFT
        if "Win" in mods_set:
            mod_flags |= MOD_WIN

        display = _format_hotkey_string(mods_set, key_token)
        return mod_flags, vk, display

    class GlobalHotkeyManager:
        """使用 RegisterHotKey 注册全局快捷键，收到后触发回调。

        注意：RegisterHotKey 必须在与 GetMessage/PeekMessage 相同的线程调用，
        否则 WM_HOTKEY 消息不会被我们自己的消息循环收到。
        这里专门启一个后台线程，在线程内部完成：
          - 注册/反注册全局快捷键
          - 运行消息循环并监听 WM_HOTKEY
        """

        def __init__(self, root, callback):
            self.root = root
            self.callback = callback
            self._id = 1
            # (cmd, (mod_flags, vk, display_str))
            self._cmd_queue: "queue.Queue[tuple[str, tuple[int, int, str]]]" = queue.Queue()
            self._thread: threading.Thread | None = None

        def _notify_register_failed(self, display: str):
            """在 Tk 主线程中提示用户注册全局快捷键失败。"""
            def _show():
                try:
                    _show_message(
                        "✂️全局快捷键",
                        f"无法注册全局快捷键：{display}\n\n"
                        "可能已被系统或其他程序占用，请在“设置快捷键”里换一个组合键，例如 Ctrl+Alt+S",
                    )
                except Exception:
                    # 即便弹窗失败，也至少打印一条日志
                    print(f"全局快捷键注册失败：{display}")

            try:
                self.root.after(0, _show)
            except Exception:
                print(f"全局快捷键注册失败：{display}")

        def _ensure_thread(self):
            if self._thread is not None:
                return

            def _loop():
                user32 = ctypes.windll.user32
                msg = MSG()
                current_mod = 0
                current_vk = 0

                while True:
                    # 先处理来自主线程的指令（更新快捷键）
                    try:
                        while True:
                            cmd, data = self._cmd_queue.get_nowait()
                            if cmd == "update":
                                new_mod, new_vk, disp = data
                                # 先注销旧的
                                if current_mod or current_vk:
                                    try:
                                        user32.UnregisterHotKey(None, self._id)
                                    except Exception:
                                        pass

                                current_mod, current_vk = new_mod, new_vk

                                if current_vk:
                                    ok = user32.RegisterHotKey(
                                        None, self._id, current_mod, current_vk
                                    )
                                    if not ok:
                                        # 通知主线程：注册失败，提示用户更换组合键
                                        self._notify_register_failed(disp)
                    except queue.Empty:
                        pass

                    # 等待并处理该线程消息队列中的 WM_HOTKEY
                    # 使用 MsgWaitForMultipleObjects 减少 CPU 占用并支持队列事件
                    ret = user32.MsgWaitForMultipleObjects(0, None, 0, 100, 0x0001)  # QS_ALLINPUT
                    if ret == 0xFFFFFFFF:
                        continue

                    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                        # 必须翻译和分发消息，否则消息循环可能不正常工作
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
                        # 在 DispatchMessage 之后检查 WM_HOTKEY 消息
                        if msg.message == WM_HOTKEY and msg.wParam == self._id:
                            # 切回 Tk 主线程执行截图逻辑
                            try:
                                self.root.after(0, self.callback)
                            except Exception:
                                pass

            self._thread = threading.Thread(target=_loop, daemon=True)
            self._thread.start()

        def update(self, mod_flags: int, vk: int, display_str: str | None = None):
            """更新当前注册的全局快捷键。

            display_str 用于在注册失败时向用户展示，例如 'Ctrl+PrtSc'。
            """
            if not sys.platform.startswith("win"):
                return
            self._ensure_thread()
            if display_str is None:
                display_str = f"{mod_flags}+VK({vk})"
            # 把更新请求发送到热键线程，由它在自己的线程中调用 RegisterHotKey
            self._cmd_queue.put(("update", (mod_flags, vk, display_str)))

    # ----------------- 注册表读写快捷键信息 -----------------
    def _load_hotkey(default: str = "Ctrl+PrtSc") -> str:
        """从注册表读取全局快捷键字符串，不存在时返回默认值"""
        if not sys.platform.startswith("win"):
            return default
        try:
            import winreg  # type: ignore

            key_path = r"Software\ScreenshotTool"
            value_name = "CaptureHotkey"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
            value = (value or "").strip()
            print(f"从注册表读取的快捷键: '{value}'")
            return value or default
        except Exception as e:
            print(f"读取注册表异常: {e}，使用默认值")
            return default

    def _save_hotkey(hotkey: str) -> None:
        """把全局快捷键字符串写入注册表"""
        if not sys.platform.startswith("win"):
            return
        try:
            import winreg  # type: ignore

            key_path = r"Software\ScreenshotTool"
            value_name = "CaptureHotkey"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, hotkey.strip())
        except Exception:
            # 注册表异常不影响主功能
            pass

    # 全局快捷键管理器：触发行为与托盘“立即截图”一致
    hotkey_manager = GlobalHotkeyManager(root, lambda: app._on_capture_click())

    # 启动时按注册表中保存的内容注册全局快捷键
    default_hotkey = "Ctrl+PrtSc"
    initial_hotkey_str = _load_hotkey(default_hotkey)
    parsed = _parse_hotkey_string(initial_hotkey_str)
    if parsed is not None:
        mod_flags, vk, display_str = parsed
        # 使用统一的接口，确保后台线程正确启动，并在注册失败时给出提示
        hotkey_manager.update(mod_flags, vk, display_str)
        CURRENT_HOTKEY = display_str  # 更新全局变量
    else:
        # 解析失败时使用默认快捷键
        print(f"未能从注册表解析出有效的快捷键配置，使用默认: {default_hotkey}")
        parsed = _parse_hotkey_string(default_hotkey)
        if parsed is not None:
            mod_flags, vk, display_str = parsed
            hotkey_manager.update(mod_flags, vk, display_str)
            CURRENT_HOTKEY = display_str

    # ----------------- 设置快捷键界面（风格与主界面一致） -----------------

    def _show_hotkey_dialog():
        """弹出设置快捷键窗口：界面风格与主界面一致，通过按键捕获组合。"""
        # 如果对话框已存在，就把它提到前面并获取焦点，不再创建新的
        if app._hotkey_dialog is not None:
            try:
                app._hotkey_dialog.lift()
                app._hotkey_dialog.focus_force()
            except Exception:
                app._hotkey_dialog = None
            return

        if not sys.platform.startswith("win"):
            _show_message("✂️设置快捷键", "当前系统不支持全局快捷键设置（仅支持 Windows）")
            return

        # 创建完全独立的顶级窗口，不依赖 root
        # 这样即使 root 被隐藏，对话框也能正常显示
        dialog = tk.Toplevel()

        # 立即保存对话框引用，防止重复打开
        app._hotkey_dialog = dialog

        dialog.title("✂️设置快捷键")

        # 工具窗口样式（不在任务栏单独显示）
        try:
            dialog.attributes("-toolwindow", True)
        except Exception:
            pass

        bg_color = "#9DDEF7" #72eaff
        dialog.configure(bg=bg_color)

        # 确保对话框可见并置前
        def _ensure_visible():
            try:
                if not dialog.winfo_viewable():
                    dialog.deiconify()
                dialog.lift()
                dialog.focus_force()
            except Exception:
                pass

        # 延迟执行确保可见
        dialog.after(50, _ensure_visible)
        dialog.after(150, _ensure_visible)

        # 对话框关闭时，只清理对话框引用
        def on_dialog_close():
            # 卸载全局键盘钩子
            if hook_installed[0]:
                _uninstall_keyboard_hook()
                hook_installed[0] = False
            app._hotkey_dialog = None
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", on_dialog_close)

        # 使用 app 中已获取的缩放比例
        scale_factor = app._scale_factor
        # 基准尺寸（100% 缩放时）- 宽度固定，最小高度
        base_width = 255
        base_min_height = 145
        # 根据缩放比例计算实际尺寸
        width = int(base_width * scale_factor)

        # 计算缩放后的字体大小（保持比例）
        base_title_font = 14
        base_value_font = 13
        base_tip_font = 9
        base_btn_font = 13
        title_font_size = max(10, int(base_title_font))
        value_font_size = max(10, int(base_value_font))
        tip_font_size = max(8, int(base_tip_font))
        btn_font_size = max(9, int(base_btn_font))

        # 先设置一个临时高度，让控件能够正常布局
        temp_height = int(base_min_height * scale_factor)
        dialog.geometry(f"{width}x{temp_height}")
        dialog.withdraw()

        # 捕获键盘：通过按下/抬起事件维护当前修饰键状态，避免 Ctrl/Alt 等“粘住”现象
        pressed_mods: set[str] = set()   # 当前按着的修饰键（Ctrl/Alt/Shift/Win）
        selected_mods: set[str] = set()  # 本次选择的组合里包含的修饰键
        selected_key = [None]            # 本次选择的主键
        hook_installed = [False]         # 标记是否已安装全局钩子

        # 用于存储当前按下的修饰键状态（通过全局钩子检测）
        current_mods_from_hook: set[str] = set()

        frame = tk.Frame(dialog, bg=bg_color)
        frame.pack(fill=tk.BOTH, expand=True, padx=3, pady=6)

        title_label = tk.Label(
            frame,
            text="请按下新的快捷键组合",
            bg=bg_color,
            fg="#000520",
            font=("Microsoft YaHei UI", title_font_size, "bold"),
        )
        title_label.pack(pady=(0, 2))

        value_label = tk.Label(
            frame,
            text="当前：",
            bg=bg_color,
            fg="#053D97",
            font=("Microsoft YaHei UI", value_font_size, "bold"),
        )
        value_label.pack(pady=(0, 2))

        tip_label = tk.Label(
            frame,
            text="点击“确定”保存。Esc 取消",
            bg=bg_color,
            fg="black",
            font=("Microsoft YaHei UI", tip_font_size),
            justify="left",
        )
        tip_label.pack()

        # 显示当前已保存的快捷键
        current = _load_hotkey()
        value_label.config(text=f"当前:{current}")

        def _update_label():
            if selected_key[0]:
                disp = _format_hotkey_string(selected_mods, selected_key[0])
                value_label.config(text=f"新按键:{disp}")
            else:
                value_label.config(text=f"当前:{current}")

        def on_key_press(event):
            kn = event.keysym
            kc = event.keycode  # 使用 keycode 作为备用识别方式

            # keycode 到 keysym 的映射（用于 keysym 无法识别的特殊键）
            # PrintScreen 的 keycode 在 Tkinter 中通常是 44
            # Scroll Lock 返回 keysym=Cancel, keycode=145
            # Pause/Break 返回 keysym=Cancel, keycode=3
            # 注意：Windows 会将 Ctrl+Scroll_Lock 转换为 Ctrl+Pause，所以需要检测 VK_SCROLL 来区分
            keycode_to_keysym = {
                44: "PrintScreen",  # PrtSc / SysRq
                145: "ScrLk",       # Scroll Lock
                3: "Pause",         # Pause/Break
            }

            # 如果 keysym 为空或无法识别，尝试使用 keycode 映射
            if not kn or kn == "?" or (kn == "Cancel" and kc in keycode_to_keysym):
                # 特殊情况：Ctrl+Scroll_Lock 会被 Windows 转换为 Ctrl+Pause
                # 需要检测 Scroll Lock 键的实际状态
                if kc == 3:  # Pause keycode
                    # VK_SCROLL = 0x91
                    scroll_state = windll.user32.GetAsyncKeyState(0x91)
                    if scroll_state & 0x8000:  # Scroll Lock is pressed
                        kn = "ScrLk"
                        kc = 145
                    else:
                        kn = "Pause"
                else:
                    kn = keycode_to_keysym.get(kc, "")

            upper = kn.upper() if kn else ""
            # Esc 取消并关闭对话框
            if upper in ("ESC", "ESCAPE"):
                on_cancel()
                return

            # 先处理修饰键本身的按下
            if "SHIFT" in upper:
                pressed_mods.add("Shift")
                return
            if "CONTROL" in upper:
                pressed_mods.add("Ctrl")
                return
            if "ALT" in upper:
                pressed_mods.add("Alt")
                return
            if upper in ("LWIN", "RWIN", "SUPER_L", "SUPER_R", "WIN"):
                pressed_mods.add("Win")
                return

            # 非修饰键按下时，记录一次“快照”：当前有哪些修饰键 + 这次按下的主键
            selected_mods.clear()
            selected_mods.update(pressed_mods)
            selected_key[0] = kn.upper() if kn.isalpha() else kn
            _update_label()

        def on_key_release(event):
            """按键释放时，清除对应的修饰键状态"""
            kn = event.keysym
            upper = kn.upper()
            if "SHIFT" in upper:
                pressed_mods.discard("Shift")
            elif "CONTROL" in upper:
                pressed_mods.discard("Ctrl")
            elif "ALT" in upper:
                pressed_mods.discard("Alt")
            elif upper in ("LWIN", "RWIN", "SUPER_L", "SUPER_R", "WIN"):
                pressed_mods.discard("Win")

        def on_ok():
            if not selected_key[0]:
                _show_message("✂️设置快捷键", "未更改新的快捷键", "warning", dialog)
                return
            
            vk = _keysym_to_vk(selected_key[0])
            if vk is None:
                _show_message(
                    "✂️设置快捷键",
                    f"不支持的按键:{selected_key[0]}，请尝试其他按键",
                    "error",
                    dialog,
                )
                return

            mod_flags = 0
            if "Ctrl" in selected_mods:
                mod_flags |= MOD_CONTROL
            if "Alt" in selected_mods:
                mod_flags |= MOD_ALT
            if "Shift" in selected_mods:
                mod_flags |= MOD_SHIFT
            if "Win" in selected_mods:
                mod_flags |= MOD_WIN

            disp = _format_hotkey_string(selected_mods, selected_key[0])
            _save_hotkey(disp)
            hotkey_manager.update(mod_flags, vk, disp)
            global CURRENT_HOTKEY
            CURRENT_HOTKEY = disp  # 更新全局变量

            print(f"快捷键已更新：{disp}")
            _show_message("✂️设置快捷键", f"新快捷键已生效:{disp}", "info", dialog)

            # 更新按钮和托盘菜单显示
            app.capture_btn.config(text=f"截图{disp}")
            root.after(100, update_tray_menu)

            # 卸载全局键盘钩子
            if hook_installed[0]:
                _uninstall_keyboard_hook()
                hook_installed[0] = False

            dialog.destroy()
            app._hotkey_dialog = None

        def on_cancel(event=None):
            """取消关闭对话框"""
            # 卸载全局键盘钩子
            if hook_installed[0]:
                _uninstall_keyboard_hook()
                hook_installed[0] = False
            dialog.destroy()
            app._hotkey_dialog = None

        # 绑定 Tkinter 键盘事件
        dialog.bind("<Key>", on_key_press)
        dialog.bind("<KeyRelease>", on_key_release)
        dialog.focus_set()

        # 安装全局键盘钩子（用于捕获 PrtSc 等 Tkinter 无法识别的组合键）
        def on_global_key_event(event):
            """全局键盘钩子回调函数（使用 keyboard 库）"""
            # 只处理按键按下事件
            if event.event_type != 'down':
                return

            key_name = event.name.lower() if event.name else ""

            # 检测修饰键状态（使用 GetAsyncKeyState 获取实时状态）
            is_ctrl = (windll.user32.GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0
            is_alt = (windll.user32.GetAsyncKeyState(VK_MENU) & 0x8000) != 0
            is_shift = (windll.user32.GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0
            is_win = (windll.user32.GetAsyncKeyState(VK_LWIN) & 0x8000) != 0 or (windll.user32.GetAsyncKeyState(VK_RWIN) & 0x8000) != 0

            # 更新当前修饰键状态
            current_mods_from_hook.clear()
            if is_ctrl:
                current_mods_from_hook.add("Ctrl")
            if is_alt:
                current_mods_from_hook.add("Alt")
            if is_shift:
                current_mods_from_hook.add("Shift")
            if is_win:
                current_mods_from_hook.add("Win")

            # 检测 PrintScreen 键 (keyboard 库中名称为 'print screen')
            if key_name == 'print screen' or key_name == 'prtsc' or key_name == 'sys req':
                print(f"[全局钩子] 捕获到 PrtSc, 修饰键: Ctrl={is_ctrl} Alt={is_alt} Shift={is_shift} Win={is_win}")
                # 组合使用钩子检测到的修饰键
                selected_mods.clear()
                selected_mods.update(current_mods_from_hook)
                selected_key[0] = "PrintScreen"
                # 在主线程中更新标签
                dialog.after(0, _update_label)
                print(f"[全局钩子] 捕获到 PrintScreen 组合键: {selected_mods}")

        # 安装全局钩子
        if sys.platform.startswith("win"):
            hook_installed[0] = _install_keyboard_hook(on_global_key_event)
            if hook_installed[0]:
                print("[全局钩子] 已安装，用于捕获特殊按键组合")
            else:
                print("[全局钩子] 安装失败，将只使用 Tkinter 键盘事件")

        btn_frame = tk.Frame(frame, bg=bg_color)
        btn_frame.pack(pady=(5, 6))

        btn_ok = tk.Button(
            btn_frame,
            text="确 定",
            font=("Microsoft YaHei UI", btn_font_size, "bold"),
            bg="#3498db",
            fg="white",
            activebackground="#0099f9",
            activeforeground="#81FFC0",
            cursor="hand2",
            width=8,
            borderwidth=0,highlightthickness=0,padx=0,pady=0,
            command=on_ok,
        )
        btn_ok.pack(side=tk.LEFT, padx=5)

        btn_cancel = tk.Button(
            btn_frame,
            text="取 消",
            font=("Microsoft YaHei UI", btn_font_size, "bold"),
            bg="#3498db",
            fg="white",
            activebackground="#0099f9",
            activeforeground="#81FFC0",
            cursor="hand2",
            width=8,
            borderwidth=0,highlightthickness=0,padx=0,pady=0,
            command=on_cancel,
        )
        btn_cancel.pack(side=tk.LEFT, padx=5)

        # ==================== 根据控件实际大小自动调整窗口高度 ====================
        dialog.update_idletasks()

        # 获取控件实际需要的最小高度
        req_height = dialog.winfo_reqheight()

        # 实际高度取最小需要高度（但不超过临时高度）
        actual_height = min(req_height, temp_height)

        # 获取屏幕尺寸并计算居中位置
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        x = (sw - width) // 2
        y = (sh - actual_height) // 2
        dialog.geometry(f"{width}x{actual_height}+{x}+{y}")

        # 设置好位置后再显示
        dialog.deiconify()

        # 仅打开时置前并获取一次焦点，不抢其他窗口焦点（不用 grab_set）
        dialog.lift()
        dialog.focus_force()

    def on_tray_set_hotkey(icon, item):
        """托盘菜单：设置全局快捷键（写入注册表并注册热键）"""
        def _open_dialog():
            try:
                _show_hotkey_dialog()
            except Exception:
                import traceback
                traceback.print_exc()

        # 切回 Tk 主线程
        root.after(0, _open_dialog)

    # 创建系统托盘图标
    def show_main_window():
        """显示主界面"""
        if not bool(root.state() == 'normal'):
            root.deiconify()
        # 用户明确通过托盘菜单显示了程序界面，
        # 之后每次截图完成/取消/出错时都恢复主窗口
        app.restore_after_capture = True
        try:
            root.attributes('-alpha', 0.8)
        except Exception:
            pass
        root.lift()
        root.attributes('-topmost', True)

    def on_tray_capture(icon, item):
        """托盘截图（菜单项默认动作，也用于左键点击）"""
        # 在 Tk 主线程中调用截图逻辑
        root.after(0, app._on_capture_click)

    def on_tray_show(icon, item):
        """显示程序界面"""
        root.after(0, show_main_window)

    def on_tray_toggle_auto_save(icon, item):
        """切换自动保存状态"""
        def toggle():
            app.auto_save.set(not app.auto_save.get())
            app._on_auto_save_changed()
        root.after(0, toggle)

    def _get_autostart_command() -> str:
        """
        返回写入 Windows Run 注册表的启动命令。

        - PyInstaller 打包后：使用 exe 路径
        - 源码运行：使用 python.exe + 当前脚本路径
        """
        if getattr(sys, "frozen", False):
            exe_path = os.path.abspath(sys.executable)
            return f'"{exe_path}"'

        python_exe = os.path.abspath(sys.executable)
        script_path = os.path.abspath(sys.argv[0] if sys.argv else __file__)
        return f'"{python_exe}" "{script_path}"'

    def _is_windows_autostart_enabled() -> bool:
        if not sys.platform.startswith("win"):
            return False
        try:
            import winreg  # type: ignore

            run_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            value_name = "ScreenshotTool"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key_path, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, value_name)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            # 权限/注册表异常时，不影响主功能
            return False

    def _set_windows_autostart(enabled: bool) -> None:
        if not sys.platform.startswith("win"):
            return
        try:
            import winreg  # type: ignore

            run_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            value_name = "ScreenshotTool"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key_path) as key:
                if enabled:
                    winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, _get_autostart_command())
                else:
                    try:
                        winreg.DeleteValue(key, value_name)
                    except FileNotFoundError:
                        pass
        except OSError:
            # 权限/注册表异常时，不影响主功能
            pass

    def on_tray_toggle_autostart(icon, item):
        """切换开机自启"""
        def toggle():
            _set_windows_autostart(not _is_windows_autostart_enabled())
        root.after(0, toggle)

    def on_tray_exit(icon, item):
        """退出程序"""
        icon.visible = False
        icon.stop()
        root.after(0, root.destroy)

    # 加载托盘图标（优先从打包进 EXE 的资源中加载）
    def load_tray_image():
        try:
            icon_path = _resource_path("JT.ico")
            if os.path.exists(icon_path):
                return Image.open(icon_path)
        except Exception:
            pass
        # 兜底：生成一个简单的小图片，避免因图标问题导致程序崩溃
        img = Image.new('RGB', (64, 64), color=(0, 180, 255))
        return img

    # 更新托盘菜单（显示当前快捷键）
    def update_tray_menu():
        global CURRENT_HOTKEY
        capture_text = f'截图{CURRENT_HOTKEY}'
        menu = pystray.Menu(
            pystray.MenuItem(capture_text, on_tray_capture, default=True),
            pystray.MenuItem('显示程序界面', on_tray_show),
            pystray.MenuItem(
                '设置快捷键',
                on_tray_set_hotkey,
                enabled=lambda item: sys.platform.startswith("win"),
            ),
            pystray.MenuItem(
                '自动保存',
                on_tray_toggle_auto_save,
                checked=lambda item: app.auto_save.get()
            ),
            pystray.MenuItem(
                '开机自启',
                on_tray_toggle_autostart,
                checked=lambda item: _is_windows_autostart_enabled(),
                enabled=lambda item: sys.platform.startswith("win"),
            ),
            pystray.MenuItem('退出程序', on_tray_exit)
        )
        tray_icon.menu = menu

    tray_image = load_tray_image()
    tray_icon = pystray.Icon("快捷截图", tray_image, "快捷截图")

    # 初始化托盘菜单
    root.after(100, update_tray_menu)

    # 在单独的线程中运行托盘事件循环，避免阻塞 Tk 主循环
    def run_tray():
        tray_icon.run()

    tray_thread = threading.Thread(target=run_tray, daemon=True)
    tray_thread.start()

    root.mainloop()


if __name__ == "__main__":
    main()
