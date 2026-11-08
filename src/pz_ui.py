# -*- coding: utf-8 -*-
r"""指针缩放 —— 界面外观层

纯标准库 tkinter 自己画的一小组控件。独立成模块，`pointer_gui.py` 只管布局。

为什么不用 ttk 默认样式
----------------------
ttk 的 LabelFrame / Scale / Checkbutton 是系统原生外观，配色和间距基本改不动，
也是它看起来「老气」的主因。所以这里改成：

* 用 Canvas 画圆角卡片、滑块、开关、分段选择器、按钮 —— 完全可控
* 颜色全部走 UI 对象，换主题只需要换色板 + 让各控件重画一次

主题
----
    LIGHT   浅色（默认）
    DARK    深色

约定：每个自绘控件都实现 `retheme()`，并在 `__init__` 里 `ui.extra(self)` 注册。
普通 tk 控件（Label / Frame / Entry）通过 `ui.reg(w, bg=..., fg=...)` 注册，
换主题时由 UI 统一回填颜色键。

用法
----
    ui = UI(root, "light")
    card = Card(outer, ui)
    ttk.Label(card.inner, ...)   # 控件照常建在 card.inner 里
    card.fit()
"""

import math
import time
import tkinter as tk
import tkinter.font as tkfont


# --------------------------------------------------------------------- 调色

def _hx(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def mix(a, b, t):
    """两种颜色按比例混合，t=0 取 a，t=1 取 b。造 hover / 浅色填充用。"""
    ar, ag, ab = _hx(a)
    br, bg, bb = _hx(b)
    return _hex((ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t))


def tint(hex_color, bg, amount):
    """把 hex_color 往 bg 混，amount 越大越接近 bg。用来做低透明度填充。"""
    return mix(hex_color, bg, amount)


def round_rect(cv, x0, y0, x1, y1, r, fill=None, outline=None, width=1):
    """Canvas 没有圆角矩形，用圆弧采样点拼多边形近似一个。

    返回第一个图元 id。fill / outline 至少要给一个。
    """
    x0, y0, x1, y1 = float(x0), float(y0), float(x1), float(y1)
    if x1 <= x0 or y1 <= y0:
        return None
    r = max(0.0, min(float(r), (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    if r < 0.6:
        items = []
        if fill:
            items.append(cv.create_rectangle(x0, y0, x1, y1, fill=fill))
        if outline:
            items.append(cv.create_rectangle(x0, y0, x1, y1, outline=outline,
                                             width=width))
        return items[0] if items else None

    steps = 8

    def arc(cx, cy, a0, a1):
        return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
                 cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps)))
                for i in range(steps + 1)]

    pts = [(x0 + r, y0), (x1 - r, y0)]
    pts += arc(x1 - r, y0 + r, 270, 360)          # 右上
    pts += [(x1, y1 - r)]
    pts += arc(x1 - r, y1 - r, 0, 90)             # 右下
    pts += [(x0 + r, y1)]
    pts += arc(x0 + r, y1 - r, 90, 180)           # 左下
    pts += [(x0, y0 + r)]
    pts += arc(x0 + r, y0 + r, 180, 270)          # 左上
    pts.append((x0 + r, y0))

    flat = [round(c, 2) for p in pts for c in p]
    items = []
    if fill:
        items.append(cv.create_polygon(flat, fill=fill))
    if outline:
        items.append(cv.create_line(flat, fill=outline, width=width,
                                    capstyle=tk.ROUND, joinstyle=tk.ROUND))
    return items[0] if items else None


# --------------------------------------------------------------------- 动效

def ease_out_cubic(t):
    """标准减速：起步快、收尾柔。界面上的补间统一用它。"""
    return 1.0 - (1.0 - t) ** 3


class Ticker:
    """after() 驱动的单值补间 —— 整个界面动效的最小内核。

    · `to(target)` 永远从**当前值**起步，所以动画可以随时被打断重来，
      快速连点不会跳变（和指针动画同一个思路）。
    · 每帧回调 `on_frame(value)`，控件在里面重画自己。
    · 控件销毁后自动停：destroy 之后连 `winfo_exists()` 都会抛 TclError，
      这里一律兜住，绝不让异常从 after 回调里逃出来。
    """

    FRAME_MS = 12               # ~80fps，tkinter 完全扛得住

    def __init__(self, widget, on_frame, ms=160, ease=ease_out_cubic):
        self.widget = widget
        self.on_frame = on_frame
        self.ms = ms
        self.ease = ease
        self.value = 0.0
        self.target = 0.0
        self._job = None
        self._t0 = 0.0
        self._from = 0.0
        self._dur = 0.001

    # ---- 内部 ----
    def _alive(self):
        try:
            return bool(self.widget.winfo_exists())
        except tk.TclError:
            return False

    def _cancel(self):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
            self._job = None

    def _frame(self, v, on_done=None):
        try:
            self.on_frame(v)
        except tk.TclError:
            return                      # 控件已销毁，静默收工
        except Exception:
            pass
        if on_done is not None:
            try:
                on_done()
            except Exception:
                pass

    def _step(self, on_done=None):
        self._job = None
        if not self._alive():
            return
        t = (time.perf_counter() - self._t0) / self._dur
        if t >= 1.0:
            self.value = self.target
            self._frame(self.value, on_done)
            return
        self.value = self._from + (self.target - self._from) * self.ease(t)
        self._frame(self.value)
        try:
            self._job = self.widget.after(self.FRAME_MS,
                                          lambda: self._step(on_done))
        except tk.TclError:
            self._job = None

    # ---- 对外 ----
    def jump(self, v):
        """不带动画直接落到 v（初始化和重绘时用）。"""
        self._cancel()
        self.value = self.target = float(v)

    def stop(self):
        self._cancel()
        self.target = self.value

    def to(self, target, ms=None, on_done=None):
        """平滑过渡到 target。on_done 在到达后调一次。"""
        target = float(target)
        self._cancel()
        if abs(target - self.value) < 1e-3:
            self.value = target
            self._frame(target, on_done)
            return
        self.target = target
        self._from = self.value
        self._t0 = time.perf_counter()
        self._dur = max(0.001, (self.ms if ms is None else ms) / 1000.0)
        self._step(on_done)


# --------------------------------------------------------------------- 色板

_LIGHT = {
    "bg":          "#f4f5f7",
    "card":        "#ffffff",
    "card_alt":    "#f1f3f6",
    "border":      "#e2e5ea",
    "border_soft": "#edeff1",
    "text":        "#15181d",
    "text2":       "#6b7280",
    "text3":       "#9aa2ae",
    "accent":      "#3b6cf5",
    "accent_soft": "#e8eeff",
    "on_accent":   "#ffffff",
    "track":       "#e4e7ec",
    "thumb":       "#ffffff",
    "thumb_edge":  "#3b6cf5",
    "green":       "#25a55f",
    "green_soft":  "#e6f5ec",
    "red":         "#d9534f",
    "off_track":   "#cfd4dc",
    "press":       "#3b6cf5",
    "release":     "#ef8b2c",
    "shadow":      "#000000",
}

_DARK = {
    "bg":          "#15171b",
    "card":        "#1f232a",
    "card_alt":    "#272c34",
    "border":      "#30363f",
    "border_soft": "#282e36",
    "text":        "#eef1f5",
    "text2":       "#9aa4b2",
    "text3":       "#6c7684",
    "accent":      "#6084ff",
    "accent_soft": "#26324f",
    "on_accent":   "#ffffff",
    "track":       "#313842",
    "thumb":       "#f2f5f9",
    "thumb_edge":  "#6084ff",
    "green":       "#38b673",
    "green_soft":  "#1d3a2c",
    "red":         "#e0645f",
    "off_track":   "#3b434e",
    "press":       "#6084ff",
    "release":     "#f09b46",
    "shadow":      "#000000",
}

THEMES = {"light": _LIGHT, "dark": _DARK}

FONT_PREFS = ["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei",
              "Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans"]


def _pick_font(root):
    try:
        avail = set(tkfont.families(root))
    except Exception:
        avail = set()
    for name in FONT_PREFS:
        if name in avail:
            return name
    return "TkDefaultFont"


# --------------------------------------------------------------------- UI

class UI:
    """主题色板 + 字体。所有颜色从这里取，换主题只动 pal。"""

    def __init__(self, root, theme="light"):
        self.root = root
        self.theme = theme
        self.pal = dict(THEMES[theme])
        self.font = _pick_font(root)

        # ---- 字号族：-1 用较细的体，因为中文用 bold 会糊 ----
        f = self.font
        self.f_title = (f, 16, "bold")
        self.f_sub = (f, 9)
        self.f_label = (f, 10)
        self.f_num = (f, 10)
        self.f_big = (f, 34, "bold")
        self.f_small = (f, 9)
        self.f_axis = (f, 8)

        self._reg = []      # (widget, bg_key, fg_key)
        self._extras = []   # 有 retheme() 的对象

    # ---- 取色 ----
    def c(self, key):
        return self.pal[key]

    # ---- 注册普通 tk 控件 ----
    def reg(self, w, bg=None, fg=None, **cfg):
        """创建一个普通 tk 控件后调它登记颜色键。extra cfg 原样传给 configure。

        w 已经是建好的控件，这里只补 bg/fg 并记账。
        """
        if cfg:
            try:
                w.configure(**cfg)
            except tk.TclError:
                pass
        self._paint(w, bg, fg)
        self._reg.append((w, bg, fg))
        return w

    def _paint(self, w, bg, fg):
        try:
            if bg:
                w.configure(bg=self.pal[bg])
            if fg:
                w.configure(fg=self.pal[fg])
        except tk.TclError:
            pass

    # ---- 注册自绘控件 ----
    def extra(self, obj):
        self._extras.append(obj)
        return obj

    # ---- 停掉全部控件动画（退出前调，免得销毁后还有 after 回调） ----
    def stop_anims(self):
        for obj in self._extras:
            fn = getattr(obj, "stop_anims", None)
            if fn is None:
                continue
            try:
                fn()
            except Exception:
                pass

    # ---- 换肤 ----
    def set_theme(self, name):
        if name not in THEMES:
            name = "light"
        self.theme = name
        self.pal = dict(THEMES[name])
        for w, bg, fg in self._reg:
            self._paint(w, bg, fg)
        for obj in self._extras:
            try:
                obj.retheme()
            except Exception:
                pass


# --------------------------------------------------------------------- 滚动条

class Scrollbar(tk.Scrollbar):
    """细滚动条。用原生 tk.Scrollbar 而不是 ttk 的，就是为了能自定义颜色。

    拇指、轨道、hover 三种色都来自色板，`retheme()` 里重刷。
    """

    WIDTH = 10

    def __init__(self, master, ui, **kw):
        self.ui = ui
        kw.setdefault("width", self.WIDTH)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("relief", "flat")
        kw.setdefault("elementborderwidth", 0)
        super().__init__(master, **kw)
        ui.extra(self)
        self.retheme()

    def retheme(self):
        p = self.ui.pal
        try:
            self.configure(bg=p["text3"], troughcolor=p["bg"],
                           activebackground=p["text2"])
        except tk.TclError:
            pass


# --------------------------------------------------------------------- 卡片

class Card(tk.Canvas):
    """圆角卡片。内部挂一个裸 Frame，控件照常 grid/pack 排。

    高度不自适应（Canvas 不会按内容撑高），内容建完后调 `fit()`。
    之后每次**宽度**变化会自动重新量高度 —— 文字换行跟着变，高度必须重算。
    """

    RADIUS = 14

    def __init__(self, master, ui, pad=18, on_fit=None, **kw):
        self.ui = ui
        self.pad = pad
        self.on_fit = on_fit
        self._fit_w = -1
        self._refitting = False
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("bg", ui.c("bg"))
        super().__init__(master, **kw)
        ui.extra(self)
        self.inner = tk.Frame(self, bg=ui.c("card"))
        self._win = self.create_window(pad + 1, pad + 1, window=self.inner,
                                       anchor="nw")
        self.bind("<Configure>", self._resize)

    def _resize(self, _evt=None):
        w = self.winfo_width()
        if w <= 1:
            w = self.winfo_reqwidth()
        if w <= 1:
            return
        h = self.winfo_height()
        if h <= 1:
            h = self.winfo_reqheight()
        self.delete("bg")
        round_rect(self, 0, 0, w - 1, max(1, h - 1), self.RADIUS,
                   fill=self.ui.c("card"), outline=self.ui.c("border"))
        self.itemconfigure(self._win,
                           width=max(10, w - 2 * self.pad - 2))
        self.coords(self._win, self.pad + 1, self.pad + 1)
        if self._fit_w != w:
            self._refit(w)

    def _refit(self, w):
        if self._refitting:
            return
        self._refitting = True
        try:
            self._fit_w = w
            self.inner.update_idletasks()
            self.configure(height=self.inner.winfo_reqheight()
                           + 2 * self.pad + 2)
        finally:
            self._refitting = False
        if self.on_fit:
            try:
                self.on_fit()
            except Exception:
                pass

    def retheme(self):
        self.configure(bg=self.ui.c("bg"))
        self.inner.configure(bg=self.ui.c("card"))
        self._resize()

    def fit(self):
        """内容建完后调用，把卡片高度撑到刚好包住 inner。"""
        self.update_idletasks()
        # 还没映射时 winfo_width 是 1，退回 reqwidth（构造时显式给的 width）
        w = self.winfo_width() or self.winfo_reqwidth()
        self._refit(w)

    # ---- 卡片内常用小件 ----

    def title(self, text):
        """小节标题：小号、粗体、次要色。"""
        lb = tk.Label(self.inner, text=text, font=self.ui.f_num,
                      bg=self.ui.c("card"))
        self.ui.reg(lb, bg="card", fg="text2")
        return lb

    def caption(self, text):
        lb = tk.Label(self.inner, text=text, font=self.ui.f_small,
                      bg=self.ui.c("card"))
        self.ui.reg(lb, bg="card", fg="text3")
        return lb

    def text_label(self, text):
        lb = tk.Label(self.inner, text=text, font=self.ui.f_label,
                      bg=self.ui.c("card"))
        self.ui.reg(lb, bg="card", fg="text")
        return lb


# --------------------------------------------------------------------- 滑块

class Slider(tk.Canvas):
    """画出来的滑块：圆角轨道 + 已选填充 + 圆形拇指。支持拖动和方向键。"""

    H = 26
    RADIUS = 7

    def __init__(self, master, ui, var, lo, hi, is_int=True, command=None,
                 bg="card", **kw):
        self.ui = ui
        self.var = var
        self.lo = float(lo)
        self.hi = float(hi)
        self.is_int = is_int
        self.command = command
        self._bg_key = bg
        self._hover = False
        self._active = False
        self._cw = 0
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("height", self.H)
        kw.setdefault("bg", ui.c(bg))
        super().__init__(master, **kw)
        ui.extra(self)
        # 0=常态 0.5=悬停 1=拖动：拇指半径跟着它平滑长大
        self._glow = Ticker(self, lambda _v: self.redraw(), ms=120)
        self._glow.jump(0.0)
        var.trace_add("write", lambda *_a: self.redraw())
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Right>", lambda _e: self.nudge(1))
        self.bind("<Left>", lambda _e: self.nudge(-1))
        self.bind("<Home>", lambda _e: self.nudge(-10 ** 6))
        self.bind("<End>", lambda _e: self.nudge(10 ** 6))
        self.redraw()

    # ---- 值 <-> 像素 ----
    def _span(self):
        # 注意 winfo_width 在未映射时是 1，1 是真值，`or` 兜底会失效
        w = self._cw if self._cw > 1 else 240
        return 8, max(9.0, w - 8)

    def _t(self):
        v = float(self.var.get())
        if self.hi == self.lo:
            return 0.0
        return max(0.0, min(1.0, (v - self.lo) / (self.hi - self.lo)))

    def _value_at(self, x):
        x0, x1 = self._span()
        t = (x - x0) / max(1.0, (x1 - x0))
        t = max(0.0, min(1.0, t))
        v = self.lo + (self.hi - self.lo) * t
        return int(round(v)) if self.is_int else round(v, 3)

    def nudge(self, direction):
        v = self._value_at(self._thumb_x() + direction * self._step())
        self._set(v)

    def _step(self):
        w = self._cw or 240
        x0, x1 = self._span()
        return max(1.0, (x1 - x0) / max(1, w)) * 8

    def _thumb_x(self):
        x0, x1 = self._span()
        return x0 + (x1 - x0) * self._t()

    # ---- 交互 ----
    def _set(self, v):
        old = self.var.get()
        if self.is_int:
            v = int(v)
        if v == old:
            return
        self.var.set(v)
        if self.command:
            self.command()

    def _set_hover(self, on):
        if on != self._hover:
            self._hover = on
            self._glow.to(0.5 if on else 0.0)

    def _press(self, evt):
        self._active = True
        self._glow.to(1.0)
        self._set(self._value_at(evt.x))
        self.redraw()
        return None

    def _move(self, evt):
        if self._active:
            self._set(self._value_at(evt.x))
        return None

    def _release(self, _evt):
        if self._active:
            self._active = False
            self._glow.to(0.5 if self._hover else 0.0)
            self.redraw()
        return None

    # ---- 绘制 ----
    def redraw(self):
        p = self.ui.pal
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        self._cw = w
        if w <= 1:
            w = 240
        if h <= 1:
            h = self.H
        cy = h / 2.0
        x0, x1 = self._span()
        thumb = x0 + (x1 - x0) * self._t()
        track_h = 6
        r_thumb = 6.0 + 2.0 * max(0.0, min(1.0, self._glow.value))

        round_rect(self, 5, cy - track_h / 2, w - 5, cy + track_h / 2,
                   track_h / 2, fill=p["track"])
        if thumb > 6:
            round_rect(self, 5, cy - track_h / 2, max(6.0, thumb),
                       cy + track_h / 2, track_h / 2, fill=p["accent"])

        self.create_oval(thumb - r_thumb, cy - r_thumb,
                         thumb + r_thumb, cy + r_thumb,
                         fill=p["thumb"], outline=p["thumb_edge"], width=2)

    def stop_anims(self):
        self._glow.stop()

    def retheme(self):
        self.configure(bg=self.ui.c(self._bg_key))
        self.redraw()


# --------------------------------------------------------------------- 开关

class Switch(tk.Canvas):
    """画出来的开关。ON=主题色，OFF=灰轨道，白色钮。绑定一个 BooleanVar。"""

    W, H = 46, 26

    def __init__(self, master, ui, var, command=None, bg="card", **kw):
        self.ui = ui
        self.var = var
        self.command = command
        self._bg_key = bg
        self._hover = False
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("width", self.W)
        kw.setdefault("height", self.H)
        kw.setdefault("bg", ui.c(bg))
        super().__init__(master, **kw)
        ui.extra(self)
        # 0=关 1=开：同一个补间值同时驱动轨道颜色和钮的位置
        self._pos = Ticker(self, lambda _v: self.redraw(), ms=170)
        self._pos.jump(1.0 if var.get() else 0.0)
        var.trace_add("write", lambda *_a: self._animate())
        self.bind("<Enter>", lambda _e: self._hover_on(True))
        self.bind("<Leave>", lambda _e: self._hover_on(False))
        self.bind("<ButtonPress-1>", self._click)
        self.redraw()

    def _animate(self):
        self._pos.to(1.0 if self.var.get() else 0.0)

    def _hover_on(self, on):
        self._hover = on
        self.redraw()

    def _click(self, _evt):
        self.var.set(not self.var.get())
        if self.command:
            self.command()
        return None

    def redraw(self):
        p = self.ui.pal
        self.delete("all")
        # 未映射时 winfo_* 返回 1，而 1 是真值 —— `or` 兜底会失效，必须显式判
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            w = self.W
        if h <= 1:
            h = self.H
        pos = max(0.0, min(1.0, self._pos.value))
        cy = h / 2.0
        pad = 3
        right = w - pad
        knob_r = 9 if self._hover else 8
        track = mix(p["off_track"], p["accent"], pos)

        round_rect(self, pad, cy - 10, right, cy + 10, 10, fill=track)

        kx = pad + 11 + (right - pad - 22) * pos
        self.create_oval(kx - knob_r, cy - knob_r, kx + knob_r, cy + knob_r,
                         fill=p["thumb"],
                         outline=tint(track, p["thumb"], 0.72),
                         width=2)

    def stop_anims(self):
        self._pos.stop()

    def retheme(self):
        self.configure(bg=self.ui.c(self._bg_key))
        self.redraw()


# --------------------------------------------------------------------- 分段

class Segmented(tk.Canvas):
    """画出来的分段选择器。绑定一个 tk 变量，点击即写入 value。"""

    H = 32
    RADIUS = 9

    def __init__(self, master, ui, options, var, command=None, bg="card", **kw):
        self.ui = ui
        self.items = list(options)          # [(value, text), ...]
        self.var = var
        self.command = command
        self._bg_key = bg
        self._hover = -1
        self._box = []
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("height", self.H)
        kw.setdefault("width", self._auto_width())
        kw.setdefault("bg", ui.c(bg))
        super().__init__(master, **kw)
        ui.extra(self)
        # 高亮块的位置（单位=段索引，可以是小数 = 正在滑动）
        self._pos = Ticker(self, lambda _v: self.redraw(), ms=190)
        self._pos.jump(float(self._sel_index()))
        var.trace_add("write", lambda *_a: self._animate())
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Leave>", lambda _e: self._set_hover(-1))
        self.bind("<Motion>", self._motion)
        self.bind("<ButtonPress-1>", self._click)
        self.redraw()

    def _sel_index(self):
        sel = self.var.get()
        for i, (val, _txt) in enumerate(self.items):
            if val == sel:
                return i
        return 0

    def _animate(self):
        self._pos.to(float(self._sel_index()))

    def _auto_width(self):
        try:
            f = tkfont.Font(font=self.ui.f_label)
            widest = max([f.measure(t) for _v, t in self.items] or [90])
            return self.H + 16 + widest * len(self.items)
        except Exception:
            return 40 * len(self.items) + 40

    def _set_hover(self, i):
        if i != self._hover:
            self._hover = i
            self.redraw()

    def _motion(self, evt):
        for i, (x0, x1) in enumerate(self._box):
            if x0 <= evt.x <= x1:
                self._set_hover(i)
                return None
        self._set_hover(-1)
        return None

    def _click(self, evt):
        for i, (x0, x1) in enumerate(self._box):
            if x0 <= evt.x <= x1:
                val, _txt = self.items[i]
                self.var.set(val)
                if self.command:
                    self.command()
                break
        return None

    def redraw(self):
        p = self.ui.pal
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1:
            w = 300
        if h <= 1:
            h = self.H
        self._box = []
        pad = 3
        n = len(self.items)
        if not n:
            return
        round_rect(self, 0, 0, w - 1, h - 1, self.RADIUS, fill=p["card_alt"])
        inner_w = w - 2 * pad
        seg_w = inner_w / n
        # 高亮块先画，文字压在它上面；位置取自补间值，所以是滑过去的
        pos = max(0.0, min(float(n - 1), self._pos.value))
        px0 = pad + pos * seg_w
        round_rect(self, px0 + 1, pad, px0 + seg_w - 1, h - pad,
                   self.RADIUS - 3, fill=p["accent"])
        for i, (val, txt) in enumerate(self.items):
            x0 = pad + i * seg_w
            x1 = pad + (i + 1) * seg_w
            self._box.append((x0, x1))
            # 被高亮块覆盖得越多，文字越接近 on_accent（滑动途中是渐变）
            a = max(0.0, 1.0 - abs(i - pos))
            base = p["text"] if self._hover == i else p["text2"]
            self.create_text((x0 + x1) / 2, h / 2, text=txt,
                             fill=mix(base, p["on_accent"], a),
                             font=self.ui.f_label)

    def stop_anims(self):
        self._pos.stop()

    def retheme(self):
        self.configure(bg=self.ui.c("card"))
        self.redraw()


# --------------------------------------------------------------------- 按钮

class Button(tk.Canvas):
    """画出来的圆角按钮。

    kind = "primary"    主题色底白字
           "secondary"  浅灰底深字（默认）
           "ghost"       透明底，跟随所在的父容器背景
    """

    H = 34
    RADIUS = 10

    def __init__(self, master, ui, text, command=None, kind="secondary",
                 bg="card", **kw):
        self.ui = ui
        self.text = text
        self.command = command
        self.kind = kind
        self._bg_key = bg
        self._hover = False
        self._down = False
        self._cw = 0
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("height", self.H)
        kw.setdefault("width", self._auto_width())
        kw.setdefault("bg", ui.c(bg))
        super().__init__(master, **kw)
        ui.extra(self)
        # 悬停底色渐变 + 按下内缩，各一个补间
        self._hl = Ticker(self, lambda _v: self.redraw(), ms=130)
        self._pr = Ticker(self, lambda _v: self.redraw(), ms=90)
        self._hl.jump(0.0)
        self._pr.jump(0.0)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda _e: self.redraw())

    def _auto_width(self):
        """按文字量给个自然宽度，省的拿到 Canvas 默认的 1 像素。"""
        try:
            f = tkfont.Font(font=self.ui.f_label)
            return max(56, f.measure(self.text) + 30)
        except Exception:
            return 84

    def _set_hover(self, on):
        if on != self._hover:
            self._hover = on
            self._hl.to(1.0 if on else 0.0)

    def _leave(self, _evt):
        self._set_hover(False)
        if self._down:
            self._down = False
            self._pr.to(0.0)

    def _press(self, _evt):
        self._down = True
        self._pr.to(1.0)

    def _release(self, evt):
        if self._down:
            self._down = False
            self._pr.to(0.0)
            if 0 <= evt.x <= (self._cw or 0) and self.command:
                self.command()
        return None

    def redraw(self):
        p = self.ui.pal
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height() or self.H
        self._cw = w
        if w <= 1:
            w = 90
        parent_bg = self.ui.c(self._bg_key)
        hl = max(0.0, min(1.0, self._hl.value))
        pr = max(0.0, min(1.0, self._pr.value))

        if self.kind == "primary":
            base = p["accent"]
            fill = mix(base, p["on_accent"], 0.18 * hl)
            fg = p["on_accent"]
        elif self.kind == "secondary":
            base = p["card_alt"]
            # 注意这里要传色值，不能传色板键名 —— mix() 只认 "#rrggbb"
            fill = mix(base, p["accent"], 0.10 * hl)
            fg = p["text"]
        else:                                   # ghost
            base = parent_bg
            fill = mix(base, p["text"], 0.05 * hl)
            fg = p["text2"]

        if self.kind == "primary":
            fill = mix(fill, "#000000", 0.10 * pr)

        # 按下时整体内缩一点点，视觉上像被按进去
        inset = 1.4 * pr
        x0, y0 = inset, inset
        x1, y1 = w - 1 - inset, h - 1 - inset
        if self.kind == "ghost":
            round_rect(self, x0, y0, x1, y1, self.RADIUS, fill=fill)
        elif self.kind == "secondary":
            round_rect(self, x0, y0, x1, y1, self.RADIUS, fill=fill,
                       outline=p["border"])
        else:
            round_rect(self, x0, y0, x1, y1, self.RADIUS, fill=fill)

        self.create_text(w / 2, h / 2, text=self.text, fill=fg,
                         font=self.ui.f_label, state="disabled")

    def stop_anims(self):
        self._hl.stop()
        self._pr.stop()

    def retheme(self):
        self.configure(bg=self.ui.c(self._bg_key))
        self.redraw()


# --------------------------------------------------------------------- 胶囊

class StatusPill(tk.Canvas):
    """右上角状态胶囊：小圆点 + 文字。"""

    H = 26

    def __init__(self, master, ui, textvar, kind="green", bg="card", **kw):
        self.ui = ui
        self.textvar = textvar
        self.kind = kind
        self._bg_key = bg
        self._cw = 0
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("height", self.H)
        kw.setdefault("bg", ui.c(bg))
        super().__init__(master, **kw)
        ui.extra(self)
        self._last_text = textvar.get()
        # 文字真的变了才闪一下（_tick 每 60ms 会 set 同一个值，必须去重）
        self._flash = Ticker(self, lambda _v: self.redraw(), ms=120)
        self._flash.jump(0.0)
        textvar.trace_add("write", lambda *_a: self._pulse())
        self.bind("<Configure>", lambda _e: self.redraw())
        self.redraw()

    def _pulse(self):
        """文字变化时闪一下：快速亮起、慢慢退掉。"""
        txt = self.textvar.get()
        if txt == self._last_text:
            return
        self._last_text = txt
        self._flash.jump(0.0)
        self._flash.to(1.0, 120, on_done=lambda: self._flash.to(0.0, 340))

    def redraw(self):
        p = self.ui.pal
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height() or self.H
        self._cw = w
        txt = self.textvar.get()
        if w <= 1:
            w = 108
        base = p["green"] if self.kind == "green" else p["red"]
        soft = p["green_soft"] if self.kind == "green" else mix(p["red"],
                                                               p["card"], 0.82)
        f = max(0.0, min(1.0, self._flash.value))
        round_rect(self, 0, 0, w - 1, h - 1, h / 2,
                   fill=mix(soft, base, 0.20 * f))
        cy = h / 2.0
        r = 4 + 1.4 * f
        self.create_oval(15 - r, cy - r, 15 + r, cy + r, fill=base, outline="")
        self.create_text(24, cy, text=txt, anchor="w", fill=base,
                         font=self.ui.f_small)

    def stop_anims(self):
        self._flash.stop()

    def retheme(self):
        self.configure(bg=self.ui.c(self._bg_key))
        self.redraw()


# --------------------------------------------------------------------- 数值输入

class ValueEntry(tk.Entry):
    """滑块旁边的数值框：扁平和卡片同色，回车/失焦提交。

    和普通 Entry 的区别：textvariable 用独立的 StringVar，双向同步到 IntVar /
    DoubleVar 上 —— 直接绑 tk.IntVar 会让非法输入把 Entry 搞乱。
    """

    def __init__(self, master, ui, var, is_int=True, on_commit=None, width=6,
                 **kw):
        self.ui = ui
        self.var = var
        self.is_int = is_int
        self.on_commit = on_commit
        self._text = tk.StringVar()
        kw.setdefault("relief", "flat")
        kw.setdefault("bd", 0)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("justify", "center")
        kw.setdefault("width", width)
        kw.setdefault("font", ui.f_num)
        super().__init__(master, textvariable=self._text, **kw)
        ui.extra(self)
        var.trace_add("write", lambda *_a: self._sync())
        self._sync()
        self.bind("<Return>", self._commit)
        self.bind("<FocusOut>", self._commit)

    # ---- 显示 ----
    def _sync(self):
        v = self.var.get()
        self._text.set("%d" % v if self.is_int else "%.2f" % float(v))

    # ---- 输入 ----
    def _commit(self, _evt=None):
        raw = self._text.get().strip()
        try:
            v = float(raw)
        except (TypeError, ValueError):
            self._sync()
            return
        if self.is_int:
            v = int(round(v))
        self.var.set(v)
        self._sync()
        if self.on_commit:
            self.on_commit()

    def retheme(self):
        p = self.ui.pal
        self.configure(bg=p["card_alt"], fg=p["text"],
                       insertbackground=p["text"],
                       selectbackground=p["accent"],
                       selectforeground=p["on_accent"])
