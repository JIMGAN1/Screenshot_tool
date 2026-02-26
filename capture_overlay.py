"""
快捷截图 - 框选覆盖层模块
"""
import tkinter as tk
from PIL import Image, ImageGrab, ImageTk
import mss
import pyperclip
import win32gui
import win32con
import win32process
import os
import ctypes


class CaptureOverlay:
    """全屏框选覆盖层"""

    def __init__(self, parent, on_capture, on_cancel=None, base_image=None):
        """
        初始化覆盖层
        :param parent: 父窗口
        :param on_capture: 截图完成回调函数 (image, bbox)
        :param on_cancel: 取消回调函数
        :param base_image: 预先截取的“干净”全屏图像（PIL.Image），用于放大镜/取色，没有则使用 mss 实时截取
        """
        self.parent = parent
        self.on_capture = on_capture
        self.on_cancel = on_cancel

        # 创建全屏窗口
        self.root = tk.Toplevel(parent)
        # 使用无边框顶层窗口，避免在任务栏生成单独按钮（配合手动铺满全屏）
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.configure(cursor='cross')
        # 手动设置为全屏大小
        # 这里窗口本身的高度可以使用 Tk 的 screenheight（工作区高度也没关系），
        # 真正用于“桌面全屏截图”的尺寸，后续统一使用预截图/系统分辨率来计算。
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.root.geometry(f"{screen_w}x{screen_h}+0+0")
        # 稍微提高整体不透明度，让提示文字/高亮更清晰，但仍保持半透明遮罩
        self.root.attributes('-alpha', 0.3)

        # 创建后尽量将覆盖层设置为前台窗口，确保 ESC 等按键事件发送到本窗口
        try:
            overlay_hwnd = self.root.winfo_id()
            try:
                win32gui.SetForegroundWindow(overlay_hwnd)
            except Exception:
                # 如果被系统策略拦截则忽略
                pass
        except Exception:
            pass

        # 预先截好的一整张屏幕图像：用于放大镜和实时取色，避免半透明遮罩影响颜色，
        # 同时也作为“真实整屏尺寸”（包含任务栏）的依据。
        self.fullscreen_image = base_image

        # 截图区域变量
        self.start_x = None
        self.start_y = None
        self.end_x = None
        self.end_y = None
        self.rect_id = None
        self.current_x = 0
        self.current_y = 0
        self.current_color = "#FFFFFF"#初始化为白色
        self.window_rect = None  # 当前窗口区域
        self.window_rect_id = None  # 窗口区域矩形ID
        self.window_hwnd = None  # 当前窗口句柄（用于调试窗口/客户区差异）
        # 是否当前命中的是“桌面全屏”（用于单击桌面时全屏截图，包含任务栏）
        self.is_desktop_fullscreen = False

        # 放大器尺寸（略放大，提升可读性）
        self.magnifier_size = 120  # 放大显示尺寸
        self.pixel_size = 5

        # 创建全屏遮罩
        self.canvas = tk.Canvas(
            self.root,
            bg='black',
            highlightthickness=0,
            cursor='cross'
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 绑定事件
        # 使用 bind_all 确保在放大镜等子窗口获得焦点时，ESC 也能生效
        self.root.bind_all('<Escape>', self._on_escape)  # ESC 取消（全局）
        self.root.bind('<Button-3>', self._on_right_click)  # 右键取消截图 + 复制信息
        self.canvas.bind('<Button-1>', self._on_mouse_down)
        self.canvas.bind('<B1-Motion>', self._on_mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_mouse_up)
        # 使用全局 Motion 绑定，让鼠标移动时即使在放大镜窗口上方也能持续更新
        self.root.bind_all('<Motion>', self._on_mouse_move)

        # 检查是否有鼠标按下（用于区分单击和拖动）
        self._has_dragged = False

        # 顶部提示文字（半透明窗口上方，不影响截图结果）
        self.tip_label = tk.Label(
            self.root,
            text="单击或拖动左键截图 | 右键复制坐标和色值 | 按Esc取消",
            bg="#000000",
            fg="#ffffff",#
            font=("Microsoft YaHei UI", 25, "bold")
        )
        self.tip_label.place(relx=0.5, y=5, anchor="n")

        # 创建放大器
        self._create_magnifier()

    def _create_magnifier(self):
        """创建放大器窗口"""
        # 放大镜单独作为顶层窗口挂在主窗口之上
        self.magnifier_window = tk.Toplevel(self.parent)
        self.magnifier_window.overrideredirect(True)
        self.magnifier_window.attributes('-topmost', True)
        self.magnifier_window.configure(bg='#1a1a2e')

        # 让放大镜窗口也响应 ESC 取消（防止焦点在此窗口时 ESC 无效）
        self.magnifier_window.bind('<Escape>', self._on_escape)

        # 主框架
        main_frame = tk.Frame(self.magnifier_window, bg='#1a1a2e')
        main_frame.pack()

        # 放大镜画布
        self.magnifier_canvas = tk.Canvas(
            main_frame,
            width=self.magnifier_size,
            height=self.magnifier_size,
            bg='white',
            highlightthickness=1,
            highlightbackground='#444'
        )
        self.magnifier_canvas.pack()

        # 添加中心十字线
        center = self.magnifier_size // 2
        self.magnifier_canvas.create_line(
            0, center, self.magnifier_size, center,
            fill='#00aaff', width=1, tags='center'
        )
        self.magnifier_canvas.create_line(
            center, 0, center, self.magnifier_size,
            fill='#00aaff', width=1, tags='center'
        )

        # 在放大镜画布上显示坐标（左上角）和色值（右上角）
        self.coord_text_id = self.magnifier_canvas.create_text(
            4,
            9,
            anchor=tk.W,
            text="0|0",
            fill="#00bbff",
            font=("Microsoft YaHei UI", 8, "bold")
        )
        self.color_text_id = self.magnifier_canvas.create_text(
            4,
            23,
            anchor=tk.W,
            text="#000000",
            fill="#00bbff",
            font=("Microsoft YaHei UI", 8, "bold")
        )

    def _update_magnifier(self, x, y):
        """更新放大器显示"""
        try:
            # 转换为屏幕绝对坐标
            screen_x = self.root.winfo_rootx() + x
            screen_y = self.root.winfo_rooty() + y
            screen_w = self.root.winfo_screenwidth()
            capture_size = 20

            # 如果有预先截好的整屏图，就从那张图里裁切，完全不受遮罩影响
            if self.fullscreen_image is not None:
                left = max(0, screen_x - capture_size // 2)
                top = max(0, screen_y - capture_size // 2)
                right = left + capture_size
                bottom = top + capture_size

                # 边界裁剪
                right = min(right, self.fullscreen_image.width)
                bottom = min(bottom, self.fullscreen_image.height)
                left = max(0, right - capture_size)
                top = max(0, bottom - capture_size)

                img = self.fullscreen_image.crop((left, top, right, bottom))
            else:
                pass
                # 兜底逻辑：没有预截图时，仍然用 mss 实时截取 颜色不准弃用
                # with mss.mss() as sct:
                #     monitor = {
                #         "left": max(0, screen_x - capture_size // 2),
                #         "top": max(0, screen_y - capture_size // 2),
                #         "width": capture_size,
                #         "height": capture_size
                #     }
                #     screenshot = sct.grab(monitor)
                #     img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

            # 放大图像
            img = img.resize((self.magnifier_size, self.magnifier_size), Image.NEAREST)

            # 转换为 PhotoImg
            photo = ImageTk.PhotoImage(img)

            # 更新画布
            self.magnifier_canvas.delete("magnified")
            self.magnifier_canvas.create_image(0, 0, anchor=tk.NW, image=photo, tag="magnified")
            self.magnifier_canvas.image = photo  # 保持引用

            # 确保十字线和文本始终在最上层
            self.magnifier_canvas.tag_raise('center')
            self.magnifier_canvas.tag_raise(self.coord_text_id)
            self.magnifier_canvas.tag_raise(self.color_text_id)

            # 更新坐标和颜色显示（绘制在放大镜画布上）
            coord_text = f"{screen_x}|{screen_y}"
            self.magnifier_canvas.itemconfig(self.coord_text_id, text=coord_text)
            self.magnifier_canvas.itemconfig(self.color_text_id, text=self.current_color)

            # 更新放大器位置（鼠标左上角，如被遮挡则显示右下角）
            offset_x = 15
            offset_y = 15

            # 默认显示在鼠标左上角（坐标色值在放大器左上角，所以整体下移）
            mag_x = x - offset_x - self.magnifier_size - 3
            mag_y = y - offset_y - self.magnifier_size - 3

            # 检查是否超出左边界或上边界
            if mag_x < 0:
                # 显示在鼠标右下角
                mag_x = x + offset_x
            if mag_y < 0:
                # 显示在鼠标右下角
                mag_y = y + offset_y

            # 确保不超出右边界
            if mag_x + self.magnifier_size > screen_w:
                mag_x = x - self.magnifier_size - offset_x

            # 将画布坐标转换为屏幕坐标后再移动放大镜窗口
            win_x = self.root.winfo_rootx() + mag_x
            win_y = self.root.winfo_rooty() + mag_y
            self.magnifier_window.geometry(f"+{win_x}+{win_y}")

        except Exception as e:
            print(f"更新放大器失败：{e}")

    def _get_pixel_color(self, x, y):
        """获取像素颜色（使用屏幕绝对坐标）"""
        try:
            # 转换为屏幕绝对坐标
            screen_x = self.root.winfo_rootx() + x
            screen_y = self.root.winfo_rooty() + y

            # 如果有预先截好的整屏图，优先使用这张图来取色，完全不受遮罩影响
            if self.fullscreen_image is not None:
                if (
                    0 <= screen_x < self.fullscreen_image.width
                    and 0 <= screen_y < self.fullscreen_image.height
                ):
                    r, g, b = self.fullscreen_image.getpixel((screen_x, screen_y))
                    return f"#{r:02X}{g:02X}{b:02X}"

            # 没有预截图时，直接使用 mss 截取 1 像素进行取色，避免窗口 DC 相关复杂逻辑
            try:
                with mss.mss() as sct:
                    monitor = {
                        "left": screen_x,
                        "top": screen_y,
                        "width": 1,
                        "height": 1,
                    }
                    screenshot = sct.grab(monitor)
                    pixel = screenshot.pixel(0, 0)
                    r_raw, g_raw, b_raw = pixel[0], pixel[1], pixel[2]
                    return f"#{r_raw:02X}{g_raw:02X}{b_raw:02X}"
            except Exception:
                return "#000000"
        except Exception:
            return "#000000"

    def _copy_info(self):
        """复制坐标和颜色到剪切板"""
        info = f"{self.current_x}|{self.current_y}\n{self.current_color}"
        pyperclip.copy(info)
        print(f"已复制到剪切板：{info}")

    def _on_right_click(self, event):
        """右键取消截图并复制坐标和颜色"""
        self._copy_info()
        print("右键已复制并取消截图")
        self._cancel()

    def _on_escape(self, event):
        """ESC 键取消截图"""
        print("ESC 键已取消")
        self._cancel()

    def _on_mouse_down(self, event):
        """鼠标按下"""
        self.start_x = event.x
        self.start_y = event.y
        self.end_x = event.x
        self.end_y = event.y
        self._has_dragged = False  # 初始化拖动标志

        # 检查是否是单击（没有拖动过）
        if not self.window_rect:  # 如果没有显示窗口区域
            # 识别窗口区域
            window_rect = self._identify_window_under_cursor(event.x, event.y)

            # 如果识别到窗口区域，显示细框提示
            if window_rect:
                self.window_rect = window_rect
                # 绘制大小提示框
                self.window_rect_id = self.canvas.create_rectangle(
                    self.window_rect['left'], self.window_rect['top'],
                    self.window_rect['right'], self.window_rect['bottom'],
                    outline="#ffff00",
                    width=4,
                    tags='window_rect'
                )
            # 创建矩形作为拖动起始点
            self.rect_id = self.canvas.create_rectangle(
                self.start_x, self.start_y,
                self.end_x, self.end_y,
                outline='#00ff00',
                width=2,
                dash=(4, 2)
            )
        else:
            # 有窗口区域，显示矩形
            self.rect_id = self.canvas.create_rectangle(
                self.start_x, self.start_y,
                self.end_x, self.end_y,
                outline='#00ff00',
                width=2,
                dash=(4, 2)
            )

    def _on_mouse_drag(self, event):
        """鼠标拖动"""
        self._has_dragged = True  # 标记为已拖动
        if self.start_x is not None:
            # 如果开始拖动，清除窗口区域矩形
            if self.window_rect_id:
                self.canvas.delete(self.window_rect_id)
                self.window_rect_id = None

            self.end_x = event.x
            self.end_y = event.y

            # 更新矩形
            self.canvas.coords(
                self.rect_id,
                self.start_x, self.start_y,
                self.end_x, self.end_y
            )

    def _on_mouse_up(self, event):
        """鼠标释放"""
        if self.start_x is not None:
            self.end_x = event.x
            self.end_y = event.y

            # 确保 start < end
            x1 = min(self.start_x, self.end_x)
            y1 = min(self.start_y, self.end_y)
            x2 = max(self.start_x, self.end_x)
            y2 = max(self.start_y, self.end_y)

            # 如果是单击（没有拖动过）且有窗口区域，则截图整个窗口区域
            if not self._has_dragged and self.window_rect:
                self._do_capture(
                    self.window_rect['left'],
                    self.window_rect['top'],
                    self.window_rect['right'],
                    self.window_rect['bottom']
                )
            # 检查选区是否有效（拖动模式）
            elif x2 - x1 > 5 and y2 - y1 > 5:
                self._do_capture(x1, y1, x2, y2)
            else:
                self._cancel()

    def _on_mouse_move(self, event):
        """鼠标移动"""
        # 如果覆盖层或画布已经销毁（截图结束之后），直接忽略残余的全局 Motion 事件
        if not hasattr(self, "root") or not self.root.winfo_exists():
            return
        if not hasattr(self, "canvas") or not self.canvas.winfo_exists():
            return

        # 使用屏幕绝对坐标转换为覆盖层内部坐标，保证无论鼠标在何处（包括放大镜窗口上方），
        # 坐标计算都基于当前全屏覆盖层的位置
        try:
            canvas_x = event.x_root - self.root.winfo_rootx()
            canvas_y = event.y_root - self.root.winfo_rooty()
        except Exception:
            # 兜底：如果拿不到 root 位置，就直接使用事件自身坐标
            canvas_x = getattr(event, "x", 0)
            canvas_y = getattr(event, "y", 0)

        self.current_x = canvas_x
        self.current_y = canvas_y
        self.current_color = self._get_pixel_color(canvas_x, canvas_y)
        self._update_magnifier(canvas_x, canvas_y)

        # 仅在未拖动选择区域时，实时高亮当前鼠标所在窗口
        if not self._has_dragged:
            # 这里传入的是覆盖层坐标（canvas_x/canvas_y），而不是事件原始坐标
            window_rect = self._identify_window_under_cursor(canvas_x, canvas_y)

            if window_rect:
                # 更新缓存的窗口矩形
                self.window_rect = window_rect

                # 如果已经有窗口高亮矩形，则更新其坐标；否则创建新的
                if self.window_rect_id:
                    self.canvas.coords(
                        self.window_rect_id,
                        self.window_rect['left'],
                        self.window_rect['top'],
                        self.window_rect['right'],
                        self.window_rect['bottom'],
                    )
                else:
                    self.window_rect_id = self.canvas.create_rectangle(
                        self.window_rect['left'],
                        self.window_rect['top'],
                        self.window_rect['right'],
                        self.window_rect['bottom'],
                        outline='#ffff00',
                        width=4,
                        tags='window_rect',
                    )
            else:
                # 鼠标不在有效窗口上时，移除高亮矩形
                if self.window_rect_id:
                    self.canvas.delete(self.window_rect_id)
                    self.window_rect_id = None
                self.window_rect = None

    def _identify_window_under_cursor(self, x=None, y=None, run_id="pre-fix"):
        """识别鼠标当前应用窗口（排除自身窗口/任务栏），并返回覆盖层坐标下的矩形"""
        try:
            # 默认重置当前窗口句柄（避免残留旧值）
            self.window_hwnd = None
            # 默认认为不是桌面全屏，只有命中桌面兜底/桌面类窗口时才置为 True
            self.is_desktop_fullscreen = False
            # 使用传入的坐标（画布坐标），没有则退回当前记录的坐标
            if x is None or y is None:
                x = self.current_x
                y = self.current_y

            # 转换为屏幕绝对坐标（与 WindowFromPoint 使用的坐标系保持一致）
            screen_x = self.root.winfo_rootx() + x
            screen_y = self.root.winfo_rooty() + y

            overlay_hwnd = self.root.winfo_id()
            parent_hwnd = self.parent.winfo_id() if hasattr(self.parent, "winfo_id") else None
            mag_hwnd = (
                self.magnifier_window.winfo_id()
                if hasattr(self, "magnifier_window") and self.magnifier_window is not None
                else None
            )

            # 需要跳过的窗口句柄（自身覆盖层、主工具窗口、放大镜窗口）
            skip_hwnds = {h for h in (overlay_hwnd, parent_hwnd, mag_hwnd) if h}
            own_pid = os.getpid()

            # 获取屏幕尺寸（用于后续桌面全屏兜底）
            # 优先使用预先截好的整屏图尺寸，确保包含任务栏；否则再退回系统 API。
            if self.fullscreen_image is not None:
                screen_w = self.fullscreen_image.width
                screen_h = self.fullscreen_image.height
            else:
                try:
                    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
                    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
                except Exception:
                    screen_w = self.root.winfo_screenwidth()
                    screen_h = self.root.winfo_screenheight()

            # 获取任务栏矩形，用于避免把任务栏误识别为“桌面全屏”
            taskbar_rect = None
            try:
                taskbar_hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
                if taskbar_hwnd:
                    taskbar_rect = win32gui.GetWindowRect(taskbar_hwnd)
            except Exception:
                taskbar_rect = None

            # 不再直接依赖 WindowFromPoint 的结果（它会命中覆盖层本身），
            # 而是从整个系统的顶层窗口 Z 序中，从前往后寻找“第一个覆盖鼠标点、且不是本进程窗口/覆盖层/任务栏/桌面”的窗口
            top_hwnd = win32gui.GetTopWindow(None)
            hwnd = top_hwnd
            original_hwnd = top_hwnd
            safety_counter = 0
            target_hwnd = None

            while hwnd:
                safety_counter += 1
                if safety_counter > 500:
                    # 防止异常情况下死循环
                    break

                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    pid = None

                # 跳过当前进程窗口以及显式需要忽略的窗口（覆盖层、主窗口、放大镜）
                if hwnd in skip_hwnds or pid == own_pid:
                    hwnd = win32gui.GetWindow(hwnd, win32con.GW_HWNDNEXT)
                    continue

                # 跳过不可见窗口
                if not win32gui.IsWindowVisible(hwnd):
                    hwnd = win32gui.GetWindow(hwnd, win32con.GW_HWNDNEXT)
                    continue

                # 获取窗口矩形（屏幕坐标系）
                try:
                    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                except Exception:
                    hwnd = win32gui.GetWindow(hwnd, win32con.GW_HWNDNEXT)
                    continue

                # 判断鼠标点是否落在该窗口内部
                if left <= screen_x < right and top <= screen_y < bottom:
                    target_hwnd = hwnd
                    break

                hwnd = win32gui.GetWindow(hwnd, win32con.GW_HWNDNEXT)

            hwnd = target_hwnd

            # 如果没有命中任何正常窗口，则认为当前在“桌面”区域上
            if not hwnd:
                # 若获取到任务栏区域，且鼠标点在任务栏内，则不做桌面兜底（避免把任务栏当成全屏）
                if taskbar_rect:
                    t_left, t_top, t_right, t_bottom = taskbar_rect
                    if t_left <= screen_x < t_right and t_top <= screen_y < t_bottom:
                        return None

                # 返回整个屏幕作为“桌面窗口”矩形（覆盖层坐标系）
                desktop_rect = {
                    "left": 0,
                    "top": 0,
                    "right": screen_w,
                    "bottom": screen_h,
                }
                # 桌面兜底视为“桌面全屏”
                self.is_desktop_fullscreen = True
                # 记录为无实际窗口句柄（后续取色时会自动走 mss 方式）
                self.window_hwnd = None
                return desktop_rect

            # 排除不可见窗口
            if not win32gui.IsWindowVisible(hwnd):
                return None

            # 过滤掉任务栏等窗口（桌面允许被选择用于全屏截图）
            class_name = win32gui.GetClassName(hwnd)
            taskbar_classes = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

            if class_name in taskbar_classes:
                return None

            # 如果命中的是桌面窗口（Progman/WorkerW），则认为是“桌面全屏截图”场景：
            # 直接返回整屏矩形，这样任务栏和托盘也会一起被截取。
            desktop_classes = {"Progman", "WorkerW"}
            if class_name in desktop_classes:
                # 命中桌面窗口，同样标记为桌面全屏
                self.is_desktop_fullscreen = True
                self.window_hwnd = None
                return {
                    "left": 0,
                    "top": 0,
                    "right": screen_w,
                    "bottom": screen_h,
                }

            # 计算在覆盖层坐标系中的窗口矩形（包含非客户区，可能带阴影）
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            window_rect = {
                "left": left - self.root.winfo_rootx(),
                "top": top - self.root.winfo_rooty(),
                "right": right - self.root.winfo_rootx(),
                "bottom": bottom - self.root.winfo_rooty(),
            }

            # 限制在屏幕范围内
            window_rect["left"] = max(0, window_rect["left"])
            window_rect["top"] = max(0, window_rect["top"])
            window_rect["right"] = min(screen_w, window_rect["right"])
            window_rect["bottom"] = min(screen_h, window_rect["bottom"])

            # 计算客户区矩形（用于避免包含系统边框/阴影）
            client_rect_overlay = None
            try:
                client_left, client_top, client_right, client_bottom = None, None, None, None
                client_l, client_t, client_r, client_b = win32gui.GetClientRect(hwnd)
                client_left_screen, client_top_screen = win32gui.ClientToScreen(hwnd, (0, 0))
                client_right_screen = client_left_screen + client_r
                client_bottom_screen = client_top_screen + client_b
                client_rect_overlay = {
                    "left": client_left_screen - self.root.winfo_rootx(),
                    "top": client_top_screen - self.root.winfo_rooty(),
                    "right": client_right_screen - self.root.winfo_rootx(),
                    "bottom": client_bottom_screen - self.root.winfo_rooty(),
                }
            except Exception:
                client_rect_overlay = None

            # 在高亮和“单击窗口截图”时，优先使用客户区矩形，避免把系统边框/阴影一起截进去；
            # 若获取失败或尺寸异常，则退回窗口矩形
            chosen_rect = window_rect
            if client_rect_overlay:
                width = client_rect_overlay["right"] - client_rect_overlay["left"]
                height = client_rect_overlay["bottom"] - client_rect_overlay["top"]
                if width >= 5 and height >= 5:
                    # 使用一个拷贝，避免修改原始 client_rect_overlay
                    chosen_rect = dict(client_rect_overlay)
                    # 用户反馈：左右和下边已经准确，但顶部仍多出 1 像素，这里对顶部再收缩 1 像素
                    original_top = chosen_rect["top"]
                    chosen_rect["top"] = original_top + 1

            # 如果最终矩形太小，则不显示
            if (
                chosen_rect["right"] - chosen_rect["left"] < 5
                or chosen_rect["bottom"] - chosen_rect["top"] < 5
            ):
                return None

            # 记录当前命中的窗口句柄和用于截图的矩形，供后续截图阶段使用
            self.window_hwnd = hwnd
            return chosen_rect
        except Exception as e:
            print(f"识别窗口失败：{e}")
            return None

    def _do_capture(self, x1, y1, x2, y2):
        """执行截图"""
        try:
            # 在截图前隐藏覆盖层和放大镜，避免它们被截进图片中
            try:
                if self.magnifier_window:
                    self.magnifier_window.withdraw()
            except Exception:
                pass

            # 隐藏覆盖层
            self.root.withdraw()

            # 截图逻辑恢复为最初的实现：统一按照覆盖层坐标换算成屏幕坐标来抓图，
            # 不区分“桌面全屏”与否，避免引入额外的不一致行为。
            screen_x = self.root.winfo_rootx()
            screen_y = self.root.winfo_rooty()
            abs_x1 = screen_x + x1
            abs_y1 = screen_y + y1
            abs_x2 = screen_x + x2
            abs_y2 = screen_y + y2

            bbox = (abs_x1, abs_y1, abs_x2, abs_y2)
            image = ImageGrab.grab(bbox=bbox)

            # 调用回调
            if self.on_capture:
                self.on_capture(image, bbox)

            self._cleanup()

        except Exception as e:
            print(f"截图失败：{e}")
            self._show_error()
            self._cleanup()

    def _cancel(self):
        """取消截图"""
        if self.on_cancel:
            self.on_cancel()
        self._cleanup()

    def _show_error(self):
        """显示错误信息"""
        error_win = tk.Toplevel(self.root)
        error_win.title("错误")
        error_win.attributes('-topmost', True)
        tk.Label(error_win, text="截图失败，请重试", padx=20, pady=20).pack()
        tk.Button(error_win, text="确定", command=error_win.destroy).pack()

    def _cleanup(self):
        """清理资源"""
        # 先解绑全局事件，避免销毁后仍有 Motion/ESC 回调触发
        try:
            self.root.unbind_all('<Motion>')
            self.root.unbind_all('<Escape>')
        except Exception:
            pass

        # 清除所有画布元素
        try:
            if hasattr(self, "canvas") and self.canvas.winfo_exists():
                self.canvas.delete("all")
        except Exception:
            pass

        # 清理放大镜窗口与覆盖层
        try:
            if hasattr(self, "magnifier_window") and self.magnifier_window:
                self.magnifier_window.destroy()
        except Exception:
            pass

        try:
            if hasattr(self, "root") and self.root:
                self.root.destroy()
        except Exception:
            pass
