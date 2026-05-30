"""
快捷截图 - 截图编辑窗口模块
"""
import tkinter as tk
from tkinter import simpledialog, messagebox
from PIL import Image, ImageDraw, ImageFont, ImageTk
import os


class ScreenshotEditor:
    """截图编辑窗口"""

    # 预设颜色
    PRESET_COLORS = [
        '#FF0000', '#FF6B00', '#FFD600', '#00FF00',
        '#00FFFF', '#0066FF', '#9900FF', '#FF00FF',
        '#000000', '#FFFFFF', '#808080', '#444444',
    ]

    def __init__(self, parent, image):
        """
        初始化编辑窗口
        :param parent: 父窗口
        :param image: PIL Image 对象
        """
        self.parent = parent
        self.original_image = image.copy()
        self.image = image.copy()
        self.result_image = None

        # 编辑工具状态
        self.current_tool = 'select'  # select, rect, pen, text
        self.current_color = '#FF0000'
        self.current_width = 3
        self.pen_points = []  # 画笔轨迹点
        self.text_items = []  # 存储文本信息 [(x, y, text, color, font_size)]
        self.rect_items = []  # 存储矩形信息 [(x1, y1, x2, y2, color, width)]
        self.pending_text = None  # 待确认的文本输入

        # 绘图对象
        self.draw = ImageDraw.Draw(self.image)

        # 创建窗口
        self._create_window()

    def _create_window(self):
        """创建编辑窗口"""
        img_w, img_h = self.image.size
        screen_w = self.parent.winfo_screenwidth()
        screen_h = self.parent.winfo_screenheight()

        # 计算窗口位置和大小
        win_w = min(img_w + 40, screen_w - 40)
        win_h = min(img_h + 120, screen_h - 40)  # 留出工具栏空间
        win_x = max(20, (screen_w - win_w) // 2)
        win_y = max(20, (screen_h - win_h) // 2)

        self.window = tk.Toplevel(self.parent)
        self.window.title("截图编辑")
        self.window.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")
        self.window.attributes('-topmost', True)
        self.window.configure(bg='#2d2d2d')

        # 主框架
        main_frame = tk.Frame(self.window, bg='#2d2d2d')
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 工具栏
        toolbar = self._create_toolbar(main_frame)
        toolbar.pack(fill=tk.X, padx=5, pady=5)

        # 画布区域
        self.canvas_frame = tk.Frame(main_frame, bg='#1a1a1a')
        self.canvas_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 创建画布
        scale = min(win_w / img_w, (win_h - 60) / img_h) if img_w > 0 and img_h > 0 else 1
        display_w = int(img_w * scale)
        display_h = int(img_h * scale)

        self.canvas = tk.Canvas(
            self.canvas_frame,
            width=display_w,
            height=display_h,
            bg='#333333',
            highlightthickness=0
        )
        self.canvas.pack()

        # 加载图片到画布
        self._update_display_image()

        # 底部按钮栏
        button_frame = self._create_button_bar(main_frame)
        button_frame.pack(fill=tk.X, padx=5, pady=5)

        # 绑定快捷键
        self.window.bind('<Escape>', lambda e: self.cancel())
        self.window.bind('<Return>', lambda e: self.confirm())
        self.window.bind('<Key>', self._on_key_press)

    def _create_toolbar(self, parent):
        """创建工具栏"""
        toolbar = tk.Frame(parent, bg='#3d3d3d', relief=tk.RAISED, bd=1)

        # 工具按钮
        tools = [
            ('选择', 'select', '[]'),
            ('画框', 'rect', '<>'),
            ('画笔', 'pen', '/~'),
            ('打字', 'text', 'Ab'),
        ]

        for text, tool_id, icon in tools:
            btn = tk.Button(
                toolbar,
                text=f"{icon} {text}",
                command=lambda t=tool_id: self._select_tool(t),
                bg='#4d4d4d' if tool_id != self.current_tool else '#00BBFD',
                fg='white',
                font=('Consolas', 9),
                relief=tk.RAISED,
                cursor='hand2',
                padx=8,
                pady=3
            )
            btn.pack(side=tk.LEFT, padx=2)
            setattr(self, f'_btn_{tool_id}', btn)

        # 颜色选择区域
        tk.Label(toolbar, text="颜色:", bg='#3d3d3d', fg='white', font=('Microsoft YaHei UI', 9)).pack(side=tk.LEFT, padx=(15, 5))

        self.color_buttons = []
        for i, color in enumerate(self.PRESET_COLORS):
            btn = tk.Button(
                toolbar,
                bg=color,
                width=2,
                height=1,
                relief=tk.RAISED,
                cursor='hand2',
                command=lambda c=color: self._select_color(c)
            )
            if color == self.current_color:
                btn.config(relief=tk.SUNKEN, bd=2)
            btn.pack(side=tk.LEFT, padx=1)
            self.color_buttons.append(btn)

        # 线条粗细
        tk.Label(toolbar, text="粗细:", bg='#3d3d3d', fg='white', font=('Microsoft YaHei UI', 9)).pack(side=tk.LEFT, padx=(15, 5))

        self.width_var = tk.IntVar(value=self.current_width)
        width_combo = tk.Spinbox(
            toolbar,
            from_=1,
            to=10,
            width=3,
            textvariable=self.width_var,
            font=('Microsoft YaHei UI', 9),
            command=self._on_width_change
        )
        width_combo.pack(side=tk.LEFT)

        return toolbar

    def _create_button_bar(self, parent):
        """创建底部按钮栏"""
        button_frame = tk.Frame(parent, bg='#2d2d2d')

        # 左侧取消按钮
        cancel_btn = tk.Button(
            button_frame,
            text="X Cancel",
            command=self.cancel,
            bg='#666666',
            fg='white',
            font=('Consolas', 10, 'bold'),
            relief=tk.RAISED,
            cursor='hand2',
            padx=20,
            pady=5
        )
        cancel_btn.pack(side=tk.LEFT, padx=10)

        # 中间空白
        tk.Frame(button_frame, bg='#2d2d2d').pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        # 右侧确定按钮
        confirm_btn = tk.Button(
            button_frame,
            text="V OK",
            command=self.confirm,
            bg='#00BBFD',
            fg='white',
            font=('Consolas', 10, 'bold'),
            relief=tk.RAISED,
            cursor='hand2',
            padx=20,
            pady=5
        )
        confirm_btn.pack(side=tk.RIGHT, padx=10)

        return button_frame

    def _select_tool(self, tool_id):
        """选择工具"""
        # 重置上一个工具按钮样式
        for t in ['select', 'rect', 'pen', 'text']:
            btn = getattr(self, f'_btn_{t}')
            btn.config(bg='#4d4d4d')

        # 高亮当前工具
        btn = getattr(self, f'_btn_{tool_id}')
        btn.config(bg='#00BBFD')
        self.current_tool = tool_id

        # 更新画布光标
        cursors = {'select': 'arrow', 'rect': 'cross', 'pen': 'pencil', 'text': 'xterm'}
        self.canvas.config(cursor=cursors.get(tool_id, 'arrow'))

        # 如果切换工具时有待确认的文本，提交它
        if tool_id != 'text' and self.pending_text:
            self._commit_pending_text()

    def _select_color(self, color):
        """选择颜色"""
        self.current_color = color
        # 更新颜色按钮样式
        for btn in self.color_buttons:
            if btn.cget('bg').lower() == color.lower():
                btn.config(relief=tk.SUNKEN, bd=2)
            else:
                btn.config(relief=tk.RAISED, bd=1)

    def _on_width_change(self):
        """线条粗细改变"""
        self.current_width = self.width_var.get()

    def _update_display_image(self):
        """更新显示的图片"""
        # 清除画布上的所有内容
        self.canvas.delete('all')

        # 缩放图片以适应画布
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        if canvas_w < 2 or canvas_h < 2:
            canvas_w = self.image.width
            canvas_h = self.image.height

        # 计算缩放比例
        scale_x = canvas_w / self.image.width
        scale_y = canvas_h / self.image.height
        self._scale = min(scale_x, scale_y, 1.0)  # 不放大，只缩小

        display_w = int(self.image.width * self._scale)
        display_h = int(self.image.height * self._scale)

        # 缩放图片
        if display_w != self.image.width or display_h != self.image.height:
            display_img = self.image.resize((display_w, display_h), Image.LANCZOS)
        else:
            display_img = self.image

        # 居中显示
        offset_x = (canvas_w - display_w) // 2
        offset_y = (canvas_h - display_h) // 2

        self._offset_x = offset_x
        self._offset_y = offset_y

        # 转换为 PhotoImage
        self._photo = ImageTk.PhotoImage(display_img)

        # 创建图片（使用坐标而不是标签，以便后续删除）
        self._img_id = self.canvas.create_image(offset_x, offset_y, anchor=tk.NW, image=self._photo)

        # 绑定鼠标事件
        self.canvas.bind('<Button-1>', self._on_mouse_down)
        self.canvas.bind('<B1-Motion>', self._on_mouse_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_mouse_up)
        self.canvas.bind('<Double-Button-1>', self._on_double_click)

    def _get_image_coords(self, event):
        """将画布坐标转换为图片坐标"""
        canvas_x = event.x - self._offset_x
        canvas_y = event.y - self._offset_y

        img_x = int(canvas_x / self._scale)
        img_y = int(canvas_y / self._scale)

        # 边界检查
        img_x = max(0, min(img_x, self.image.width - 1))
        img_y = max(0, min(img_y, self.image.height - 1))

        return img_x, img_y

    def _on_mouse_down(self, event):
        """鼠标按下"""
        x, y = self._get_image_coords(event)

        if self.current_tool == 'select':
            pass
        elif self.current_tool == 'rect':
            self._start_rect(x, y)
        elif self.current_tool == 'pen':
            self._start_pen(x, y)
        elif self.current_tool == 'text':
            self._start_text(x, y)

    def _on_mouse_drag(self, event):
        """鼠标拖动"""
        x, y = self._get_image_coords(event)

        if self.current_tool == 'rect':
            self._update_rect(x, y)
        elif self.current_tool == 'pen':
            self._update_pen(x, y)

    def _on_mouse_up(self, event):
        """鼠标释放"""
        x, y = self._get_image_coords(event)

        if self.current_tool == 'rect':
            self._end_rect(x, y)
        elif self.current_tool == 'pen':
            self._end_pen(x, y)

    def _on_double_click(self, event):
        """双击"""
        if self.current_tool == 'text':
            x, y = self._get_image_coords(event)
            self._prompt_text_input(x, y)

    def _on_key_press(self, event):
        """按键事件"""
        if self.current_tool == 'text':
            if event.keysym == 'Return':
                if self.pending_text:
                    self._commit_pending_text()
            elif event.keysym == 'Escape':
                if self.pending_text:
                    self._cancel_pending_text()

    # ========== 画框功能 ==========
    def _start_rect(self, x, y):
        """开始画框"""
        self._rect_start = (x, y)
        self._rect_preview_id = None

    def _update_rect(self, x, y):
        """更新画框预览"""
        # 删除旧的预览
        if self._rect_preview_id:
            self.canvas.delete(self._rect_preview_id)
            self._rect_preview_id = None

        x1, y1 = self._rect_start
        # 转换为画布坐标
        cx1, cy1 = int(x1 * self._scale + self._offset_x), int(y1 * self._scale + self._offset_y)
        cx2, cy2 = int(x * self._scale + self._offset_x), int(y * self._scale + self._offset_y)

        # 绘制矩形预览
        self._rect_preview_id = self.canvas.create_rectangle(
            cx1, cy1, cx2, cy2,
            outline=self.current_color,
            width=self.current_width
        )

    def _end_rect(self, x, y):
        """结束画框"""
        if self._rect_preview_id:
            self.canvas.delete(self._rect_preview_id)
            self._rect_preview_id = None

        x1, y1 = self._rect_start
        # 在图片上绘制矩形
        self.draw.rectangle([x1, y1, x, y], outline=self.current_color, width=self.current_width)
        # 保存矩形信息
        self.rect_items.append((min(x1, x), min(y1, y), max(x1, x), max(y1, y), self.current_color, self.current_width))

    # ========== 画笔功能 ==========
    def _start_pen(self, x, y):
        """开始画笔"""
        self.pen_points = [(x, y)]

    def _update_pen(self, x, y):
        """更新画笔轨迹"""
        if len(self.pen_points) >= 2:
            # 删除上一条预览线
            if hasattr(self, '_pen_preview_ids') and self._pen_preview_ids:
                for pid in self._pen_preview_ids:
                    self.canvas.delete(pid)

        self.pen_points.append((x, y))

        # 绘制预览线
        self._pen_preview_ids = []
        for i in range(len(self.pen_points) - 1):
            x1, y1 = self.pen_points[i]
            x2, y2 = self.pen_points[i + 1]
            cx1, cy1 = int(x1 * self._scale + self._offset_x), int(y1 * self._scale + self._offset_y)
            cx2, cy2 = int(x2 * self._scale + self._offset_x), int(y2 * self._scale + self._offset_y)
            pid = self.canvas.create_line(
                cx1, cy1, cx2, cy2,
                fill=self.current_color,
                width=max(1, int(self.current_width * self._scale))
            )
            self._pen_preview_ids.append(pid)

    def _end_pen(self, x, y):
        """结束画笔"""
        if len(self.pen_points) >= 2:
            # 在图片上绘制线条
            for i in range(len(self.pen_points) - 1):
                x1, y1 = self.pen_points[i]
                x2, y2 = self.pen_points[i + 1]
                self.draw.line([x1, y1, x2, y2], fill=self.current_color, width=self.current_width)

        # 清除预览
        if hasattr(self, '_pen_preview_ids') and self._pen_preview_ids:
            for pid in self._pen_preview_ids:
                self.canvas.delete(pid)
            self._pen_preview_ids = []

        self.pen_points = []

    # ========== 打字功能 ==========
    def _start_text(self, x, y):
        """开始文本输入"""
        # 如果有待确认的文本，先提交
        if self.pending_text:
            self._commit_pending_text()

        self._prompt_text_input(x, y)

    def _prompt_text_input(self, x, y):
        """弹出文本输入对话框"""
        text = simpledialog.askstring(
            "Input Text",
            "Enter text to add:",
            parent=self.window
        )
        if text:
            self.pending_text = {
                'x': x,
                'y': y,
                'text': text,
                'color': self.current_color
            }
            self._show_text_preview()

    def _show_text_preview(self):
        """显示文本预览"""
        if not self.pending_text:
            return

        x, y = self.pending_text['x'], self.pending_text['y']
        text = self.pending_text['text']
        color = self.pending_text['color']

        # 清除旧的预览
        if hasattr(self, '_text_preview_ids') and self._text_preview_ids:
            for tid in self._text_preview_ids:
                self.canvas.delete(tid)

        # 转换坐标
        cx, cy = int(x * self._scale + self._offset_x), int(y * self._scale + self._offset_y)

        # 显示预览文本
        font_size = int(16 * self._scale)
        try:
            font = ImageFont.truetype("msyh.ttc", max(10, font_size))
        except:
            font = ImageFont.load_default()

        # 测量文本尺寸
        bbox = self.draw.textbbox((x, y), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        # 绘制背景框
        padding = 2
        self._text_preview_ids = [
            self.canvas.create_rectangle(
                cx - padding, cy - padding,
                cx + int(text_w) + padding, cy + int(text_h) + padding,
                fill='#FFFF00', outline='#FFFF00'
            ),
            self.canvas.create_text(
                cx, cy,
                text=text,
                fill=color,
                font=('Microsoft YaHei UI', max(9, font_size)),
                anchor='nw'
            )
        ]

    def _commit_pending_text(self):
        """提交待确认的文本"""
        if not self.pending_text:
            return

        x = self.pending_text['x']
        y = self.pending_text['y']
        text = self.pending_text['text']
        color = self.pending_text['color']

        # 在图片上绘制文本
        try:
            font = ImageFont.truetype("msyh.ttc", 16)
        except:
            font = ImageFont.load_default()

        self.draw.text((x, y), text, fill=color, font=font)
        self.text_items.append((x, y, text, color, 16))

        # 清除预览
        if hasattr(self, '_text_preview_ids') and self._text_preview_ids:
            for tid in self._text_preview_ids:
                self.canvas.delete(tid)
            self._text_preview_ids = []

        self.pending_text = None

    def _cancel_pending_text(self):
        """取消待确认的文本"""
        if hasattr(self, '_text_preview_ids') and self._text_preview_ids:
            for tid in self._text_preview_ids:
                self.canvas.delete(tid)
            self._text_preview_ids = []
        self.pending_text = None

    def edit(self):
        """打开编辑窗口并返回编辑后的图片"""
        self.window.wait_window()
        return self.result_image

    def confirm(self):
        """确认编辑"""
        # 如果有待确认的文本，先提交
        if self.pending_text:
            self._commit_pending_text()

        self.result_image = self.image
        self.window.destroy()

    def cancel(self):
        """取消编辑"""
        # 如果有待确认的文本，先清除
        if self.pending_text:
            self._cancel_pending_text()

        self.result_image = None
        self.window.destroy()
