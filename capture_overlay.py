"""
快捷截图 - 框选覆盖层模块
"""
from time import perf_counter, sleep
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageGrab, ImageTk, ImageChops
from numpy import array, asarray, int16, any
import mss
from utils import copy_text_to_clipboard
import win32gui
import win32con
import win32process
import os
import ctypes
from ctypes import wintypes
# import pyautogui
import threading

# Windows API 常量
MOUSEEVENTF_WHEEL = 0x0800
INPUT_MOUSE = 0

# Windows API 结构体
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.LPARAM), 
    ]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT),
    ]

# 获取 Windows API 函数
SendInput = ctypes.windll.user32.SendInput
SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
SendInput.restype = ctypes.c_uint  # 返回实际发送的输入数量
# 在文件开头添加 GetLastError
kernel32 = ctypes.windll.kernel32
GetLastError = kernel32.GetLastError
GetLastError.restype = ctypes.c_uint

class CaptureOverlay:
    """全屏框选覆盖层"""

    def __init__(self, parent, on_capture, on_cancel=None, base_image=None, enable_edit=False, fullscreen_offset=(0, 0)):
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
        self.enable_edit = enable_edit
        self.captured_bbox = None
        self.fullscreen_offset = fullscreen_offset

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
        # 设置半透明黑色背景（覆盖层效果）
        self.root.attributes('-alpha', 1)

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

        # 创建全屏遮罩和背景
        self.canvas = tk.Canvas(
            self.root,
            bg='black',
            highlightthickness=0,
            cursor='cross'
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # 显示预截图背景（让用户看到静态画面）
        if self.fullscreen_image is not None:
            self._bg_photo = ImageTk.PhotoImage(self.fullscreen_image)
            self.bg_id = self.canvas.create_image(0, 0, anchor=tk.NW, image=self._bg_photo)
            # 设置 canvas 滚动区域以支持大图片
            self.canvas.configure(scrollregion=(0, 0, self.fullscreen_image.width, self.fullscreen_image.height))
            # 把背景图放到最底层，确保框在最上层
            self.canvas.tag_lower(self.bg_id)

        # 绑定事件
        # 使用 bind_all 确保在放大镜等子窗口获得焦点时，ESC 也能生效
        self.root.bind_all('<Escape>', self._on_escape)  # ESC 取消（全局）
        self.root.bind_all('<Button-3>', self._on_right_click)  # 右键取消截图 + 复制信息
        # 画布自身的左键事件
        self.canvas.bind('<Button-1>', self._on_mouse_down)
        self.canvas.bind('<B1-Motion>', self._on_mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_mouse_up)
        # 顶层窗口（包括顶部空白区域 / 提示 label）左键事件统一转发到 canvas
        # 避免点击在 label 或 root 边缘时无法开始框选
        self.root.bind('<Button-1>', self._on_root_mouse_down, add="+")
        self.root.bind('<B1-Motion>', self._on_root_mouse_drag, add="+")
        self.root.bind('<ButtonRelease-1>', self._on_root_mouse_up, add="+")
        # 使用全局 Motion 绑定，让鼠标移动时即使在放大镜窗口上方也能持续更新
        self.root.bind_all('<Motion>', self._on_mouse_move)
        # 长截图模式：空格键绑定
        self.root.bind_all('<KeyPress-space>', self._on_space_press)
        self.root.bind_all('<KeyRelease-space>', self._on_space_release)

        # 检查是否有鼠标按下（用于区分单击和拖动）
        self._has_dragged = False

        # 长截图模式相关状态
        self.is_long_capture_mode = False
        self.space_pressed = False
        self.left_pressed = False  # 左键是否按下：用于空格/左键顺序无关的组合判断
        self.long_capture_rect = None
        self.scroll_unit_pixels = None  # 滚动单位像素数（计算一次后复用）
        self.stitched_image = None  # 拼接后的图片
        self.preview_window = None  # 预览窗口
        self.tip_window = None  # 长截图提示框窗口
        self.tip_window_hwnd = None  # 提示框窗口句柄（用于跳过列表）
        self.is_long_capture_active = False  # 是否正在进行长截图
        self.long_capture_cancelled = False  # 是否已取消长截图
        # 防止 _finish_long_capture 被重复调用
        self._long_finish_called = False

        # 顶部提示文字（半透明窗口上方，不影响截图结果）
        self.tip_default_text = "单击或拖动左键截图 | 右键复制坐标和色值 | 按Esc取消\n空格+左键框选滚动截图"
        self.tip_long_text = "空格+左键框选滚动截图：别框选悬浮窗和滚动条"

        self.tip_default_font = ("Microsoft YaHei UI", 25, "bold")
        self.tip_long_font = ("Microsoft YaHei UI", 35, "bold")  # 比默认大 5 号

        # 顶部提示：改为在 canvas 上绘制文字，而不是单独的 Label
        canvas_width = self.root.winfo_screenwidth()
        self.tip_text_id = self.canvas.create_text(
            canvas_width // 2,
            30,  # 顶部一点的位置
            text=self.tip_default_text,
            fill="#ffffff",
            font=self.tip_default_font,
            anchor="n",
            justify="center"  # 多行文本水平居中对齐
        )
        # 标记顶部提示是否已因框选而隐藏
        self.tip_hidden_for_selection = False

        # 创建放大器
        self._create_magnifier()

        # 放大镜创建后立即设置到当前鼠标位置（避免初始显示在左上角）
        try:
            # 获取当前鼠标的屏幕绝对坐标
            mouse_x = self.root.winfo_pointerx()
            mouse_y = self.root.winfo_pointery()
            # 转换为覆盖层坐标
            init_x = mouse_x - self.root.winfo_rootx()
            init_y = mouse_y - self.root.winfo_rooty()
            self._update_magnifier(init_x, init_y)
        except Exception:
            pass  # 忽略获取鼠标位置的错误

    def _create_toolbar_icons(self):
        """创建工具栏图标"""
        size = 20
        icons = {}
        
        # 矩形图标
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([2, 2, size - 3, size - 3], outline='white', width=2)
        icons['rect'] = ImageTk.PhotoImage(img)
        
        # 画笔图标：像素风格方块笔
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.line([(size - 3, 3), (8, size - 8)], fill='white', width=6)
        d.polygon([
        (1, size - 1),       # 三角形的左下角点 (与笔身末端重合)
        (7, 16), # 三角形的右上角点
        (4, 13)  # 三角形的左上角点
        ], fill='white', width=5)
        icons['pen'] = ImageTk.PhotoImage(img)


        # 文字图标：矩形框 + T
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, size-1, size-1], outline='white', width=2)
        d.text((5, 0), 'T', fill='white', font=ImageFont.load_default(size=16), stroke_width=1)
        icons['text'] = ImageTk.PhotoImage(img)
        
        # 撤销图标
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # 1. 底部水平直线段，连接圆弧
        d.line([(0, 18), (12, 18)], fill='white', width=3, joint='curve')
        # 2. 右侧的 180° 圆弧（关键部分，实现弯曲效果）
        d.arc( [(4, 4), (19, 19)],  # 圆弧的外接矩形
            start=270, end=90, fill='white', width=3)
        # 3. 顶部水平直线段，连接圆弧和箭头
        d.line([(8, 4), (12, 4)], fill='white', width=4, joint='curve')
        # 4. 箭头部分（用 polygon 画三角形）
        d.polygon ([
            (1, 4),   # 箭头尖点
            (8, 0),   # 箭头左上点
            (8, 8)   # 箭头左下点
        ], fill='white', width=3)
        icons['undo'] = ImageTk.PhotoImage(img)
        
        # 取消图标：X（用大尺寸+抗锯齿）
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # 用较粗的线条画X
        d.line([(4, 4), (16, 16)], fill='#FF6B6B', width=5, joint='curve')
        d.line([(16, 4), (4, 16)], fill='#FF6B6B', width=5, joint='curve')
        icons['cancel'] = ImageTk.PhotoImage(img)
        
        # 确认图标：勾
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.line([(1, 10), (7, 16), (19, 4)], fill='#69DB7C', width=5, joint='curve')
        icons['confirm'] = ImageTk.PhotoImage(img)
        
        return icons

    def _create_magnifier(self):
        """创建放大镜（在 canvas 上绘制）"""
        # 放大镜边框（向外扩展 border_offset 避免边框被裁剪）
        border_offset = 1
        self.mag_border_id = self.canvas.create_rectangle(
            -border_offset, -border_offset, 
            self.magnifier_size + border_offset, self.magnifier_size + border_offset,
            outline='#555', width=border_offset, fill='#1a1a2e'
        )
        
        # 放大镜内容（初始化为空白）
        self.mag_image_id = self.canvas.create_image(
            0, 0, anchor=tk.NW
        )
        
        # 中心十字线
        center = self.magnifier_size // 2
        self.mag_center_h = self.canvas.create_line(
            0, center, self.magnifier_size, center,
            fill="#00aaff", width=1
        )
        self.mag_center_v = self.canvas.create_line(
            center, 0, center, self.magnifier_size,
            fill='#00aaff', width=1
        )
        
        # 坐标和颜色文字
        self.mag_coord_id = self.canvas.create_text(
            2, 9, anchor=tk.NW, text="0|0", fill="#00BBFD",
            font=("Microsoft YaHei UI", 8)
        )
        self.mag_color_id = self.canvas.create_text(
            2, 23, anchor=tk.NW, text="#000000", fill="#00BBFD",
            font=("Microsoft YaHei UI", 8)
        )
        
        # 初始隐藏
        self._hide_magnifier()
    
    def _hide_magnifier(self):
        """隐藏放大镜"""
        if hasattr(self, 'mag_border_id'):
            self.canvas.itemconfigure(self.mag_border_id, state='hidden')
            self.canvas.itemconfigure(self.mag_image_id, state='hidden')
            self.canvas.itemconfigure(self.mag_center_h, state='hidden')
            self.canvas.itemconfigure(self.mag_center_v, state='hidden')
            self.canvas.itemconfigure(self.mag_coord_id, state='hidden')
            self.canvas.itemconfigure(self.mag_color_id, state='hidden')
    
    def _force_hide_magnifier(self):
        """强制删除放大镜元素"""
        for attr in ['mag_border_id', 'mag_image_id', 'mag_center_h', 'mag_center_v', 'mag_coord_id', 'mag_color_id']:
            if hasattr(self, attr) and getattr(self, attr) is not None:
                try:
                    self.canvas.delete(getattr(self, attr))
                except:
                    pass
                setattr(self, attr, None)
    
    def _show_magnifier(self):
        """显示放大镜"""
        if hasattr(self, 'mag_border_id'):
            self.canvas.itemconfigure(self.mag_border_id, state='normal')
            self.canvas.itemconfigure(self.mag_image_id, state='normal')
            self.canvas.itemconfigure(self.mag_center_h, state='normal')
            self.canvas.itemconfigure(self.mag_center_v, state='normal')
            self.canvas.itemconfigure(self.mag_coord_id, state='normal')
            self.canvas.itemconfigure(self.mag_color_id, state='normal')

    def _update_magnifier(self, x, y):
        """更新放大镜显示"""
        try:
            # 显示放大镜
            self._show_magnifier()
            
            # 从预截图裁切放大区域
            capture_size = 20
            if self.fullscreen_image is not None:
                left = max(0, int(x) - capture_size // 2)
                top = max(0, int(y) - capture_size // 2)
                right = left + capture_size
                bottom = top + capture_size
                right = min(right, self.fullscreen_image.width)
                bottom = min(bottom, self.fullscreen_image.height)
                left = max(0, right - capture_size)
                top = max(0, bottom - capture_size)
                img = self.fullscreen_image.crop((left, top, right, bottom))
            else:
                img = Image.new('RGB', (capture_size, capture_size), 'black')

            # 放大图像并更新 canvas
            img = img.resize((self.magnifier_size, self.magnifier_size), Image.NEAREST)
            photo = ImageTk.PhotoImage(img)
            self.canvas.itemconfigure(self.mag_image_id, image=photo)
            self.canvas.mag_photo = photo  # 保持引用

            # 计算放大镜位置（边框需要向外扩展）
            border_offset = 1
            offset_x = 15 + border_offset
            offset_y = 15 + border_offset
            mag_x = x - offset_x - self.magnifier_size - 3
            mag_y = y - offset_y - self.magnifier_size - 3
            
            if mag_x < 0:
                mag_x = x + offset_x - border_offset
            if mag_y < 0:
                mag_y = y + offset_y - border_offset
            
            # 更新所有元素的位置
            self.canvas.coords(self.mag_border_id, 
                             mag_x - border_offset, mag_y - border_offset,
                             mag_x + self.magnifier_size + border_offset, 
                             mag_y + self.magnifier_size + border_offset)
            self.canvas.coords(self.mag_image_id, mag_x, mag_y)
            
            center = self.magnifier_size // 2
            self.canvas.coords(self.mag_center_h, mag_x, mag_y + center, 
                             mag_x + self.magnifier_size, mag_y + center)
            self.canvas.coords(self.mag_center_v, mag_x + center, mag_y, 
                             mag_x + center, mag_y + self.magnifier_size)
            
            # 文字位置
            self.canvas.coords(self.mag_coord_id, mag_x + 2, mag_y + 9)
            self.canvas.coords(self.mag_color_id, mag_x + 2, mag_y + 23)
            
            # 更新文字内容
            screen_x = self.root.winfo_rootx() + x
            screen_y = self.root.winfo_rooty() + y
            self.canvas.itemconfigure(self.mag_coord_id, text=f"{int(screen_x)}|{int(screen_y)}")
            self.canvas.itemconfigure(self.mag_color_id, text=self.current_color)
            
            # 确保放大镜在最上层
            self.canvas.tag_raise(self.mag_border_id)
            self.canvas.tag_raise(self.mag_image_id)
            self.canvas.tag_raise(self.mag_center_h)
            self.canvas.tag_raise(self.mag_center_v)
            self.canvas.tag_raise(self.mag_coord_id)
            self.canvas.tag_raise(self.mag_color_id)

        except Exception:
            pass

    def _get_pixel_color(self, x, y):
        """获取像素颜色（使用预截图的图像坐标）"""
        try:
            # canvas 坐标直接对应预截图的图像坐标
            if self.fullscreen_image is not None:
                if (
                    0 <= x < self.fullscreen_image.width
                    and 0 <= y < self.fullscreen_image.height
                ):
                    r, g, b = self.fullscreen_image.getpixel((int(x), int(y)))
                    return f"#{r:02X}{g:02X}{b:02X}"
            
            # 没有预截图时，转换为屏幕坐标用 mss 取色
            screen_x = self.root.winfo_rootx() + x
            screen_y = self.root.winfo_rooty() + y
            try:
                with mss.mss() as sct:
                    monitor = {
                        "left": int(screen_x),
                        "top": int(screen_y),
                        "width": 1,
                        "height": 1,
                    }
                    screenshot = sct.grab(monitor)
                    pixel = screenshot.pixel(0, 0)
                    return f"#{pixel[0]:02X}{pixel[1]:02X}{pixel[2]:02X}"
            except Exception:
                return "#000000"
        except Exception:
            return "#000000"

    def _copy_info(self):
        """复制坐标和颜色到剪切板"""
        info = f"{self.current_x}|{self.current_y}\n{self.current_color}"
        copy_text_to_clipboard(info)

    def _on_right_click(self, event):
        """右键取消截图并复制坐标和颜色"""
        if self.is_long_capture_active:
            self._cancel()
            return
        
        self._copy_info()
        self._cancel()

    def _on_escape(self, event):
        """ESC 键取消截图"""
        # 如果正在编辑模式，取消编辑
        if hasattr(self, 'edit_frame') and self.edit_frame:
            self._edit_cancel()
            return
        self._cancel()

    def _on_space_press(self, event):
        """空格键按下"""
        self.space_pressed = True
        # 空格按下时，切换为长截图提示并放大字号
        if getattr(self, "tip_text_id", None) is not None:
            self.canvas.itemconfigure(
                self.tip_text_id,
                text=self.tip_long_text,
                font=self.tip_long_font,
            )
        #如果此时左键正在按住（先按左键再按空格），也进入长截图模式
        if self.left_pressed and self.start_x is not None:
            self.is_long_capture_mode = True

    def _on_space_release(self, event):
        """空格键释放"""
        self.space_pressed = False
        # 空格释放时，切换回正常提示并恢复字号
        if getattr(self, "tip_text_id", None) is not None:
            self.canvas.itemconfigure(
                self.tip_text_id,
                text=self.tip_default_text,
                font=self.tip_default_font,
            )
        # 补：必须左键也已释放，才允许启动长截图（避免拖拽中松开空格就开跑）
        if (self.is_long_capture_mode
                and not self.left_pressed
                and self.start_x is not None and self.end_x is not None):
            # 下面保持你现有逻辑不变
            x1 = min(self.start_x, self.end_x)
            y1 = min(self.start_y, self.end_y)
            x2 = max(self.start_x, self.end_x)
            y2 = max(self.start_y, self.end_y)
            if x2 - x1 > 5 and y2 - y1 > 5:
                self.long_capture_rect = (x1, y1, x2, y2)
                self._start_long_capture()
            else:
                self.is_long_capture_mode = False
                self._cancel()

    # ========= 顶部 / 顶层区域事件转发 =========
    def _event_to_canvas(self, event):
        """把任意控件上的事件坐标转换到 canvas 坐标"""
        # 统一使用覆盖层根窗口的位置来换算坐标，保证和 _on_mouse_move 的坐标体系一致
        canvas_x = event.x_root - self.root.winfo_rootx()
        canvas_y = event.y_root - self.root.winfo_rooty()

        class _Evt:
            def __init__(self, x, y):
                self.x = x
                self.y = y

        return _Evt(canvas_x, canvas_y)

    def _on_root_mouse_down(self, event):
        """顶层窗口左键按下（canvas 以外区域也能开始框选）"""
        # 如果本来就是在 canvas 上点击，就让 canvas 自己的绑定处理，避免重复调用
        if event.widget is self.canvas:
            return
        e = self._event_to_canvas(event)
        self._on_mouse_down(e)

    def _on_root_mouse_drag(self, event):
        """顶层窗口鼠标拖动（canvas 以外区域也能持续拖动）"""
        if event.widget is self.canvas:
            return
        e = self._event_to_canvas(event)
        self._on_mouse_drag(e)

    def _on_root_mouse_up(self, event):
        """顶层窗口左键释放"""
        if event.widget is self.canvas:
            return
        e = self._event_to_canvas(event)
        self._on_mouse_up(e)

    def _on_mouse_down(self, event):
        """鼠标按下"""
        #记录左键按下（用于“先左键后空格”）
        self.left_pressed = True

        self.start_x = event.x
        self.start_y = event.y
        self.end_x = event.x
        self.end_y = event.y
        self._has_dragged = False  # 初始化拖动标志

        # 检查是否是长截图模式（空格+左键）
        if self.space_pressed:
            self.is_long_capture_mode = True
            # 创建矩形作为拖动起始点（长截图模式）
            self.rect_id = self.canvas.create_rectangle(
                self.start_x, self.start_y,
                self.end_x, self.end_y,
                outline='#00ff00',
                width=2,
                dash=(4, 2)
            )
            return

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
            # 首次拖动时（普通/长截图都适用），可以确保选区矩形在最上层
            if self.rect_id is not None:
                try:
                    self.canvas.tag_raise(self.rect_id)
                except Exception:
                    pass

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
        #记录左键释放（配合 _on_space_release 的 not self.left_pressed）
        self.left_pressed = False

        if self.start_x is not None:
            self.end_x = event.x
            self.end_y = event.y

            # 确保 start < end
            x1 = min(self.start_x, self.end_x)
            y1 = min(self.start_y, self.end_y)
            x2 = max(self.start_x, self.end_x)
            y2 = max(self.start_y, self.end_y)

            # 检查是否是长截图模式（空格+左键）
            # 需要同时松开空格+左键才会开始滚动截图
            if self.is_long_capture_mode:
                # 只有在空格键也已经释放的情况下才启动长截图
                if not self.space_pressed:
                    # 检查选区是否有效
                    if x2 - x1 > 5 and y2 - y1 > 5:
                        self.long_capture_rect = (x1, y1, x2, y2)
                        # 启动长截图流程
                        self._start_long_capture()
                    else:
                        # 选区无效，取消长截图模式
                        self.is_long_capture_mode = False
                        self._cancel()
                else:
                    # 空格键还没释放，不启动长截图，但保持长截图模式
                    # 等待空格键释放
                    pass
                return

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
            tip_hwnd = self.tip_window_hwnd if hasattr(self, "tip_window_hwnd") and self.tip_window_hwnd else None
            preview_hwnd = (
                self.preview_window.winfo_id()
                if hasattr(self, "preview_window") and self.preview_window is not None
                else None
            )

            # 需要跳过的窗口句柄（自身覆盖层、主工具窗口、放大镜窗口、提示框窗口、预览窗口）
            skip_hwnds = {h for h in (overlay_hwnd, parent_hwnd, mag_hwnd, tip_hwnd, preview_hwnd) if h}
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
        except Exception:
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

            # 从预截图裁剪，无需隐藏覆盖层

            # 截图逻辑恢复为最初的实现：统一按照覆盖层坐标换算成屏幕坐标来抓图，
            # 不区分“桌面全屏”与否，避免引入额外的不一致行为。
            screen_x = self.root.winfo_rootx()
            screen_y = self.root.winfo_rooty()
            abs_x1 = screen_x + x1
            abs_y1 = screen_y + y1
            abs_x2 = screen_x + x2
            abs_y2 = screen_y + y2

            bbox = (abs_x1, abs_y1, abs_x2, abs_y2)
            # 直接从预先截取的全屏图中裁剪，完全避免重复截图导致的界面变化
            if self.fullscreen_image is not None:
                image = self.fullscreen_image.crop(bbox)
            else:
                # 兜底：如果没有预截图（理论上不应发生），临时截取
                image = ImageGrab.grab(bbox=bbox)

            # 如果启用了编辑模式，显示编辑工具栏
            if self.enable_edit:
                self.captured_bbox = (abs_x1, abs_y1, abs_x2, abs_y2)
                # 使用实际的屏幕坐标
                self._show_edit_toolbar(abs_x1, abs_y1, abs_x2, abs_y2)
            else:
                # 直接调用回调
                if self.on_capture:
                    self.on_capture(image, (abs_x1, abs_y1, abs_x2, abs_y2))
                self._cleanup()

        except Exception as e:
            print(f"截图失败: {e}")
            self._cleanup()

    def _show_edit_toolbar(self, x1, y1, x2, y2):
        """显示编辑工具栏（直接在全屏图上绘画）"""
        # 保存截图区域坐标
        self.edit_bbox = (x1, y1, x2, y2)
        
        # 截图区域尺寸
        img_w = abs(x2 - x1)
        img_h = abs(y2 - y1)
        
        # 工具栏尺寸
        toolbar_h = 40
        btn_size = 30
        btn_gap = 5
        
        # 计算工具栏宽度
        btn_size = 30
        tool_btn_gap = 8
        color_gap = 5
        action_btn_gap = 8
        # 4个工具按钮: 4*30 + 3*8 = 144
        tools_width = 4 * btn_size + 3 * tool_btn_gap
        # 6个颜色按钮: 6*17 = 102
        color_btn_w = btn_size // 2 + 4
        color_width = 6 * color_btn_w
        # 总宽度 = 左边距 + 工具 + 间隔 + 颜色 + 分隔线间隔 + 取消 + 间隔 + 确定 + 右边距
        # 10 + 144 + 15 + 102 + 10 + 30 + 8 + 30 + 10 = 359
        toolbar_w = 10 + tools_width + 15 + color_width + 10 + btn_size + action_btn_gap + btn_size + 10
        
        # 计算工具栏位置
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        
        # 工具栏在截图区域下方，右边对齐
        tb_x = max(x2, x1) - toolbar_w
        tb_y = max(y2, y1) + 5
        if tb_x < 0:
            tb_x = 5
        if tb_y + toolbar_h > screen_h:
            tb_y = min(y1, y2) - toolbar_h - 5
            if tb_y < 0:
                tb_y = 5
        
        # 强制隐藏放大镜
        self._force_hide_magnifier()
        
        # 删除提示文字
        if hasattr(self, 'tip_text_id') and self.tip_text_id:
            try:
                self.canvas.delete(self.tip_text_id)
                self.tip_text_id = None
            except:
                pass
        
        # 直接在全屏背景图上显示截图区域边框
        # 先删除旧的框选区域显示（如果有）
        if hasattr(self, '_selection_rect_id'):
            try:
                self.canvas.delete(self._selection_rect_id)
            except:
                pass
        
        # 显示框选区域边框
        self._selection_rect_id = self.canvas.create_rectangle(
            x1, y1, x2, y2,
            outline='#00BBFD', width=2
        )
        
        # 在截图区域位置显示截取的图片（从全屏图crop）
        cropped = self.fullscreen_image.crop((x1, y1, x2, y2))
        self._edit_photo = ImageTk.PhotoImage(cropped)
        self._edit_image_id = self.canvas.create_image(
            x1, y1, anchor=tk.NW, image=self._edit_photo
        )
        
        # 保存用于绘图的完整图片副本
        self._edit_base_image = self.fullscreen_image.copy()
        self._edit_draw = ImageDraw.Draw(self._edit_base_image)
        
        # 创建工具栏背景
        self._toolbar_bg_id = self.canvas.create_rectangle(
            tb_x, tb_y, tb_x + toolbar_w, tb_y + toolbar_h,
            fill='#2d2d2d', outline='#444444', width=1
        )
        
        # 工具栏按钮
        btn_size = 30
        btn_y = tb_y + (toolbar_h - btn_size) // 2
        
        self._toolbar_buttons = []
        self.color_buttons = {}
        
        # 创建工具图标
        icons = self._create_toolbar_icons()
        
        # 工具按钮
        tool_btn_gap = 8
        self.btn_rect = tk.Button(
            self.root, image=icons['rect'],
            command=lambda: self._select_edit_tool('rect'),
            bg='#1a1a2e', fg='white',
            relief=tk.FLAT, cursor='hand2', bd=0,
            highlightthickness=0, compound=tk.CENTER
        )
        self.btn_rect.place(x=tb_x + 10, y=btn_y, width=btn_size, height=btn_size)
        self._toolbar_buttons.append(self.btn_rect)
        self._icon_rect = icons['rect']
        
        self.btn_pen = tk.Button(
            self.root, image=icons['pen'],
            command=lambda: self._select_edit_tool('pen'),
            bg='#1a1a2e', fg='white',
            relief=tk.FLAT, cursor='hand2', bd=0,
            highlightthickness=0, compound=tk.CENTER
        )
        self.btn_pen.place(x=tb_x + 10 + btn_size + tool_btn_gap, y=btn_y, width=btn_size, height=btn_size)
        self._toolbar_buttons.append(self.btn_pen)
        self._icon_pen = icons['pen']
        
        self.btn_text = tk.Button(
            self.root, image=icons['text'],
            command=lambda: self._select_edit_tool('text'),
            bg='#1a1a2e', fg='white',
            relief=tk.FLAT, cursor='hand2', bd=0,
            highlightthickness=0, compound=tk.CENTER
        )
        self.btn_text.place(x=tb_x + 10 + (btn_size + tool_btn_gap) * 2, y=btn_y, width=btn_size, height=btn_size)
        self._toolbar_buttons.append(self.btn_text)
        self._icon_text = icons['text']
        
        # 撤销按钮
        self.btn_undo = tk.Button(
            self.root, image=icons['undo'],
            command=self._undo_edit,
            bg='#1a1a2e', fg='white',
            relief=tk.FLAT, cursor='hand2', bd=0,
            highlightthickness=0, compound=tk.CENTER
        )
        self.btn_undo.place(x=tb_x + 10 + (btn_size + tool_btn_gap) * 3, y=btn_y, width=btn_size, height=btn_size)
        self._toolbar_buttons.append(self.btn_undo)
        self._icon_undo = icons['undo']
        
        # 颜色按钮
        colors = ['#FF0000', '#00FF00', '#0000FF', '#FFFF00', '#000000', '#FFFFFF']
        color_start_x = tb_x + 10 + tools_width + 15
        color_gap = 0
        for i, color in enumerate(colors):
            btn = tk.Button(
                self.root, bg=color, width=2, height=1,
                relief=tk.FLAT, cursor='hand2', bd=0,
                command=lambda c=color: self._select_edit_color(c)
            )
            cx = color_start_x + i * (color_btn_w + color_gap)
            btn.place(x=cx, y=btn_y, width=color_btn_w, height=btn_size)
            self._toolbar_buttons.append(btn)
            self.color_buttons[color] = btn
        
        # 分隔线
        sep_x = color_start_x + color_width + 10
        self._toolbar_sep = self.canvas.create_line(
            sep_x, tb_y + 6, sep_x, tb_y + toolbar_h - 6,
            fill='#555555', width=1
        )
        
        # 取消/确定按钮（使用图标）
        action_btn_gap = 8
        ok_btn_x = tb_x + toolbar_w - 10 - btn_size
        cancel_btn_x = ok_btn_x - btn_size - action_btn_gap
        
        self.btn_cancel = tk.Button(
            self.root, image=icons['cancel'],
            command=self._edit_cancel,
            bg='#1a1a2e', fg='white',
            relief=tk.FLAT, cursor='hand2', bd=0,
            highlightthickness=0, compound=tk.CENTER
        )
        self.btn_cancel.place(x=cancel_btn_x, y=btn_y, width=btn_size, height=btn_size)
        self._toolbar_buttons.append(self.btn_cancel)
        self._icon_cancel = icons['cancel']
        
        self.btn_ok = tk.Button(
            self.root, image=icons['confirm'],
            command=self._edit_confirm,
            bg='#1a1a2e', fg='white',
            relief=tk.FLAT, cursor='hand2', bd=0,
            highlightthickness=0, compound=tk.CENTER
        )
        self.btn_ok.place(x=ok_btn_x, y=btn_y, width=btn_size, height=btn_size)
        self._toolbar_buttons.append(self.btn_ok)
        self._icon_confirm = icons['confirm']
        
        # 画笔粗细调节器（默认隐藏）
        self.pen_width = 2
        self.text_size = 16
        self.rect_width = 2
        slider_y = tb_y + toolbar_h + 5
        slider_h = 20
        slider_w = 120
        
        # 创建矩形边框粗细滑块容器（工具栏下方）
        self._rect_slider_frame = tk.Frame(self.root, bg='#2d2d2d', relief=tk.FLAT, bd=0)
        self._rect_slider_frame.place(x=tb_x + 10, y=slider_y, width=slider_w, height=slider_h)
        self._rect_slider_frame.lower()  # 默认隐藏
        
        tk.Label(
            self._rect_slider_frame, text='粗', fg='white', bg='#2d2d2d',
            font=('Arial', 8)
        ).place(x=0, y=3, width=20, height=14)
        
        self._rect_width_slider = tk.Scale(
            self._rect_slider_frame, from_=1, to=10, orient=tk.HORIZONTAL,
            length=80, bg='#2d2d2d', fg='white', troughcolor='#555555',
            highlightthickness=0, showvalue=0, command=self._on_rect_width_change
        )
        self._rect_width_slider.set(2)
        self._rect_width_slider.place(x=20, y=0)
        
        self._rect_width_label = tk.Label(
            self._rect_slider_frame, text='2', fg='white', bg='#2d2d2d',
            font=('Arial', 8)
        )
        self._rect_width_label.place(x=105, y=3, width=15, height=14)
        
        # 创建画笔粗细滑块容器（工具栏下方）
        self._pen_slider_frame = tk.Frame(self.root, bg='#2d2d2d', relief=tk.FLAT, bd=0)
        self._pen_slider_frame.place(x=tb_x + 10, y=slider_y, width=slider_w, height=slider_h)
        self._pen_slider_frame.lower()  # 默认隐藏
        
        tk.Label(
            self._pen_slider_frame, text='粗', fg='white', bg='#2d2d2d',
            font=('Arial', 8)
        ).place(x=0, y=3, width=20, height=14)
        
        self._pen_width_slider = tk.Scale(
            self._pen_slider_frame, from_=1, to=10, orient=tk.HORIZONTAL,
            length=80, bg='#2d2d2d', fg='white', troughcolor='#555555',
            highlightthickness=0, showvalue=0, command=self._on_pen_width_change
        )
        self._pen_width_slider.set(2)
        self._pen_width_slider.place(x=20, y=0)
        
        self._pen_width_label = tk.Label(
            self._pen_slider_frame, text='2', fg='white', bg='#2d2d2d',
            font=('Arial', 8)
        )
        self._pen_width_label.place(x=105, y=3, width=15, height=14)
        
        # 文字字体大小调节器（默认隐藏）
        self._text_slider_frame = tk.Frame(self.root, bg='#2d2d2d', relief=tk.FLAT, bd=0)
        self._text_slider_frame.place(x=tb_x + 10, y=slider_y, width=slider_w, height=slider_h)
        self._text_slider_frame.lower()  # 默认隐藏
        
        tk.Label(
            self._text_slider_frame, text='字', fg='white', bg='#2d2d2d',
            font=('Arial', 8)
        ).place(x=0, y=3, width=20, height=14)
        
        self._text_size_slider = tk.Scale(
            self._text_slider_frame, from_=10, to=48, orient=tk.HORIZONTAL,
            length=80, bg='#2d2d2d', fg='white', troughcolor='#555555',
            highlightthickness=0, showvalue=0, command=self._on_text_size_change
        )
        self._text_size_slider.set(16)
        self._text_size_slider.place(x=20, y=0)
        
        self._text_size_label = tk.Label(
            self._text_slider_frame, text='16', fg='white', bg='#2d2d2d',
            font=('Arial', 8)
        )
        self._text_size_label.place(x=105, y=3, width=15, height=14)
        
        # 状态变量
        self.edit_tool = 'rect'
        self.edit_color = '#FF0000'
        self.edit_start = None
        self.edit_pen_points = []
        self._edit_draw_items = []  # 保存所有绘图元素
        self._edit_preview_item = None  # 当前预览元素
        self._edit_history = []  # 撤销历史
        self._edit_history_max = 50  # 最多保存50步
        self._rect_slider_visible = False
        self._pen_slider_visible = False
        self._text_slider_visible = False
        self._text_just_committed = False
        
        # 初始化编辑绘图对象
        self._edit_draw = ImageDraw.Draw(self._edit_base_image)
        
        # 取消所有鼠标事件绑定
        self.canvas.unbind_all('<Button-1>')
        self.canvas.unbind_all('<B1-Motion>')
        self.canvas.unbind_all('<ButtonRelease-1>')
        self.canvas.unbind_all('<Double-Button-1>')
        self.canvas.unbind_all('<Motion>')
        self.root.unbind('<Button-1>')
        self.root.unbind('<B1-Motion>')
        self.root.unbind('<ButtonRelease-1>')
        
        # 绑定编辑鼠标事件
        self.canvas.bind('<Button-1>', self._edit_on_mouse_down)
        self.canvas.bind('<B1-Motion>', self._edit_on_mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self._edit_on_mouse_up)
        self.canvas.bind('<Double-Button-1>', self._edit_on_double_click)
        self.canvas.bind('<Motion>', self._edit_on_mouse_move)
        
        # 初始高亮颜色
        if '#FF0000' in self.color_buttons:
            self.color_buttons['#FF0000'].config(relief=tk.SUNKEN, bd=2)
        
        # 默认选中矩形工具（直接设置，不调用方法避免显示滑块）
        self.edit_tool = 'rect'
        self.btn_rect.config(bg='#00BBFD', highlightbackground='#00BBFD', highlightthickness=2)
        self.canvas.config(cursor='cross')

    def _select_edit_tool(self, tool):
        """选择编辑工具"""
        is_same_tool = (self.edit_tool == tool)
        
        # 如果点击的是不同工具，隐藏所有滑块并重置状态
        if not is_same_tool:
            if hasattr(self, '_rect_slider_frame'):
                self._rect_slider_frame.lower()
            if hasattr(self, '_pen_slider_frame'):
                self._pen_slider_frame.lower()
            if hasattr(self, '_text_slider_frame'):
                self._text_slider_frame.lower()
            self._rect_slider_visible = False
            self._pen_slider_visible = False
            self._text_slider_visible = False
            
            # 重置所有按钮样式
            for btn in [self.btn_rect, self.btn_pen, self.btn_text]:
                btn.config(bg='#1a1a2e', highlightbackground='#1a1a2e', highlightthickness=0)
        
        # 高亮选中按钮
        active_color = '#00BBFD'
        if tool == 'rect':
            self.btn_rect.config(bg=active_color, highlightbackground=active_color, highlightthickness=2)
            self.canvas.config(cursor='cross')
            # 切换滑块显示状态
            if hasattr(self, '_rect_slider_frame'):
                if self._rect_slider_visible:
                    self._rect_slider_frame.lower()
                    self._rect_slider_visible = False
                else:
                    self._rect_slider_frame.lift()
                    self._rect_slider_visible = True
        elif tool == 'pen':
            self.btn_pen.config(bg=active_color, highlightbackground=active_color, highlightthickness=2)
            self.canvas.config(cursor='pencil')
            # 切换滑块显示状态
            if hasattr(self, '_pen_slider_frame'):
                if self._pen_slider_visible:
                    self._pen_slider_frame.lower()
                    self._pen_slider_visible = False
                else:
                    self._pen_slider_frame.lift()
                    self._pen_slider_visible = True
        elif tool == 'text':
            self.btn_text.config(bg=active_color, highlightbackground=active_color, highlightthickness=2)
            self.canvas.config(cursor='xterm')
            # 切换滑块显示状态
            if hasattr(self, '_text_slider_frame'):
                if self._text_slider_visible:
                    self._text_slider_frame.lower()
                    self._text_slider_visible = False
                else:
                    self._text_slider_frame.lift()
                    self._text_slider_visible = True
        
        # 更新当前工具
        self.edit_tool = tool

    def _on_rect_width_change(self, value):
        """矩形边框粗细改变"""
        self.rect_width = int(value)
        if hasattr(self, '_rect_width_label'):
            self._rect_width_label.config(text=str(self.rect_width))

    def _on_pen_width_change(self, value):
        """画笔粗细改变"""
        self.pen_width = int(value)
        if hasattr(self, '_pen_width_label'):
            self._pen_width_label.config(text=str(self.pen_width))

    def _on_text_size_change(self, value):
        """文字大小改变"""
        self.text_size = int(value)
        if hasattr(self, '_text_size_label'):
            self._text_size_label.config(text=str(self.text_size))

    def _select_edit_color(self, color):
        """选择编辑颜色"""
        self.edit_color = color
        if hasattr(self, 'color_buttons'):
            for c, btn in self.color_buttons.items():
                if c == color:
                    btn.config(relief=tk.SUNKEN, bd=2)
                else:
                    btn.config(relief=tk.RAISED, bd=1)

    def _is_in_toolbar(self, x, y):
        """检查坐标是否在工具栏按钮上"""
        if hasattr(self, '_toolbar_buttons'):
            for btn in self._toolbar_buttons:
                try:
                    info = btn.place_info()
                    bx, by = int(info['x']), int(info['y'])
                    bw, bh = int(info['width']), int(info['height'])
                    if bx <= x <= bx + bw and by <= y <= by + bh:
                        return True
                except Exception:
                    pass
        return False

    def _is_in_edit_region(self, x, y):
        """检查坐标是否在编辑区域内"""
        if not hasattr(self, 'edit_bbox'):
            return False
        x1, y1, x2, y2 = self.edit_bbox
        # 确保坐标顺序正确
        left, right = min(x1, x2), max(x1, x2)
        top, bottom = min(y1, y2), max(y1, y2)
        return left <= x <= right and top <= y <= bottom

    def _is_in_toolbar(self, x, y):
        """检查坐标是否在工具栏区域"""
        if hasattr(self, '_toolbar_buttons'):
            for btn in self._toolbar_buttons:
                try:
                    info = btn.place_info()
                    bx, by = int(info['x']), int(info['y'])
                    bw, bh = int(info['width']), int(info['height'])
                    if bx <= x <= bx + bw and by <= y <= by + bh:
                        return True
                except:
                    pass
        if hasattr(self, '_toolbar_bg_id'):
            coords = self.canvas.coords(self._toolbar_bg_id)
            if coords and len(coords) >= 4:
                tb_x1, tb_y1, tb_x2, tb_y2 = coords
                if tb_x1 <= x <= tb_x2 and tb_y1 <= y <= tb_y2:
                    return True
        return False

    def _redraw_canvas(self):
        """重绘 canvas：显示编辑后的截图区域"""
        if not hasattr(self, '_edit_base_image') or not hasattr(self, '_edit_image_id'):
            return
        try:
            self.canvas.delete(self._edit_image_id)
        except:
            pass
        try:
            # 从编辑后的全屏图裁剪出截图区域
            # edit_bbox 是屏幕绝对坐标，需要转换为相对于 root 的坐标
            screen_x = self.root.winfo_rootx()
            screen_y = self.root.winfo_rooty()
            abs_x1, abs_y1, abs_x2, abs_y2 = self.edit_bbox
            # crop 使用相对于全屏图的坐标
            cropped = self._edit_base_image.crop((abs_x1, abs_y1, abs_x2, abs_y2))
            self._edit_photo = ImageTk.PhotoImage(cropped)
            # canvas 位置使用相对于 root 的坐标
            rel_x1 = abs_x1 - screen_x
            rel_y1 = abs_y1 - screen_y
            self._edit_image_id = self.canvas.create_image(
                rel_x1, rel_y1, anchor=tk.NW, image=self._edit_photo
            )
        except Exception as e:
            print(f"重绘失败: {e}")

    def _edit_on_mouse_down(self, event):
        """鼠标按下"""
        x, y = event.x, event.y
        if self._is_in_toolbar(x, y):
            return
        if not self._is_in_edit_region(x, y):
            return
        
        # 如果有未提交的输入框，先提交
        if hasattr(self, '_text_entry') and self._text_entry:
            self._commit_text_input()
            # 标记刚刚提交过，不要再次创建输入框
            self._text_just_committed = True
        else:
            self._text_just_committed = False
        
        # 保存当前状态到历史
        self._save_history()
        
        if self.edit_tool == 'rect':
            self.edit_start = (x, y)
        elif self.edit_tool == 'pen':
            self.edit_pen_points = [(x, y)]
        elif self.edit_tool == 'text':
            # 只有不是刚提交过的情况下才创建输入框
            if not self._text_just_committed:
                self._create_text_input(x, y)

    def _edit_on_mouse_drag(self, event):
        """鼠标拖动"""
        x, y = event.x, event.y
        if self._is_in_toolbar(x, y):
            return
        if not self._is_in_edit_region(x, y):
            return
        
        if self.edit_tool == 'rect' and self.edit_start:
            # 删除旧预览
            if self._edit_preview_item:
                self.canvas.delete(self._edit_preview_item)
            # 画新预览
            x1, y1 = self.edit_start
            self._edit_preview_item = self.canvas.create_rectangle(
                x1, y1, x, y, outline=self.edit_color, width=self.rect_width
            )
        elif self.edit_tool == 'pen':
            if self.edit_pen_points:
                x1, y1 = self.edit_pen_points[-1]
                # 转换 canvas 相对坐标为屏幕绝对坐标
                screen_x = self.root.winfo_rootx()
                screen_y = self.root.winfo_rooty()
                x1_abs, y1_abs = x1 + screen_x, y1 + screen_y
                x_abs, y_abs = x + screen_x, y + screen_y
                # 画到图片上
                self._edit_draw.line([x1_abs, y1_abs, x_abs, y_abs], fill=self.edit_color, width=self.pen_width)
                self.edit_pen_points.append((x, y))
                self._redraw_canvas()

    def _save_history(self):
        """保存当前状态到历史"""
        if not hasattr(self, '_edit_base_image'):
            return
        # 复制当前图片状态
        self._edit_history.append(self._edit_base_image.copy())
        # 限制历史长度
        if len(self._edit_history) > self._edit_history_max:
            self._edit_history.pop(0)

    def _undo_edit(self):
        """撤销上一步操作"""
        if not hasattr(self, '_edit_history') or not self._edit_history:
            return
        # 恢复上一个状态
        self._edit_base_image = self._edit_history.pop()
        # 重新创建 ImageDraw 对象
        self._edit_draw = ImageDraw.Draw(self._edit_base_image)
        # 重绘 canvas
        self._redraw_canvas()

    def _edit_on_mouse_move(self, event):
        """鼠标移动"""
        x, y = event.x, event.y
        if hasattr(self, 'edit_tool'):
            if self._is_in_edit_region(x, y):
                self.canvas.config(cursor='cross')
            else:
                self.canvas.config(cursor='arrow')

    def _edit_on_mouse_up(self, event):
        """鼠标释放"""
        x, y = event.x, event.y
        if self._is_in_toolbar(x, y):
            return
        if not self._is_in_edit_region(x, y):
            return
        
        if self.edit_tool == 'rect' and self.edit_start:
            # 删除预览
            if self._edit_preview_item:
                self.canvas.delete(self._edit_preview_item)
                self._edit_preview_item = None
            # 画矩形到全屏图
            # 转换 canvas 相对坐标为屏幕绝对坐标
            screen_x = self.root.winfo_rootx()
            screen_y = self.root.winfo_rooty()
            x1_abs, y1_abs = self.edit_start[0] + screen_x, self.edit_start[1] + screen_y
            x_abs, y_abs = x + screen_x, y + screen_y
            # 向外扩展 rect_width // 2 来补偿边框向内收缩
            offset = self.rect_width // 2
            self._edit_draw.rectangle([x1_abs - offset, y1_abs - offset, x_abs + offset-1, y_abs + offset-1], outline=self.edit_color, width=self.rect_width)
            self.edit_start = None
            self._redraw_canvas()
        elif self.edit_tool == 'pen':
            self.edit_pen_points = []
            self._redraw_canvas()

    def _edit_on_double_click(self, event):
        """编辑画布双击"""
        if self.edit_tool == 'text':
            # 创建输入框
            self._create_text_input(event.x, event.y)

    def _create_text_input(self, x, y):
        """在指定位置创建文字输入框"""
        # 隐藏其他滑块
        if hasattr(self, '_text_slider_frame') and self._text_slider_visible:
            self._text_slider_frame.lower()
        
        # 如果已有输入框，先提交
        if hasattr(self, '_text_entry') and self._text_entry:
            self._commit_text_input()
        
        # 计算输入框大小（根据字体大小）
        font_size = self.text_size
        entry_width = max(120, font_size * 8)
        entry_height = font_size + 6
        
        edit_color = getattr(self, 'edit_color', '#FF0000')
        
        # 创建透明输入框（使用 Canvas + Text）
        self._text_canvas = tk.Canvas(
            self.root,
            width=entry_width,
            height=entry_height,
            bg='#1a1a2e',
            highlightthickness=0,
            bd=0
        )
        self._text_canvas.place(x=x, y=y)
        
        # 创建 Text 小部件（使用设置的颜色）
        self._text_entry = tk.Text(
            self._text_canvas,
            width=20,
            height=1,
            bg='#1a1a2e',
            fg=edit_color,
            insertbackground=edit_color,
            relief=tk.FLAT,
            bd=0,
            font=('Arial', font_size),
            highlightthickness=0
        )
        self._text_entry.pack(fill=tk.BOTH, expand=True)
        self._text_entry.focus_set()
        
        # 绑定回车键提交
        self._text_entry.bind('<Return>', lambda e: self._commit_text_input())
        # 绑定 Escape 取消输入
        self._text_entry.bind('<Escape>', lambda e: self._cancel_text_input())
        
        # 保存输入位置
        self._text_input_pos = (x, y)

    def _cancel_text_input(self):
        """取消文字输入"""
        if hasattr(self, '_text_canvas') and self._text_canvas:
            try:
                self._text_canvas.destroy()
            except:
                pass
        self._text_canvas = None
        self._text_entry = None
        self._text_just_committed = False

    def _commit_text_input(self):
        """提交文字输入"""
        if not hasattr(self, '_text_entry') or not self._text_entry:
            return
        
        text = self._text_entry.get("1.0", tk.END).strip()
        x, y = self._text_input_pos if hasattr(self, '_text_input_pos') else (0, 0)
        
        # 销毁输入框
        try:
            self._text_entry.destroy()
        except:
            pass
        self._text_entry = None
        
        try:
            if hasattr(self, '_text_canvas') and self._text_canvas:
                self._text_canvas.destroy()
                self._text_canvas = None
        except:
            pass
        
        # 如果有文字，绘制到图片
        if text:
            try:
                font = ImageFont.truetype("msyh.ttc", self.text_size)
            except:
                font = ImageFont.load_default()
            self._edit_draw.text((x, y), text, fill=self.edit_color, font=font)
            self._redraw_canvas()
        
        # 重置提交标记
        self._text_just_committed = False

    def _edit_on_key(self, event):
        """编辑键盘事件"""
        if event.keysym == 'Escape':
            self._edit_cancel()

    def _edit_confirm(self):
        """确认编辑"""
        # 从编辑后的全屏图裁剪出截图区域
        x1, y1, x2, y2 = self.edit_bbox
        final_image = self._edit_base_image.crop((x1, y1, x2, y2))

        # 保存裁剪后的图片到剪贴板
        from utils import copy_to_clipboard
        copy_to_clipboard(final_image)

        # 调用回调
        if self.on_capture:
            self.on_capture(final_image, self.edit_bbox)

        # 清理并退出
        self._cleanup_edit()
        self._cleanup()

    def _edit_cancel(self):
        """取消编辑"""
        # 清理编辑资源
        self._cleanup_edit()
        # 退出截图模式
        self._cancel()

    def _cleanup_edit(self):
        """清理编辑相关资源"""
        # 销毁工具栏按钮
        for btn in getattr(self, '_toolbar_buttons', []):
            try:
                btn.destroy()
            except:
                pass
        self._toolbar_buttons = []
        
        # 删除 canvas 上的编辑元素
        for item_id in getattr(self, '_edit_draw_ids', []):
            try:
                self.canvas.delete(item_id)
            except:
                pass
        
        # 删除编辑图片和工具栏背景
        if hasattr(self, '_edit_image_id'):
            try:
                self.canvas.delete(self._edit_image_id)
            except:
                pass
            self._edit_image_id = None
        if hasattr(self, '_edit_photo'):
            self._edit_photo = None
        if hasattr(self, '_toolbar_bg_id'):
            try:
                self.canvas.delete(self._toolbar_bg_id)
            except:
                pass
            self._toolbar_bg_id = None
        if hasattr(self, '_toolbar_sep'):
            try:
                self.canvas.delete(self._toolbar_sep)
            except:
                pass
            self._toolbar_sep = None
        
        # 删除预览元素
        if hasattr(self, '_edit_rect_preview'):
            try:
                self.canvas.delete(self._edit_rect_preview)
            except:
                pass
            self._edit_rect_preview = None
        
        # 重置状态
        self.edit_tool = None
        self.edit_start = None
        self.edit_pen_points = []
        self._edit_pen_preview_ids = []

    def _cancel(self):
        """取消截图"""
        # 如果正在编辑模式，先取消编辑
        if self.enable_edit and hasattr(self, 'edit_frame') and self.edit_frame:
            self._edit_cancel()
            return

        # 如果正在进行长截图，取消它
        if self.is_long_capture_active:
            self.long_capture_cancelled = True
            self._hide_long_capture_tip()
            if self.preview_window:
                try:
                    self.preview_window.destroy()
                except Exception:
                    pass
                self.preview_window = None

        # 不要立即清理，等待 _scroll_and_capture 完成
        # 如果已经有拼接好的图片，立即完成一次（但只允许运行一次）
        if self.stitched_image and not self._long_finish_called:
            self._long_finish_called = True
            self.root.after(0, self._finish_long_capture)
            return  # 长截图进行中时，不立即清理，让 _scroll_and_capture 处理

        if self.on_cancel:
            self.on_cancel()
        self._cleanup()

    # ========== 长截图功能方法 ==========
    def _start_long_capture(self):
        """启动滚动长截图流程"""
        if not self.long_capture_rect:
            return
        
        x1, y1, x2, y2 = self.long_capture_rect
        
        
        # 在后台线程中执行滚动截图，避免阻塞UI
        self.is_long_capture_active = True
        self.long_capture_cancelled = False
        self._long_finish_called = False  # 每次开始重置
        thread = threading.Thread(target=self._scroll_and_capture, daemon=True)
        thread.start()

    def _identify_window_for_scroll(self):
        """识别框选区域所在的窗口句柄"""
        if not self.long_capture_rect:
            return None
        
        x1, y1, x2, y2 = self.long_capture_rect
        # 使用框选区域中心点识别窗口
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2

        window_rect = self._identify_window_under_cursor(center_x, center_y)

        if window_rect and self.window_hwnd:
            return self.window_hwnd
        return None

    def _calculate_tip_position(self, rect_x1, rect_y1, rect_x2, rect_y2):
        """计算提示框位置，确保不遮挡框选区域，且距离截图范围至少2个像素"""
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        tip_width = 300  # 提示框宽度
        tip_height = 60  # 提示框高度
        margin = 2  # 与截图范围的最小间距（至少2个像素，确保不会被截取到）
        
        rect_center_x = (rect_x1 + rect_x2) // 2
        
        # 优先放在框选区域下方（距离截图范围下边界至少2个像素）
        tip_y = rect_y2 + margin
        if tip_y + tip_height <= screen_h:
            tip_x = rect_center_x - tip_width // 2
            # 确保不超出屏幕边界
            tip_x = max(0, min(tip_x, screen_w - tip_width))
            return tip_x, tip_y
        
        # 其次放在框选区域上方（距离截图范围上边界至少2个像素）
        tip_y = rect_y1 - tip_height - margin
        if tip_y >= 0:
            tip_x = rect_center_x - tip_width // 2
            tip_x = max(0, min(tip_x, screen_w - tip_width))
            return tip_x, tip_y
        
        # 如果上下都不行，放在屏幕顶部中央（确保不遮挡截图范围）
        return (screen_w - tip_width) // 2, 10


    def _hide_long_capture_tip(self):
        """隐藏长截图提示框"""
        if self.tip_window:
            try:
                self.tip_window.destroy()
            except Exception:
                pass
            self.tip_window = None
            self.tip_window_hwnd = None

    def _calculate_preview_position(self, rect_x1, rect_y1, rect_x2, rect_y2, preview_width, preview_height):
        """智能计算预览窗口位置（优先右侧，其次左侧，最后上方）"""
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        margin = 10  # 与框选区域的间距
        
        # 确保预览窗口不超出屏幕边界
        preview_width = min(preview_width, screen_w - 40)
        preview_height = min(preview_height, screen_h - 40)

        # 计算框选区域的高度
        rect_height = rect_y2 - rect_y1
        # 优先右侧
        if rect_x2 + margin + preview_width <= screen_w:
            preview_x = rect_x2 + margin
            # 让预览窗口垂直居中于框选区域，但如果超出屏幕则调整
            # 先尝试顶部对齐
            preview_y = rect_y1
            # 如果底部超出屏幕，向上调整
            if preview_y + preview_height > screen_h - margin:
                preview_y = screen_h - margin - preview_height
            # 如果顶部超出屏幕，向下调整
            if preview_y < margin:
                preview_y = margin
            # 如果预览窗口比框选区域高，尽量让框选区域在预览窗口中间
            if preview_height > rect_height:
                ideal_y = rect_y1 - (preview_height - rect_height) // 2
                # 确保不超出屏幕
                if ideal_y >= margin and ideal_y + preview_height <= screen_h - margin:
                    preview_y = ideal_y
                elif ideal_y < margin:
                    preview_y = margin
                elif ideal_y + preview_height > screen_h - margin:
                    preview_y = screen_h - margin - preview_height
            return preview_x, preview_y, preview_width, preview_height
        # 其次左侧
        if rect_x1 - margin - preview_width >= 0:
            preview_x = rect_x1 - margin - preview_width
            # 同样的垂直对齐逻辑
            preview_y = rect_y1
            if preview_y + preview_height > screen_h - margin:
                preview_y = screen_h - margin - preview_height
            if preview_y < margin:
                preview_y = margin
            if preview_height > rect_height:
                ideal_y = rect_y1 - (preview_height - rect_height) // 2
                if ideal_y >= margin and ideal_y + preview_height <= screen_h - margin:
                    preview_y = ideal_y
                elif ideal_y < margin:
                    preview_y = margin
                elif ideal_y + preview_height > screen_h - margin:
                    preview_y = screen_h - margin - preview_height
            return preview_x, preview_y, preview_width, preview_height
        
        # 最后上方
        if rect_y1 - margin - preview_height >= 0:
            preview_x = rect_x1
            preview_y = rect_y1 - margin - preview_height
            preview_w = min(preview_width, rect_x2 - rect_x1)
            return preview_x, preview_y, preview_w, preview_height
        
        # 如果都不行，放在屏幕右上角
        return screen_w - preview_width - 20, 20, preview_width, preview_height

    def _show_preview(self, image):
        """显示拼接预览窗口"""
        if not self.long_capture_rect:
            return
        
        x1, y1, x2, y2 = self.long_capture_rect
        
        # 计算预览窗口尺寸（限制最大尺寸，默认宽度减少一半）
        max_preview_width = self.root.winfo_screenwidth() // 8
        max_preview_height = self.root.winfo_screenheight()
        
        # 如果图片尺寸超过最大限制，则缩放；否则使用图片实际尺寸
        if image.width > max_preview_width or image.height > max_preview_height:
            scale = min(max_preview_width / image.width, max_preview_height / image.height)
            preview_width = int(image.width * scale)
            preview_height = int(image.height * scale)
            preview_image = image.resize((preview_width, preview_height), Image.LANCZOS)
        else:
            # 使用图片实际尺寸，让预览窗口随图片变化
            preview_width = image.width
            preview_height = image.height
            preview_image = image
        
        # 计算预览窗口位置
        preview_x, preview_y, final_width, final_height = self._calculate_preview_position(
            x1, y1, x2, y2, preview_width, preview_height
        )
        
        # 转换为屏幕绝对坐标
        screen_x = self.root.winfo_rootx() + preview_x
        screen_y = self.root.winfo_rooty() + preview_y
        
        # 创建或更新预览窗口
        if not self.preview_window:
            self.preview_window = tk.Toplevel(self.root)
            self.preview_window.overrideredirect(True)
            self.preview_window.attributes('-topmost', True)
            self.preview_window.configure(bg='#000000')
            
            self.preview_canvas = tk.Canvas(
                self.preview_window,
                bg='#000000',
                highlightthickness=1,
                highlightbackground='#00ff00'
            )
            self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        
        # 更新预览窗口位置和大小
        self.preview_window.geometry(f"{final_width}x{final_height}+{screen_x}+{screen_y}")
        self.preview_canvas.config(width=final_width, height=final_height)
        
        # 显示预览窗口
        try:
            self.preview_window.deiconify()
        except Exception:
            pass
        
        # 更新预览图片
        photo = ImageTk.PhotoImage(preview_image)
        self.preview_canvas.delete("preview")
        self.preview_canvas.create_image(
            final_width // 2, final_height // 2,
            anchor=tk.CENTER, image=photo, tag="preview"
        )
        self.preview_canvas.image = photo  # 保持引用

    def _capture_region(self, use_realtime=False):
        """截取框选区域
        
        Args:
            use_realtime: 是否使用实时截图。True 时用于长截图滚动过程，False 时使用预截图
        """
        if not self.long_capture_rect:
            return None
        
        x1, y1, x2, y2 = self.long_capture_rect
        
        # 转换为屏幕绝对坐标
        screen_x = self.root.winfo_rootx()
        screen_y = self.root.winfo_rooty()
        abs_x1 = screen_x + x1
        abs_y1 = screen_y + y1
        abs_x2 = screen_x + x2
        abs_y2 = screen_y + y2

        bbox = (abs_x1, abs_y1, abs_x2, abs_y2)

        # 隐藏放大镜，避免被截取
        try:
            if self.magnifier_window:
                self.magnifier_window.withdraw()
        except Exception:
            pass

        # 隐藏预览窗口
        try:
            if hasattr(self, 'preview_window') and self.preview_window:
                self.preview_window.withdraw()
        except Exception:
            pass

        if use_realtime:
            # 长截图滚动时使用实时截图，获取滚动后的新画面
            image = ImageGrab.grab(bbox=bbox)
        else:
            # 普通截图或桌面场景：使用预截图，完全避免重复截图导致的界面变化
            if self.fullscreen_image is not None:
                image = self.fullscreen_image.crop(bbox)
            else:
                # 兜底：如果没有预截图
                image = ImageGrab.grab(bbox=bbox)

        # 恢复预览窗口
        try:
            if hasattr(self, 'preview_window') and self.preview_window:
                self.preview_window.deiconify()
        except Exception:
            pass

        return image

    def _capture_remaining_region(self):
        """截取框选区域底部到应用程序底部的剩余区域"""
        if not self.long_capture_rect or not self.window_hwnd:
            return None
        
        x1, y1, x2, y2 = self.long_capture_rect
        
        # 获取应用程序窗口的底部位置（使用客户区，避免包含边框）
        try:
            client_l, client_t, client_r, client_b = win32gui.GetClientRect(self.window_hwnd)
            client_left_screen, client_top_screen = win32gui.ClientToScreen(self.window_hwnd, (0, 0))
            client_bottom_screen = client_top_screen + client_b
        except Exception:
            # 如果获取客户区失败，使用窗口矩形
            try:
                left, top, right, bottom = win32gui.GetWindowRect(self.window_hwnd)
                client_bottom_screen = bottom
            except Exception:
                return None
        
        # 如果仍然无法获取底部位置，返回 None
        if client_bottom_screen is None:
            return None

        # 转换为屏幕绝对坐标
        screen_x = self.root.winfo_rootx()
        screen_y = self.root.winfo_rooty()
        abs_x1 = screen_x + x1
        abs_y2 = screen_y + y2  # 框选区域底部
        abs_x2 = screen_x + x2
        
        # 确保应用程序底部不小于框选区域底部
        if client_bottom_screen <= abs_y2:
            return None  # 没有剩余区域需要截取
        
        # 截取剩余区域：从框选区域底部到应用程序底部，宽度为框选区域宽度
        bbox = (abs_x1, abs_y2, abs_x2, client_bottom_screen)
        
        # 隐藏放大镜，避免被截取
        try:
            if self.magnifier_window:
                self.magnifier_window.withdraw()
        except Exception:
            pass
        
        # 隐藏预览窗口
        try:
            if hasattr(self, 'preview_window') and self.preview_window:
                self.preview_window.withdraw()
        except Exception:
            pass
        
        # 使用实时截图（滚动停止后的最终画面）
        try:
            image = ImageGrab.grab(bbox=bbox)
        except Exception:
            return None
        
        # 恢复预览窗口
        try:
            if hasattr(self, 'preview_window') and self.preview_window:
                self.preview_window.deiconify()
        except Exception:
            pass
        
        return image

    def get_mouse_scroll_lines_api(self):
        """获取鼠标滚轮滚动行数，失败默认返回3"""
        SPI_GETWHEELSCROLLLINES = 0x0068
        lines = wintypes.UINT()

        result = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETWHEELSCROLLLINES,
            0,
            ctypes.byref(lines),
            0
        )

        return lines.value if result else 3
    
    def _wait_scroll_stable(self, timeout=0.3, interval=0.005):
        """
        等待滚动动画结束：高频轮询截图，检测画面是否稳定。
        只截取框选区域中间一小条像素（10行），用 mss + bytes 直接比较，
        避免 numpy 开销，响应时间可达 ~10ms。
        
        - interval: 轮询间隔（秒），默认 5ms
        - timeout: 最大等待时间（秒），默认 0.3s 兜底
        """
        if not self.long_capture_rect:
            sleep(0.01)
            return

        x1, y1, x2, y2 = self.long_capture_rect
        screen_x = self.root.winfo_rootx()
        screen_y = self.root.winfo_rooty()

        # 只截取中间 10 行像素的横条，极大减少截图数据量
        region_h = y2 - y1
        mid_y = y1 + region_h // 2
        strip_half = 2  # 上下各 2 行，共 4 行
        strip_top = screen_y + max(y1, mid_y - strip_half)
        strip_bot = screen_y + min(y2, mid_y + strip_half)
        strip_left = screen_x + x1
        strip_right = screen_x + x2

        monitor = {
            "left": strip_left,
            "top": strip_top,
            "width": strip_right - strip_left,
            "height": strip_bot - strip_top,
        }

        try:
            with mss.mss() as sct:
                # 滚动前的基准帧
                baseline_bytes = bytes(sct.grab(monitor).rgb)
                start = perf_counter()

                # Phase 1: 等待画面开始变化（滚动开始渲染）
                changed = False
                while perf_counter() - start < timeout:
                    sleep(interval)
                    curr_bytes = bytes(sct.grab(monitor).rgb)
                    if curr_bytes != baseline_bytes:
                        changed = True
                        prev_bytes = curr_bytes
                        break

                if not changed:
                    # 超时仍未变化，可能滚动到底了或目标无响应，直接返回
                    return

                # Phase 2: 等待画面不再变化（滚动动画结束）
                while perf_counter() - start < timeout:
                    sleep(interval)
                    curr_bytes = bytes(sct.grab(monitor).rgb)
                    if curr_bytes == prev_bytes:
                        # 画面已稳定，立即返回
                        return
                    prev_bytes = curr_bytes
        except Exception as e:
            # mss 异常时兜底
            print(f"等待滚动动画完成异常: {e}")
            sleep(0.01)

    def _simulate_scroll(self, current_lines, rollback_unit_pixels,  x, y, pixels_amount=-100, target_hwnd=None):
        """在指定位置模拟鼠标滚轮"""
        # 转换为屏幕绝对坐标
        screen_x = self.root.winfo_rootx() + x
        screen_y = self.root.winfo_rooty() + y

        # try:
        #     # 隐藏预览窗口
        #     # original_alpha = self.root.attributes('-alpha')
        #     # self.root.attributes('-alpha', 0.0)
        #     if hasattr(self, 'preview_window') and self.preview_window:
        #         self.preview_window.withdraw()
        # except Exception:
        #     pass
        # # 使用pyautogui模拟滚轮
        # pyautogui.moveTo(screen_x, screen_y)
        # if rollback_unit_pixels != 0:
        #     pyautogui.scroll(rollback_unit_pixels, x=screen_x, y=screen_y)
        # else:
        #     pyautogui.scroll(scroll_amount, x=screen_x, y=screen_y)

        # 使用 Windows SendInput API 模拟滚轮
        # 移动鼠标到指定位置
        # 如果提供了目标窗口句柄，先激活它
        # 如果提供了目标窗口句柄，先验证它不是覆盖层窗口
        # 添加调试输出 
    
        # 移动鼠标到指定位置
        # ctypes.windll.user32.SetCursorPos(int(screen_x), int(screen_y))

        # 确定滚动单位像素数
        pixels_amount = rollback_unit_pixels if rollback_unit_pixels != 0 else pixels_amount
        
        #鼠标默认行数是3，如果是其他值进行转换
        mouse_data = int(pixels_amount * 3 / current_lines)
        # print(f"滚动: {rollback_unit_pixels}|{pixels_amount}|{current_lines}|{mouse_data}")

        # 如果提供了目标窗口句柄，用SendMessage发送滚轮消息
        if target_hwnd:
            # WM_MOUSEWHEEL 的 wParam 和 lParam 格式
            # wParam: 高16位是滚轮增量（有符号16位整数），低16位是按键状态
            # lParam: 低16位是 x 坐标（屏幕坐标），高16位是 y 坐标（屏幕坐标）
            
            # 将 mouse_data 转换为有符号16位整数
            wheel_delta = int(mouse_data)

            # wParam: 高16位是滚轮增量（有符号），低16位是按键状态（0表示没有按键）
            wparam = ((wheel_delta & 0xFFFF) << 16) | 0x0000

            # lParam: 低16位是 x 坐标（屏幕坐标），高16位是 y 坐标（屏幕坐标）
            lparam = ((int(screen_y) & 0xFFFF) << 16) | (int(screen_x) & 0xFFFF)

            # 使用 SendMessage 而不是 PostMessage，确保消息被处理
            # SendMessage 是同步的，会等待消息处理完成
            win32gui.SendMessage(target_hwnd, win32con.WM_MOUSEWHEEL, wparam, lparam)
            # print(f"SendMessage 发送滚轮消息: {wparam}|{lparam}|{wheel_delta}")
            # 等待滚动动画完成
            # sleep(0.1)

            # 等待滚动动画完成（截图对比检测画面稳定）
            self._wait_scroll_stable()
            
        #不提供目标窗口句柄，使用SendInput发送滚轮消息
        else:
            # 创建鼠标滚轮输入
            mouse_input = MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=mouse_data,
                dwFlags=MOUSEEVENTF_WHEEL,
                time=0,
                dwExtraInfo=0,
            )
    
            input_struct = INPUT(
                type=INPUT_MOUSE,
                mi=mouse_input,
            )
    
            # 发送输入并检查返回值
            result = SendInput(1, ctypes.byref(input_struct), ctypes.sizeof(INPUT))
            # print(f"SendInput 返回值: {result}|{mouse_data}|{input_struct}")
            if result == 0:
                error_code = ctypes.windll.kernel32.GetLastError()
                print(f"SendInput 返回值: {result} (1=成功, 0=失败), error_code: {error_code}")
            # 等待滚动动画完成
            # sleep(0.1)

            # 等待滚动动画完成（截图对比检测画面稳定）
            self._wait_scroll_stable()

        # 恢复覆盖层的 topmost 和 alpha 属性
        # if target_hwnd and original_topmost is not None:
        #     try:
        #         self.root.attributes('-topmost', original_topmost)
        #         self.root.attributes('-alpha', original_alpha)
        #         self.root.lift()  # 恢复窗口层级
        #     except Exception:
        #         pass


    
    def _regions_similar(self, r1, r2, same_ratio_value, pixel_diff_threshold, dynamic_region_ratio=None):
        """
        模糊判断两个灰度区域是否“足够相似”
        - pixel_diff_threshold: 单个像素允许的灰度差
        - dynamic_region_ratio: 允许被视为“动态区域”的最大占比（0~1），例如 0.3 表示最多 30%
        
        设计目标：
        - 适配微信聊天窗口中局部动图（如表情/GIF）导致的小区域差异
        - 自动识别差异主要集中区域时，将该区域视为“动态容错区”，在该区域内自动放宽像素差阈值
        """
        # 默认允许最多 30% 区域作为动态区域，可通过实例属性覆盖
        if dynamic_region_ratio is None:
            dynamic_region_ratio = getattr(self, "dynamic_region_ratio", 0.3)
        # 动态区域的额外容错阈值，例如基础阈值 25，则动态区内阈值为 25 + 40 = 65
        dynamic_extra_threshold = getattr(self, "dynamic_extra_threshold", 50)

        a1 = asarray(r1, dtype=int16)
        a2 = asarray(r2, dtype=int16)
        if a1.shape != a2.shape:
            return False

        diff = abs(a1 - a2)
        total_pixels = diff.size
        if total_pixels == 0:
            return False

        # 基础相似判定：灰度差在基础阈值以内的像素
        base_similar = diff <= pixel_diff_threshold

        # 如果本身就几乎完全相同，直接返回
        base_same_ratio = base_similar.sum() / total_pixels
        # 这里不直接返回 1.0，而是保留真实比例，后面仍可根据动态区域逻辑做微调
        if base_same_ratio >= same_ratio_value:
            return base_same_ratio

        # 找出“明显不同”的像素（可能是动图/闪烁区域）
        diff_mask = ~base_similar
        diff_count = diff_mask.sum()
        if diff_count == 0:
            # 理论上不会走到这里，因为上面 base_same_ratio 已覆盖，但为了安全保留
            return base_same_ratio

        # 基本的差异占比
        diff_ratio = diff_count / total_pixels

        # -----------------------------
        # 情况 1：整体差异非常大 → 直接认为整体变化，连通域直接跳过
        # -----------------------------
        # 这里乘以 2 是经验值：允许“比设定动态区域阈值再大一倍”的情况仍进入连通域判断
        # 例如 dynamic_region_ratio=0.3，则 diff_ratio>0.6 时直接认为整体变化
        if diff_ratio > dynamic_region_ratio * 2:
            return base_same_ratio

        # -----------------------------
        # 情况 2：差异整体不大，且可以近似看成“一个动态区域”
        #       → 不做连通域，只按整体包围盒处理一次
        # -----------------------------
        h, w = diff_mask.shape
        ys, xs = diff_mask.nonzero()
        if ys.size > 0:
            y_min, y_max = ys.min(), ys.max()
            x_min, x_max = xs.min(), xs.max()
            bbox_area = int((y_max - y_min + 1) * (x_max - x_min + 1))

            # 如果所有差异几乎都集中在一个紧凑区域（稀疏度不高），可以把它当作“单一动态区域”
            # sparse_ratio 越接近 1，说明 bbox 里大部分像素都是差异像素
            sparse_ratio = diff_count / max(bbox_area, 1)
            if diff_ratio <= dynamic_region_ratio and sparse_ratio >= 0.7:
                dynamic_threshold = pixel_diff_threshold + dynamic_extra_threshold
                refined_similar = base_similar.copy()

                sub_mask = diff_mask[y_min : y_max + 1, x_min : x_max + 1]
                if sub_mask.any():
                    sub_diff = diff[y_min : y_max + 1, x_min : x_max + 1]
                    local_dyn = sub_diff <= dynamic_threshold
                    update_mask = sub_mask & local_dyn
                    refined_similar[y_min : y_max + 1, x_min : x_max + 1][update_mask] = True

                same_ratio = refined_similar.sum() / total_pixels
                return same_ratio

        # -----------------------------
        # 情况 3：确实存在多个动态块 → 使用简单近似版（投影切分）
        # -----------------------------

        # 简单近似版：使用按行/按列投影 + 阈值切分快速粗分多个区块
        def _find_regions_by_projection(mask, min_area=100, max_components=3, gap_threshold=50):
            """
            使用投影方法快速切分区块，比完整连通域算法更快
            - gap_threshold: 距离大于此值（像素）算不同区块，默认50
            - max_components: 最多返回几个区块，默认3
            """
            h, w = mask.shape
            if h == 0 or w == 0:
                return []
            
            # 按行投影：统计每行有多少True像素
            row_proj = mask.sum(axis=1)  # shape: (h,)
            # 按列投影：统计每列有多少True像素
            col_proj = mask.sum(axis=0)  # shape: (w,)
            
            # 找到投影值>0的连续区间（行区间），直接使用列表避免后续转换
            row_intervals = []  # [[start_row, end_row], ...]
            in_interval = False
            start_row = 0
            for i in range(h):
                if row_proj[i] > 0:
                    if not in_interval:
                        start_row = i
                        in_interval = True
                else:
                    if in_interval:
                        row_intervals.append([start_row, i - 1])
                        in_interval = False
            if in_interval:
                row_intervals.append([start_row, h - 1])
            
            # 找到投影值>0的连续区间（列区间），直接使用列表避免后续转换
            col_intervals = []  # [[start_col, end_col], ...]
            in_interval = False
            start_col = 0
            for i in range(w):
                if col_proj[i] > 0:
                    if not in_interval:
                        start_col = i
                        in_interval = True
                else:
                    if in_interval:
                        col_intervals.append([start_col, i - 1])
                        in_interval = False
            if in_interval:
                col_intervals.append([start_col, w - 1])
            
            if not row_intervals or not col_intervals:
                return []
            
            # 合并距离较近的行区间（距离<=gap_threshold的合并）
            merged_row_intervals = [row_intervals[0][:]]  # 复制第一个区间
            for i in range(1, len(row_intervals)):
                prev_end = merged_row_intervals[-1][1]
                curr_start = row_intervals[i][0]
                if curr_start - prev_end <= gap_threshold:
                    # 合并：更新当前最后一个区间的结束位置
                    merged_row_intervals[-1][1] = row_intervals[i][1]
                else:
                    # 新区间：添加新区间
                    merged_row_intervals.append(row_intervals[i][:])
            
            # 合并距离较近的列区间（距离<=gap_threshold的合并）
            merged_col_intervals = [col_intervals[0][:]]  # 复制第一个区间
            for i in range(1, len(col_intervals)):
                prev_end = merged_col_intervals[-1][1]
                curr_start = col_intervals[i][0]
                if curr_start - prev_end <= gap_threshold:
                    # 合并：更新当前最后一个区间的结束位置
                    merged_col_intervals[-1][1] = col_intervals[i][1]
                else:
                    # 新区间：添加新区间
                    merged_col_intervals.append(col_intervals[i][:])
            
            # 根据行区间和列区间的组合，快速划分区块
            # 每个 (row_interval, col_interval) 组合形成一个候选区块
            candidate_regions = []  # [(area, (y_min, y_max, x_min, x_max)), ...]
            
            for y_min, y_max in merged_row_intervals:
                for x_min, x_max in merged_col_intervals:
                    # 计算这个区块内的实际True像素数量（面积）
                    block_mask = mask[y_min:y_max+1, x_min:x_max+1]
                    area = int(block_mask.sum())
                    
                    if area >= min_area:
                        candidate_regions.append((area, (y_min, y_max, x_min, x_max)))
            
            # 如果候选区域太少，直接返回空
            if not candidate_regions:
                return []
            
            # 按面积从大到小排序，只保留最大的 max_components 个
            candidate_regions.sort(key=lambda x: x[0], reverse=True)
            return candidate_regions[:max_components]

        # 使用简单近似版：投影切分，只取3个动态区间，距离大于50像素算不同区块
        components = _find_regions_by_projection(diff_mask, min_area=100, max_components=3, gap_threshold=50)
        if not components:
            return base_same_ratio

        # 统计所有"小块动态区域"的包围盒并计算整体动图占比
        dynamic_threshold = pixel_diff_threshold + dynamic_extra_threshold
        refined_similar = base_similar.copy()

        total_dyn_pixels = 0  # 使用各连通块自己真实面积，而不是包围盒面积，比例更准确

        for area, (y_min, y_max, x_min, x_max) in components:
            # 使用真实像素数来算比例，而不是包围盒面积，避免两个很稀疏的块拉大占比
            block_ratio = area / total_pixels

            # 只把“小块”当作动态区域（<= dynamic_region_ratio）
            if block_ratio <= dynamic_region_ratio:
                total_dyn_pixels += area

                # 一旦动态像素总量已经超过允许上限，就没必要继续细分，直接用基础相似度即可
                if total_dyn_pixels / total_pixels > dynamic_region_ratio:
                    return base_same_ratio

                # 在该块的包围盒内，用更大的阈值重新判断
                sub_mask = diff_mask[y_min : y_max + 1, x_min : x_max + 1]
                if not sub_mask.any():
                    continue
                sub_diff = diff[y_min : y_max + 1, x_min : x_max + 1]

                # 局部位置应用放宽阈值
                local_dyn = sub_diff <= dynamic_threshold
                # 只在差异像素位置上更新 refined_similar，避免误改本来就相似的像素
                update_mask = sub_mask & local_dyn
                refined_similar[y_min : y_max + 1, x_min : x_max + 1][update_mask] = True

        # 如果所有动态区域加起来仍然只占小部分，则使用放宽后的相似度
        if total_dyn_pixels > 0 and (total_dyn_pixels / total_pixels) <= dynamic_region_ratio:
            same_ratio = refined_similar.sum() / total_pixels
            # 调试时可以打开日志
            # print(f"动态区域占比{total_dyn_pixels / total_pixels:.2%}|{same_ratio:.2%}")
        else:
            # 差异区域太大，认为不是局部动图，而是整体内容变化，直接用基础比例
            same_ratio = base_same_ratio

        return same_ratio

    def _find_top_same(self, img1, img2):
        """
        检测img1和img2顶部完全相同的行数
        """
        w1, h1 = img1.size
        w2, h2 = img2.size
        if w1 != w2:
            print("图片宽度不同，无法比较")
            return 0

        # 预处理为灰度图 提高判断效率
        img1 = img1.convert('L') if img1.mode != 'L' else img1
        img2 = img2.convert('L') if img2.mode != 'L' else img2

        # 设置最大搜索范围
        max_search = min(h1, h2)

        top_same = 0
        overlap = 0
        for overlap in range(1, max_search + 1):
             # 从img1顶部取overlap行
            bottom_region = img1.crop((0, 0, w1, overlap))
            # 从img2顶部取overlap行
            top_region = img2.crop((0, 0, w2, overlap))
            
            # 比较两个区域是否完全相同
            try:
                diff = ImageChops.difference(bottom_region, top_region)
                diff_array = array(diff)
                # if ImageChops.difference(bottom_region, bottom_region).getbbox() is None:
                # 检查是否有超过容差的像素
                if (diff_array <= 5).mean() >= 0.999: #容差小于5的像素占比大于99%
                    top_same = overlap
                else:
                    break
            except Exception as e:
                print(f"比较异常: {e}")

                break
        #纯色重叠容易误判，跳过
        if top_same == 0:
            return 0
        else:
            gray = img1.crop((0, 0, w1, top_same)).convert("L")
            mn, mx = gray.getextrema()
            if mx - mn <= 20:
                # print(f"top_same跳过纯色: {top_same}|{mn}|{mx}")
                return 0
            # print(f"顶部相同区域: {top_same}|{mn}|{mx}")
            return top_same

    def _find_overlap_rows(self, img1, img2, max_search):
        """
        检测img1底部和img2顶部完全相同的行数
        
        参数：
        img1: 第一张图片
        img2: 第二张图片  
        max_search: 最大搜索行数（None表示搜索全部）
        
        返回：
        重叠的行数（0表示没有重叠）
        """
        # 确保两张图片宽度相同
        w1, h1 = img1.size
        w2, h2 = img2.size
        if w1 != w2:
            print("图片宽度不同，无法比较")
            return 0
        # 预处理为灰度图 提高判断效率
        img1 = img1.convert('L') if img1.mode != 'L' else img1
        img2 = img2.convert('L') if img2.mode != 'L' else img2

        # 设置最大搜索范围
        max_search = min(h1, h2, max_search)
        pure_color = 0
        overlap = 0
        # 从img2底部开始，逐行向上检查
        for overlap in range(1, max_search + 1):
            # 从img1底部取overlap行
            bottom_region = img1.crop((0, h1 - overlap, w1, h1))
            # 从img2顶部取overlap行
            top_region = img2.crop((0, 0, w2, overlap))
            

            #比较两个区域是否完全相同
            diff = ImageChops.difference(bottom_region, top_region)
            diff_array = array(diff)
            # if ImageChops.difference(bottom_region, bottom_region).getbbox() is None:
            # 检查是否有超过容差的像素
            if (diff_array <= 5).mean() >= 0.999: #容差小于5的像素占比大于99.9%
                #先排除新图顶部纯色区域
                gray = top_region.convert("L")
                mn, mx = gray.getextrema()
                if mx - mn <= 20:
                    pure_color += 1
                    continue
                # print(f"精确检测到重叠: {overlap}行{max_search}|{pure_color}|{mn}||{mx}")
                return overlap

        if pure_color >= 1:
            # print(f"纯色重叠: {pure_color}行{max_search}")
            return pure_color
                


        # 2. 精确没通过时，用模糊判断做补充
        overlap = 0
        for overlap in range(1, max_search + 1):
            # 从img1底部取overlap行
            bottom_region = img1.crop((0, h1 - overlap, w1, h1))
            # 从img2顶部取overlap行
            top_region = img2.crop((0, 0, w2, overlap))

            if overlap >= 20:
                # pixel_diff_threshold：判断单个像素是否相似的"容忍度" same_ratio：整个图像中满足条件的像素所占的比例
                same_ratio_value = 0.99
                same_ratio = self._regions_similar(bottom_region, top_region, same_ratio_value, pixel_diff_threshold=20)
                if same_ratio >= same_ratio_value:
                    # print(f"模糊检测到重叠: {overlap}行{max_search}")        
                    return overlap

        print(f"未检测到重叠，已检查 {overlap} 行{h1}|{h2}") 

        return 0

    def _stitch_images(self, base_image, new_image, scroll_unit_pixels):
        """按固定高度拼接图片（使用计算出的滚动单位像素数）"""
        if base_image is None:
            return new_image
        
        if new_image is None:
            return base_image
        
        # 从新图中截取顶部scroll_unit_pixels高度的区域
        new_width, new_height = new_image.size
        crop_height = min(scroll_unit_pixels, new_height)
        crop_region = new_image.crop((0, 0, new_width, crop_height))
        
        # 创建新的拼接图片
        base_width, base_height = base_image.size
        stitched_width = base_width
        stitched_height = base_height + crop_height
        
        stitched = Image.new('RGB', (stitched_width, stitched_height))
        stitched.paste(base_image, (0, 0))
        stitched.paste(crop_region, (0, base_height))
        
        return stitched

    def _scroll_and_capture(self):
        """滚动窗口并截图（循环）"""
        try:
            # 识别窗口
            window_hwnd = self._identify_window_for_scroll()
            if not window_hwnd:
                # 桌面场景：直接截取当前框选的区域
                # 隐藏覆盖层
                self.root.attributes('-alpha', 0)
                
                # 截取框选区域
                captured_image = self._capture_region()
                if not captured_image:
                    self.root.after(0, self._cancel)
                    return
                
                # 将截图设置为拼接图片
                self.stitched_image = captured_image
                
                # 显示预览
                self.root.after(0, lambda: self._show_preview(self.stitched_image))
                
                # 完成截图（使用回调保存）
                self.root.after(0, self._finish_long_capture)
                return
            
            x1, y1, x2, y2 = self.long_capture_rect

            # 获取应用程序窗口的底部位置（使用客户区，避免包含边框）
            try:
                client_l, client_t, client_r, client_b = win32gui.GetClientRect(self.window_hwnd)
                client_left_screen, client_top_screen = win32gui.ClientToScreen(self.window_hwnd, (0, 0))
                client_bottom_screen = client_top_screen + client_b
            except Exception:
                # 如果获取客户区失败，使用窗口矩形
                try:
                    left, top, right, bottom = win32gui.GetWindowRect(self.window_hwnd)
                    client_bottom_screen = bottom
                except Exception:
                    # 无法安全获取窗口底部，直接中止本次长截图
                    self.root.after(0, self._cancel)
                    return

            # 隐藏覆盖层
            self.root.attributes('-alpha', 0)

            center_x = (x1 + x2) // 2
            center_y = (y1 + y2) // 2

            # screen_x = self.root.winfo_rootx() + center_x
            # screen_y = self.root.winfo_rooty() + center_y

            # # 移动到框选区域中心
            # pyautogui.moveTo(screen_x, screen_y)

            # 计算反比例因子：框选区域越高，因子越小
            # reciprocal_ratio = 1 - (y2-y1) / self.root.winfo_screenheight()
            # print(f"反比例: {reciprocal_ratio}|{self.root.winfo_screenheight()}|{(y2-y1)}")
            #后续更加根据重叠行数动态调整滚动单位像素数
            # rect_height_ratio = int(reciprocal_ratio * (y2-y1)*0.1 + (y2-y1)*0.9)
            # rect_height = min(rect_height_ratio,int((y2-y1)*0.9))

            rect_height = y2-y1

            self.scroll_unit_pixels = int(rect_height*0.5)
            # 截取第一张图片（长截图需要实时截图获取当前画面）
            first_image_full = self._capture_region(use_realtime=True)
            if not first_image_full:
                self.root.after(0, self._cancel)
                return
            
            # # 第一张图片截取顶部部分
            # first_width, first_height = first_image_full.size
            # crop_height = first_height

        
            # first_image = first_image_full.crop((0, 0, first_width, crop_height))
            first_image = first_image_full
            self.stitched_image = first_image
            scroll_count = 0
            max_scrolls = 1000  # 防止无限循环
            no_change_count = 0  # 连续无变化次数
            current_image = first_image  # 用于比较的上一张图片
            
            # 更新预览
            self.root.after(0, lambda: self._show_preview(self.stitched_image))
            # self._show_preview(self.stitched_image)

            # 内存获取鼠标滚轮滚动行数，失败默认返回3
            current_lines = self.get_mouse_scroll_lines_api()

            overlap_rows = 0
            if_unit_pixels = False
            rollback_unit_pixels = 0
            # 滚动循环
            while scroll_count < max_scrolls and not self.long_capture_cancelled:
                # 检查取消标志（在每次循环开始时立即检查）
                if self.long_capture_cancelled:
                    break
                
                #rollback_unit_pixels不等于0时进行回滚操作
                self._simulate_scroll(current_lines, rollback_unit_pixels, center_x, center_y, pixels_amount=-self.scroll_unit_pixels, target_hwnd=window_hwnd)
                
                # 截取滚动后的图片（长截图需要实时截图获取滚动后的新画面）
                new_image = self._capture_region(use_realtime=True)
                if not new_image:
                    break

                # 检查图片是否变化
                try:
                    diff = ImageChops.difference(new_image, current_image)
                    if diff.getbbox() is None:
                        no_change_count += 1
                        print(f"图片相同, 已停止滚动")
                        # 当检测到没有变化时，截取框选区域底部到应用程序底部的剩余部分
                        if no_change_count >= 1:
                            remaining_image = self._capture_remaining_region()
                            if remaining_image:
                                # 将剩余部分拼接到长截图最后
                                remaining_height = remaining_image.height
                                if remaining_height > 0:  # 确保有内容
                                    # 直接拼接剩余部分
                                    base_width, base_height = self.stitched_image.size
                                    stitched_width = base_width
                                    stitched_height = base_height + remaining_height
                                    
                                    stitched = Image.new('RGB', (stitched_width, stitched_height))
                                    stitched.paste(self.stitched_image, (0, 0))
                                    stitched.paste(remaining_image, (0, base_height))
                                    self.stitched_image = stitched
                                    
                                    # 更新预览
                                    self.root.after(0, lambda img=self.stitched_image: self._show_preview(img))
                                    break
                            break
                    else:
                        try:
                            # 截取顶部相同区域
                            top_same = self._find_top_same(current_image, new_image)
                            if top_same != 0 and top_same < new_image.height-15:
                                new_image1 = new_image.crop((0, top_same+15, new_image.width, new_image.height))
                            else:
                                new_image1 = new_image
                            
                            max_search = rect_height
                            #确定安全滚动量
                            if if_unit_pixels == False:
                                overlap_rows = self._find_overlap_rows(current_image, new_image1, max_search)
                                if self.scroll_unit_pixels is None:
                                    self.scroll_unit_pixels = int(overlap_rows*0.7)
                                
                                if overlap_rows >= 50:
                                    self.scroll_unit_pixels = int(self.scroll_unit_pixels + overlap_rows*0.7)
                                    rollback_unit_pixels = 0
                                elif 0 < overlap_rows < 50:
                                    if_unit_pixels  = True
                                    rollback_unit_pixels = 0

                                if overlap_rows == 0 and no_change_count <= 5:
                                    if_unit_pixels = False
                                    rollback_unit_pixels = int(self.scroll_unit_pixels/3)
                                    self.scroll_unit_pixels = self.scroll_unit_pixels - rollback_unit_pixels
                                    print(f"回滚：{rollback_unit_pixels}")

                                print(f"确定安全滚动量: {self.scroll_unit_pixels}|{overlap_rows}|{(y2-y1)}|{if_unit_pixels}|{top_same}")
                            # 检测重叠行数
                            else:
                                overlap_rows = self._find_overlap_rows(current_image, new_image1, max_search)
                            
                            if overlap_rows == 0:
                                no_change_count += 1
                            else:
                                no_change_count = 0
                                # 裁剪掉重叠部分                                
                                new_image1 = new_image1.crop((0, overlap_rows, new_image1.width, new_image1.height))
                                # 拼接图片
                                self.stitched_image = self._stitch_images(self.stitched_image, new_image1, new_image1.height)
                                self.root.after(0, lambda img=self.stitched_image: self._show_preview(img))
                            

                        except Exception as e:
                            print(f"图片处理错误（重叠检测/拼接）: {e}")
                            # 如果图片处理失败，跳过本次循环，继续下一次
                            scroll_count += 1
                            continue
                except Exception as e:
                    print(f"图片比较错误: {e}")
                    # 如果图片比较失败，跳过本次循环，继续下一次
                    scroll_count += 1
                    continue

                if no_change_count >= 6:
                    print(f"连续多次无重叠: {no_change_count}")
                    break

                # 更新current_image为new_image，用于下次迭代的比较
                if overlap_rows != 0:
                    current_image = new_image
                
                scroll_count += 1
                
                
                if self.long_capture_cancelled:
                    break

            # 完成长截图
            # 即使被取消，如果有已拼接的图片，也要保存和复制
            print("完成长截图循环，准备保存")
            if self.stitched_image and not self._long_finish_called:
                self._long_finish_called = True
                self.root.after(0, self._finish_long_capture)
            elif not self.long_capture_cancelled:
                # 没有拼接图片且不是取消的，说明出错了，调用取消
                self.root.after(0, self._cancel)
        
        except Exception as e:
            print("发生错误:", e)
            # 即使出错，如果有已拼接的图片，也尝试完成保存
            if self.stitched_image and not self._long_finish_called:
                self._long_finish_called = True
                self.root.after(0, self._finish_long_capture)
            else:
                self.root.after(0, self._cancel)

    def _finish_long_capture(self):
        """完成长截图，调用现有保存逻辑"""
        # 再加一道保险，防止任何路径重复进入
        if self._long_finish_called:
            # 如果是从其他地方直接调进来的，这里也只执行一次
            pass
        else:
            self._long_finish_called = True

        if not self.stitched_image:
            self._cancel()
            return

        
        # 隐藏覆盖层和放大镜
        try:
            if self.magnifier_window:
                self.magnifier_window.withdraw()
        except Exception:
            pass
        
        try:
            if hasattr(self, 'root') and self.root and self.root.winfo_exists():
                self.root.withdraw()
        except Exception:
            pass
        
        # 计算bbox（使用原始框选区域的坐标）
        if self.long_capture_rect:
            x1, y1, x2, y2 = self.long_capture_rect
            screen_x = self.root.winfo_rootx()
            screen_y = self.root.winfo_rooty()
            abs_x1 = screen_x + x1
            abs_y1 = screen_y + y1
            abs_x2 = screen_x + x2
            abs_y2 = screen_y + y2
            # bbox使用原始区域，但实际图片是拼接后的
            bbox = (abs_x1, abs_y1, abs_x2, abs_y2)
        else:
            bbox = None
        
        # 调用回调
        if self.on_capture:
            self.on_capture(self.stitched_image, bbox)
        
        # 重置状态
        self.is_long_capture_active = False
        self.is_long_capture_mode = False
        self.long_capture_rect = None
        self.scroll_unit_pixels = None
        self.stitched_image = None
        
        self._cleanup()

    def _cleanup(self):
        """清理资源"""
        # 如果正在编辑模式，只清理编辑相关资源，保留 overlay 窗口
        if self.enable_edit and hasattr(self, 'edit_frame') and self.edit_frame:
            self._cleanup_edit()
            return

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

        # 销毁 overlay 窗口
        try:
            if hasattr(self, "root") and self.root:
                self.root.destroy()
        except Exception:
            pass
