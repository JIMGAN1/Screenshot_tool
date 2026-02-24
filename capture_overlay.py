"""
快捷截图 - 框选覆盖层模块
"""
import tkinter as tk
from PIL import Image, ImageGrab, ImageTk
import mss
import pyperclip


class CaptureOverlay:
    """全屏框选覆盖层"""

    def __init__(self, parent, on_capture, on_cancel=None):
        """
        初始化覆盖层
        :param parent: 父窗口
        :param on_capture: 截图完成回调函数 (image, bbox)
        :param on_cancel: 取消回调函数
        """
        self.parent = parent
        self.on_capture = on_capture
        self.on_cancel = on_cancel

        # 创建全屏窗口
        self.root = tk.Toplevel(parent)
        self.root.attributes('-fullscreen', True)
        self.root.attributes('-topmost', True)
        self.root.configure(cursor='cross')
        self.root.attributes('-alpha', 0.3)  # 半透明

        # 截图区域变量
        self.start_x = None
        self.start_y = None
        self.end_x = None
        self.end_y = None
        self.rect_id = None

        # 当前坐标和颜色
        self.current_x = 0
        self.current_y = 0
        self.current_color = "#000000"

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
        self.root.bind('<Escape>', self._on_escape)  # ESC 取消
        self.root.bind('<Button-3>', self._on_right_click)  # 右键取消截图 + 复制信息
        self.canvas.bind('<Button-1>', self._on_mouse_down)
        self.canvas.bind('<B1-Motion>', self._on_mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_mouse_up)
        self.canvas.bind('<Motion>', self._on_mouse_move)

        # 顶部提示文字（半透明窗口上方，不影响截图结果）
        self.tip_label = tk.Label(
            self.root,
            text="请按Esc或者右键取消截图，按右键复制坐标和颜色",
            bg="#000000",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 11, "bold")
        )
        self.tip_label.place(relx=0.5, y=10, anchor="n")

        # 创建放大器
        self._create_magnifier()

    def _create_magnifier(self):
        """创建放大器窗口"""
        self.magnifier_window = tk.Toplevel(self.root)
        self.magnifier_window.overrideredirect(True)
        self.magnifier_window.attributes('-topmost', True)
        self.magnifier_window.configure(bg='#1a1a2e')

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
            fill="#00ff00",
            font=("Microsoft YaHei UI", 8, "bold")
        )
        self.color_text_id = self.magnifier_canvas.create_text(
            self.magnifier_size - 3,
            9,
            anchor=tk.E,
            text="#000000",
            fill="#00ff00",
            font=("Microsoft YaHei UI", 8, "bold")
        )

    def _update_magnifier(self, x, y):
        """更新放大器显示"""
        try:
            # 获取屏幕尺寸
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()

            # 截取 20x20 像素区域（鼠标周围）
            capture_size = 20
            left = max(0, x - capture_size // 2)
            top = max(0, y - capture_size // 2)

            with mss.mss() as sct:
                monitor = {
                    "left": left,
                    "top": top,
                    "width": capture_size,
                    "height": capture_size
                }
                screenshot = sct.grab(monitor)
                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

            # 放大图像（保持 20x20 像素）
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
            coord_text = f"{x}|{y}"
            self.magnifier_canvas.itemconfig(self.coord_text_id, text=coord_text)
            self.magnifier_canvas.itemconfig(self.color_text_id, text=self.current_color)

            # 更新放大器位置（鼠标左上角，如被遮挡则显示右下角）
            offset_x = 15
            offset_y = 15

            # 默认显示在鼠标左上角（坐标色值在放大器左上角，所以整体下移）
            mag_x = x - offset_x - self.magnifier_size
            mag_y = y - offset_y - self.magnifier_size - 20  # 减去标签高度

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

            self.magnifier_window.geometry(f"+{mag_x}+{mag_y}")

        except Exception as e:
            print(f"更新放大器失败：{e}")

    def _get_pixel_color(self, x, y):
        """获取像素颜色"""
        try:
            with mss.mss() as sct:
                monitor = {"left": x, "top": y, "width": 1, "height": 1}
                screenshot = sct.grab(monitor)
                pixel = screenshot.pixel(0, 0)
                return f"#{pixel[0]:02X}{pixel[1]:02X}{pixel[2]:02X}"
        except:
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

        # 创建选区矩形
        self.rect_id = self.canvas.create_rectangle(
            self.start_x, self.start_y,
            self.end_x, self.end_y,
            outline='#00ff00',
            width=2,
            dash=(4, 2)
        )

    def _on_mouse_drag(self, event):
        """鼠标拖动"""
        if self.start_x is not None:
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

            # 检查选区是否有效
            if x2 - x1 > 5 and y2 - y1 > 5:
                self._do_capture(x1, y1, x2, y2)
            else:
                self._cancel()

    def _on_mouse_move(self, event):
        """鼠标移动"""
        self.current_x = event.x
        self.current_y = event.y
        self.current_color = self._get_pixel_color(event.x, event.y)
        self._update_magnifier(event.x, event.y)

    def _do_capture(self, x1, y1, x2, y2):
        """执行截图"""
        try:
            # 隐藏覆盖层
            self.root.withdraw()

            # 获取屏幕位置（考虑多显示器）
            screen_x = self.root.winfo_rootx()
            screen_y = self.root.winfo_rooty()

            # 转换为屏幕坐标
            abs_x1 = screen_x + x1
            abs_y1 = screen_y + y1
            abs_x2 = screen_x + x2
            abs_y2 = screen_y + y2

            # 截图
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
        if self.magnifier_window:
            self.magnifier_window.destroy()
        self.root.destroy()
