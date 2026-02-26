"""
快捷截图 - 主界面类
"""
import tkinter as tk
import os
import sys
import ctypes
import threading

import mss
from PIL import Image
import pystray

from capture_overlay import CaptureOverlay
from utils import save_screenshot, copy_to_clipboard


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

        # 使用工具窗口样式，只保留关闭按钮（隐藏最小化和最大化）
        self.root.attributes('-toolwindow', True)
        self.root.attributes('-topmost', True)  # 始终置顶
        # try:
        #     # self.root.iconbitmap('JT.ico') # 设置窗口图标
        #     icon = tk.PhotoImage(file='JT.png')  # 加载图标文件,iconbitmap加载有问题
        #     self.root.iconphoto(True, icon)
        #     # self.root._icon = icon  # 防止被垃圾回收
        # except:
        #     pass

    def _adjust_window_geometry(self):
        """根据控件实际大小和屏幕分辨率，自动调整窗口大小和位置

        这样在 125% / 150% 缩放、高 DPI 显示器上，文字不会被挤压或裁剪。
        """
        # 先让 Tk 计算所有控件所需的最小大小
        self.root.update_idletasks()

        req_width = self.root.winfo_reqwidth()
        req_height = self.root.winfo_reqheight()

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # 宽度：以控件需要的宽度为主，略微增加一点边距即可，让整体更紧凑
        win_width = max(req_width + 10, 110)
        win_width = min(win_width, 170)

        # 高度直接用控件实际需要的高度，保证文字不被裁剪
        win_height = req_height

        # 位置：右上角，预留出一点距离
        win_x = screen_width - win_width * 2
        win_y = screen_height // 10

        self.root.geometry(f"{win_width}x{win_height}+{win_x}+{win_y}")


    def _create_widgets(self):
        """创建界面元素"""
        # 设置背景色
        bg_color = '#3ee0f5'
        self.root.configure(bg=bg_color)

        # 主框架
        main_frame = tk.Frame(self.root, bg=bg_color)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 按钮统一颜色
        btn_bg = '#3498db'  # 按钮初始颜色
        btn_fg = 'white'      # 字体颜色
        btn_checked_bg = '#0059e8'  # 勾选后深蓝色


        # 截图按钮(高度扩大1.5倍)
        self.capture_btn = tk.Button(
            main_frame,
            text="点击截图",
            font=('Microsoft YaHei UI', 13, 'bold'),
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
        self.capture_btn.pack(fill=tk.X, pady=(0, 3))

        # 自动保存复选框
        cb_frame = tk.Frame(main_frame, bg=bg_color)
        cb_frame.pack(fill=tk.X)

        self.auto_save_cb = tk.Checkbutton(
            cb_frame,
            text="自动保存",
            variable=self.auto_save,
            font=('Microsoft YaHei UI', 11, 'bold'),
            bg=btn_bg,
            fg=btn_fg,
            selectcolor=btn_checked_bg,
            activebackground=btn_bg,
            activeforeground=btn_fg,
            cursor='hand2',
            relief=tk.FLAT,
            command=self._on_auto_save_changed,# 绑定事件
            indicatoron=False,# 不显示默认的勾
            width=150,
        )
        self.auto_save_cb.pack()

    def _on_auto_save_changed(self):
        """自动保存状态改变"""
        if self.auto_save.get():
            self.capture_btn.config(bg='#0059e8')
        else:
            self.capture_btn.config(bg='#3498db')  # 恢复初始颜色

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
            overlay = CaptureOverlay(self.root, on_capture, on_cancel, base_image)
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
        # messagebox.showerror("错误", f"截图失败：{error_msg}")

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


def main():
    """主函数"""
    _set_dpi_awareness()
    root = tk.Tk()
    # 启动时不显示主界面，只在系统托盘中显示图标
    root.withdraw()

    # 创建应用（窗口位置会在初始化时设置好）
    app = ScreenshotApp(root)

    # 关闭按钮只隐藏窗口，不退出程序（保留托盘图标继续工作）
    def on_close():
        root.withdraw()
        # 用户主动关闭主窗口后，不再在截图完成时自动恢复主界面
        app.restore_after_capture = False

    root.protocol("WM_DELETE_WINDOW", on_close)

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

    def on_tray_exit(icon, item):
        """退出程序"""
        icon.visible = False
        icon.stop()
        root.after(0, root.destroy)

    # 右键菜单：第一个菜单项为默认项，双击或左键激活
    menu = pystray.Menu(
        pystray.MenuItem('立即截图', on_tray_capture, default=True),
        pystray.MenuItem('显示程序界面', on_tray_show),
        pystray.MenuItem(
            '自动保存',
            on_tray_toggle_auto_save,
            checked=lambda item: app.auto_save.get()
        ),
        pystray.MenuItem('退出程序', on_tray_exit)
    )

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

    tray_image = load_tray_image()
    tray_icon = pystray.Icon("快捷截图", tray_image, "快捷截图", menu)

    # 在单独的线程中运行托盘事件循环，避免阻塞 Tk 主循环
    def run_tray():
        tray_icon.run()

    tray_thread = threading.Thread(target=run_tray, daemon=True)
    tray_thread.start()

    root.mainloop()


if __name__ == "__main__":
    main()
