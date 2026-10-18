# -*- coding: utf-8 -*-
r"""指针缩放 —— 图形界面（v1.1 新版面）

线程模型
--------
   主线程    tkinter 界面（tkinter 必须独占主线程）
   钩子线程  SetWindowsHookEx + RegisterHotKey + 消息循环
   动画线程  尺寸缓动（Animator 自带）

界面**不做任何跨线程回调**：tkinter 不是线程安全的，从钩子线程里调 after()
或改控件迟早会崩。统一用 after() 在主线程轮询读状态，简单且稳。

布局
----
   头部          标题 + 副标题 + 日间/夜间切换 + 状态胶囊
   状态卡        当前字号大字、进度条、启用 / 窗口置顶开关
   大小卡        平时大小、按下大小
   动画卡        按下时长、松开时长、帧率
   曲线卡        两条曲线、回弹强度、尺寸轨迹预览图
   触发按键卡    分段选择器
   底部          按钮 + 一行操作反馈

外观层在 `pz_ui.py`：颜色/圆角/自绘控件都在那儿，本文件只管排布和取值。
所有普通 tk 控件都经 `_lbl()` / `_frm()` 创建，它们会向 UI 登记颜色键，
换主题时统一回填。

用法
----
    pythonw pointer_gui.py
    python  pointer_gui.py --config ..\\pointer-zoom.ini
"""

import argparse
import os
import sys
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pointer_zoom as pz   # noqa: E402
import pz_ui as ui           # noqa: E402

VERSION = "1.1.0"
SUBTITLE = "按下鼠标时动画缩放系统光标"

CARD_W = 528                 # 卡片宽；窗口 = CARD_W + 左右外边距

CURVES = sorted(pz.CURVES.keys())
TRIGGERS = [("left", "左键"), ("right", "右键"),
            ("middle", "中键"), ("both", "左右键")]

HINT = {
    "linear": "匀速",
    "ease_out_quad": "快出慢停",
    "ease_out_cubic": "减速明显",
    "ease_out_quart": "一按到位",
    "ease_out_expo": "瞬间到达",
    "ease_out_back": "冲过头再弹回",
    "ease_in_out_back": "两端回弹",
    "ease_out_elastic": "橡皮筋抖动",
    "ease_out_bounce": "落地弹跳",
    "ease_in_out_cubic": "两头慢中间快",
}


class ZoomGui:
    def __init__(self, root, cfg, cfg_path):
        self.root = root
        self.cfg_path = cfg_path
        self.cfg = dict(cfg)
        self.app = pz.PointerZoom(self.cfg)
        self.hook_thread = None
        self._save_job = None
        self._hook_error_reported = False
        self.ui = ui.UI(root, "light")

        root.title("指针缩放 " + VERSION)
        root.resizable(False, False)
        self.ui.reg(root, bg="bg")
        self._style_ttk()

        self._build()
        self._start_engine()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(60, self._tick)
        self._center()

    # ------------------------------------------------------------ 小工具

    def _frm(self, parent, bg="bg"):
        """建一个会跟随换主题的 Frame。"""
        return self.ui.reg(tk.Frame(parent), bg=bg)

    def _lbl(self, parent, text, fg="text", bg="bg", font="f_label",
             anchor="w", **kw):
        """建一个会跟随换主题的 Label。"""
        w = tk.Label(parent, text=text, font=getattr(self.ui, font),
                     anchor=anchor, **kw)
        return self.ui.reg(w, bg=bg, fg=fg)

    def _style_ttk(self):
        """只用了 Combobox，把它压平成扁平观感。"""
        p = self.ui.pal
        try:
            st = ttk.Style()
            if "clam" in st.theme_names():
                st.theme_use("clam")
            st.configure("TCombobox", padding=5, arrowsize=14)
            st.map("TCombobox",
                   fieldbackground=[("readonly", "!disabled", p["card_alt"])],
                   background=[("readonly", "!disabled", p["card"])],
                   foreground=[("readonly", "!disabled", p["text"])])
        except Exception:
            pass

    def _center(self):
        root = self.root
        root.update_idletasks()
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w = root.winfo_width() or root.winfo_reqwidth()
        h = root.winfo_height() or root.winfo_reqheight()
        root.geometry("+%d+%d" % (max(0, (sw - w) // 2), max(0, (sh - h) // 3)))

    def _card(self, outer, pady=0):
        """建一张满宽卡片并返回它。"""
        card = ui.Card(outer, self.ui, width=CARD_W)
        card.pack(fill="x", pady=(pady, 0))
        return card

    # ------------------------------------------------------------ 界面构建

    def _build(self):
        p = self.ui.pal
        outer = self._frm(self.root)
        outer.pack(fill="both", expand=True, padx=18, pady=16)

        # ---------------- 头部 ----------------
        head = self._frm(outer)
        head.pack(fill="x", pady=(0, 14))

        left = self._frm(head)
        left.pack(side="left", anchor="w")
        self._lbl(left, "指针缩放", fg="text", font="f_title").pack(anchor="w")
        self._lbl(left, "v%s  ·  %s" % (VERSION, SUBTITLE), fg="text2",
                  font="f_sub").pack(anchor="w")

        right = self._frm(head)
        right.pack(side="right", anchor="e")
        self.var_theme = tk.StringVar(value="light")
        ui.Segmented(right, self.ui, [("light", "日间"), ("dark", "夜间")],
                     self.var_theme, command=self._on_theme_change,
                     bg="bg").pack(anchor="e")

        # ---------------- 状态 ----------------
        card = self._card(outer)
        self._build_status(card)
        card.fit()

        # ---------------- 大小 ----------------
        card = self._card(outer, pady=10)
        self._build_size(card)
        card.fit()

        # ---------------- 动画 ----------------
        card = self._card(outer, pady=10)
        self._build_anim(card)
        card.fit()

        # ---------------- 曲线 ----------------
        card = self._card(outer, pady=10)
        self._build_curve(card)
        card.fit()

        # ---------------- 触发按键 ----------------
        card = self._card(outer, pady=10)
        self._build_trigger(card)
        card.fit()

        # ---------------- 底部 ----------------
        foot = self._frm(outer)
        foot.pack(fill="x", pady=(14, 0))

        row = self._frm(foot)
        row.pack(fill="x")
        ui.Button(row, self.ui, "试一下", command=self.preview,
                  kind="secondary", bg="bg").pack(side="left")
        ui.Button(row, self.ui, "恢复原始大小", command=self.reset,
                  kind="secondary", bg="bg").pack(side="left", padx=(8, 0))
        ui.Button(row, self.ui, "退出", command=self.on_close,
                  kind="ghost", bg="bg").pack(side="right")
        ui.Button(row, self.ui, "保存配置", command=self.save_now,
                  kind="primary", bg="bg").pack(side="right", padx=(0, 8))

        self.lbl_footer = self._lbl(foot, "", fg="text2", font="f_small")
        self.lbl_footer.pack(fill="x", pady=(8, 0))

    # ---- 状态卡 ----

    def _build_status(self, card):
        u = self.ui
        p = u.pal
        inner = card.inner
        inner.columnconfigure(1, weight=1)

        num = self._frm(inner, bg="card")
        num.grid(row=0, column=0, rowspan=2, sticky="w")
        self.var_current = tk.StringVar(value="--")
        big = tk.Label(num, textvariable=self.var_current, font=self.ui.f_big,
                       bg=p["card"], fg=p["text"], anchor="sw")
        self.ui.reg(big, bg="card", fg="text")
        big.pack(side="left")
        self._lbl(num, "px", fg="text2", bg="card", font="f_num",
                  anchor="sw").pack(side="left", pady=(0, 8))

        self.var_hint = tk.StringVar(value="")
        hint = self._lbl(inner, "", fg="text2", bg="card", font="f_small")
        hint.grid(row=0, column=1, sticky="sw", padx=(16, 0))
        hint.configure(textvariable=self.var_hint)

        self.bar = tk.Canvas(inner, height=8, bg=p["card"], highlightthickness=0,
                             bd=0)
        self.bar.grid(row=1, column=1, sticky="ew", padx=(16, 0))
        self.bar.bind("<Configure>", lambda _e: self._draw_bar())

        self.var_state = tk.StringVar(value="")
        pill = ui.StatusPill(inner, u, self.var_state, kind="green")
        pill.grid(row=0, column=2, rowspan=2, sticky="e", padx=(16, 0))
        pill.configure(width=96)

        sep = tk.Frame(inner, bg=p["border"], height=1)
        sep.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(14, 0))

        # 两个开关并排放，省一行高度
        row = self._frm(inner, bg="card")
        row.grid(row=3, column=0, columnspan=3, sticky="w", pady=(14, 0))
        self.var_enabled = tk.BooleanVar(value=self.cfg["enabled"])
        ui.Switch(row, u, self.var_enabled, command=self._on_toggle_enabled
                  ).pack(side="left")
        self._lbl(row, "启用", fg="text", bg="card").pack(side="left",
                                                          padx=(8, 0))

        self.var_top = tk.BooleanVar(value=True)
        ui.Switch(row, u, self.var_top,
                  command=lambda: self.root.attributes(
                      "-topmost", self.var_top.get())).pack(side="left",
                                                            padx=(26, 0))
        self.root.attributes("-topmost", True)
        self._lbl(row, "窗口置顶", fg="text2", bg="card").pack(side="left",
                                                               padx=(8, 0))

    # ---- 大小卡 ----

    def _build_size(self, card):
        card.title("大小").grid(row=0, column=0, sticky="w", pady=(0, 10))
        card.inner.columnconfigure(1, weight=1)

        self.var_idle = tk.IntVar(value=self.cfg["idle_size"])
        self._slider_row(card, 1, "平时", 1, pz.SIZE_MAX, self.var_idle,
                         "px", self._on_size_change)
        self.var_click = tk.IntVar(value=self.cfg["click_size"])
        self._slider_row(card, 2, "按下", 1, pz.SIZE_MAX, self.var_click,
                         "px", self._on_size_change)

        card.caption("平时 = 不点击时的大小；按下 = 按住鼠标时缩小到的大小"
                     ).grid(row=3, column=0, columnspan=4, sticky="w",
                            pady=(10, 0))

    # ---- 动画卡 ----

    def _build_anim(self, card):
        card.title("动画").grid(row=0, column=0, sticky="w", pady=(0, 10))
        card.inner.columnconfigure(1, weight=1)

        self.var_press_ms = tk.IntVar(value=self.cfg["press_ms"])
        self._slider_row(card, 1, "按下", 0, 800, self.var_press_ms, "ms",
                         self._on_anim_change)
        self.var_release_ms = tk.IntVar(value=self.cfg["release_ms"])
        self._slider_row(card, 2, "松开", 0, 800, self.var_release_ms, "ms",
                         self._on_anim_change)
        self.var_fps = tk.IntVar(value=self.cfg["fps"])
        self._slider_row(card, 3, "帧率", 10, 144, self.var_fps, "fps",
                         self._on_anim_change)

    # ---- 曲线卡 ----

    def _build_curve(self, card):
        inner = card.inner
        card.title("缓动曲线").grid(row=0, column=0, columnspan=4,
                                    sticky="w", pady=(0, 10))
        inner.columnconfigure(1, weight=1)

        for row, key, who in ((1, "press_curve", "按下"),
                              (2, "release_curve", "松开")):
            card.text_label(who).grid(row=row, column=0, sticky="w",
                                      padx=(0, 10))
            var = tk.StringVar(value=self.cfg[key])
            cb = ttk.Combobox(inner, textvariable=var, values=CURVES,
                              state="readonly", width=24, font=self.ui.f_label)
            cb.grid(row=row, column=1, sticky="w")
            cb.bind("<<ComboboxSelected>>",
                    lambda _e: self._on_curve_change())
            hint = self._lbl(inner, "", fg="text3", bg="card", font="f_small")
            hint.grid(row=row, column=2, columnspan=2, sticky="w",
                      padx=(12, 0))
            var.trace_add("write", lambda *_a: self._refresh_curve_hints())
            if row == 1:
                self.var_press_curve = var
                self.lbl_press_hint = hint
            else:
                self.var_release_curve = var
                self.lbl_release_hint = hint

        self.var_strength = tk.DoubleVar(value=self.cfg["strength"])
        self._slider_row(card, 3, "回弹强度", 1.0, 3.0, self.var_strength, "",
                         self._on_curve_change, is_int=False,
                         entry_col=3, suffix_col=None)

        self.canvas = tk.Canvas(inner, height=152, bg=self.ui.c("card"),
                                highlightthickness=1,
                                highlightbackground=self.ui.c("border"))
        self.canvas.grid(row=4, column=0, columnspan=4, sticky="ew",
                         pady=(12, 0))
        self.canvas.bind("<Configure>", lambda _e: self.draw_preview())

    # ---- 触发按键卡 ----

    def _build_trigger(self, card):
        card.title("触发按键").grid(row=0, column=0, sticky="w",
                                    pady=(0, 10))
        card.inner.columnconfigure(1, weight=1)
        self.var_trigger = tk.StringVar(value=self.cfg["trigger"])
        ui.Segmented(card.inner, self.ui, TRIGGERS, self.var_trigger,
                     command=self._on_trigger_change,
                     height=32).grid(row=1, column=0, sticky="w")
        card.caption("按住鼠标哪个键时触发缩小"
                     ).grid(row=2, column=0, columnspan=4, sticky="w",
                            pady=(8, 0))

    # ---- 滑块行 ----

    def _slider_row(self, card, row, label, lo, hi, var, suffix, callback,
                    is_int=True, entry_col=2, suffix_col=3):
        parent = card.inner
        card.text_label(label).grid(row=row, column=0, sticky="w",
                                    padx=(0, 10), pady=(5, 0))
        ui.Slider(parent, self.ui, var, lo, hi, is_int=is_int,
                  command=callback).grid(row=row, column=1, sticky="ew",
                                         pady=(5, 0))
        ui.ValueEntry(parent, self.ui, var, is_int=is_int, on_commit=callback,
                      width=5).grid(row=row, column=entry_col, sticky="w",
                                    padx=(10, 0) if entry_col == 2 else 0,
                                    pady=(5, 0))
        if suffix and suffix_col is not None:
            self._lbl(parent, suffix, fg="text2", bg="card", font="f_small"
                      ).grid(row=row, column=suffix_col, sticky="w",
                             padx=(4, 0), pady=(5, 0))

    # ------------------------------------------------------------ 引擎控制

    def _start_engine(self):
        self.app.start()
        self.hook_thread = pz.HookThread(self.app)
        self.hook_thread.start()

    # ------------------------------------------------------------ 事件处理

    def _apply(self):
        """把界面上的值同步进配置对象，并立刻生效。"""
        c = self.cfg
        c["idle_size"] = max(pz.SIZE_MIN, min(pz.SIZE_MAX, self.var_idle.get()))
        c["click_size"] = max(pz.SIZE_MIN,
                              min(pz.SIZE_MAX, self.var_click.get()))
        c["press_ms"] = max(0, self.var_press_ms.get())
        c["release_ms"] = max(0, self.var_release_ms.get())
        c["fps"] = max(10, min(144, self.var_fps.get()))
        c["press_curve"] = self.var_press_curve.get()
        c["release_curve"] = self.var_release_curve.get()
        c["strength"] = round(self.var_strength.get(), 2)
        c["trigger"] = self.var_trigger.get()
        c["enabled"] = self.var_enabled.get()
        self.app.anim.fps = c["fps"]
        # broadcast 没有对应的界面控件，但要原样透传给动画线程
        c["broadcast"] = c.get("broadcast", pz.BROADCAST_SETTLE)
        self.app.anim.broadcast = c["broadcast"]
        self._schedule_save()

    def _on_size_change(self):
        self._apply()
        # 没按住时，平时大小要立刻体现在指针上
        if not self.app.pressed:
            self.app.anim.snap(self.cfg["idle_size"] if self.app.enabled
                               else self.app.original)
        self.draw_preview()

    def _on_anim_change(self):
        self._apply()
        self.draw_preview()

    def _on_curve_change(self):
        self._apply()
        self._refresh_curve_hints()
        self.draw_preview()

    def _on_trigger_change(self):
        self._apply()

    def _on_toggle_enabled(self):
        self.app.enabled = self.var_enabled.get()
        self._apply()
        if self.app.enabled:
            self.app.anim.snap(self.cfg["idle_size"])
        else:
            self.app.pressed = False
            self.app.anim.snap(self.app.original)

    def _on_theme_change(self):
        self.ui.set_theme(self.var_theme.get())
        self._style_ttk()
        self.draw_preview()
        self._draw_bar()

    def _refresh_curve_hints(self):
        self.lbl_press_hint.config(
            text=HINT.get(self.var_press_curve.get(), ""))
        self.lbl_release_hint.config(
            text=HINT.get(self.var_release_curve.get(), ""))

    # ------------------------------------------------------------ 操作

    def preview(self):
        """跑一遍按下 + 松开的完整动画，不用真的点鼠标。"""
        self._apply()
        c = self.cfg
        self.app.pressed = False
        self.app.anim.goto(c["idle_size"], c["release_ms"],
                           c["release_curve"], c["strength"])
        self.root.after(c["release_ms"] + 120,
                        lambda: self.app.anim.goto(
                            c["click_size"], c["press_ms"],
                            c["press_curve"], c["strength"]))
        self.root.after(c["release_ms"] + c["press_ms"] + 320,
                        lambda: self.app.anim.goto(
                            c["idle_size"], c["release_ms"],
                            c["release_curve"], c["strength"]))

    def reset(self):
        self.app.pressed = False
        self.app.anim.snap(self.app.original)
        self._flash("已恢复原始大小 %dpx" % self.app.original)

    def save_now(self):
        self._apply()
        pz.save_config(self.cfg_path, self.cfg)
        self._flash("配置已保存到 %s" % os.path.basename(self.cfg_path))

    def _schedule_save(self):
        if self._save_job:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(800, self._auto_save)

    def _auto_save(self):
        self._save_job = None
        pz.save_config(self.cfg_path, self.cfg)

    def _flash(self, text):
        self.lbl_footer.config(text=text)
        self.root.after(2500, lambda: self.lbl_footer.config(text=""))

    # ------------------------------------------------------------ 曲线预览

    def draw_preview(self):
        cv = self.canvas
        cv.delete("all")
        u = self.ui
        p = u.pal

        w = cv.winfo_width()
        h = cv.winfo_height()
        if w <= 1:
            w = 452
        if h <= 1:
            h = 152
        left, right = 40, w - 14
        top, bottom = 14, h - 24

        c = self.cfg
        press_ms = max(1, c["press_ms"])
        release_ms = max(1, c["release_ms"])
        span = max(press_ms, release_ms)

        def sample(curve_name, ms, frm, to):
            fn = pz.CURVES.get(curve_name, pz.CURVES["linear"])
            pts = []
            steps = max(12, int(ms / 6))
            for i in range(steps + 1):
                t = i / steps
                size = frm + (to - frm) * fn(t)
                pts.append((t * ms, max(pz.SIZE_FLOOR,
                                        min(pz.SIZE_MAX, size))))
            return pts

        press = sample(c["press_curve"], press_ms, c["idle_size"],
                       c["click_size"])
        release = sample(c["release_curve"], release_ms, c["click_size"],
                         c["idle_size"])
        all_sizes = [s for _, s in press] + [s for _, s in release]
        lo = min(all_sizes) - 5
        hi = max(all_sizes) + 5
        if hi - lo < 12:
            lo, hi = lo - 6, hi + 6

        def px(t_ms, size):
            x = left + (t_ms / span) * (right - left)
            y = bottom - (size - lo) / (hi - lo) * (bottom - top)
            return x, y

        # 网格 + 坐标轴
        for i in (1, 2, 3):
            y = bottom - i * (bottom - top) / 4.0
            cv.create_line(left, y, right, y, fill=p["border"])
        cv.create_line(left, top, left, bottom, fill=p["border"])
        cv.create_line(left, bottom, right, bottom, fill=p["border"])

        cv.create_text(left - 8, top, text="%.0f" % hi, anchor="e",
                       fill=p["text3"], font=u.f_axis)
        cv.create_text(left - 8, bottom, text="%.0f" % lo, anchor="e",
                       fill=p["text3"], font=u.f_axis)
        cv.create_text(left, h - 9, text="0ms", anchor="w",
                       fill=p["text3"], font=u.f_axis)
        cv.create_text(right, h - 9, text="%.0fms" % span, anchor="e",
                       fill=p["text3"], font=u.f_axis)

        # 面积 + 曲线
        for pts, key in ((press, "press"), (release, "release")):
            color = p[key]
            soft = ui.tint(color, p["card"], 0.90)
            flat = [coord for (t, s) in pts for coord in px(t, s)]
            x_first, y_first = px(*pts[0])
            x_last, y_last = px(*pts[-1])
            cv.create_polygon(flat + [x_last, bottom, x_first, bottom],
                              fill=soft, outline="")
            cv.create_line(*flat, fill=color, width=2, smooth=True,
                           capstyle=tk.ROUND, joinstyle=tk.ROUND)
            for x, y in ((x_first, y_first), (x_last, y_last)):
                cv.create_oval(x - 3.2, y - 3.2, x + 3.2, y + 3.2,
                               fill=color, outline=p["card"], width=1.2)

        # 图例
        lx = right - 92
        cv.create_line(lx, 14, lx + 14, 14, fill=p["press"], width=2)
        cv.create_text(lx + 19, 14, text="按下", anchor="w", fill=p["text2"],
                       font=u.f_small)
        cv.create_line(lx, 28, lx + 14, 28, fill=p["release"], width=2)
        cv.create_text(lx + 19, 28, text="松开", anchor="w", fill=p["text2"],
                       font=u.f_small)

    # ------------------------------------------------------------ 轮询刷新

    def _draw_bar(self):
        w = self.bar.winfo_width()
        if w <= 1:
            return
        p = self.ui.pal
        cur = self.app.anim.current
        frac = max(0.0, min(1.0, cur / pz.SIZE_MAX))
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, w * frac, 8,
                                  fill=p["press"] if self.app.pressed
                                  else p["green"], outline="")
        self.bar.create_rectangle(w * frac, 0, w, 8, fill=p["track"],
                                  outline="")

    def _tick(self):
        cur = self.app.anim.current
        self.var_current.set("%d" % round(cur))
        c = self.cfg
        self.var_hint.set("平时 %d → 按下 %d"
                          % (c["idle_size"], c["click_size"]))
        self.var_state.set("已启用" if self.app.enabled else "已停用")

        self._draw_bar()

        if self.var_enabled.get() != self.app.enabled:
            self.var_enabled.set(self.app.enabled)

        if (self.hook_thread and self.hook_thread.error
                and not self._hook_error_reported):
            self._hook_error_reported = True
            self.lbl_footer.config(text="钩子启动失败：%s"
                                   % self.hook_thread.error)
            self.lbl_footer.config(foreground=self.ui.pal["red"])

        self.root.after(60, self._tick)

    # ------------------------------------------------------------ 退出

    def on_close(self):
        try:
            self._apply()
            if self._save_job:
                self.root.after_cancel(self._save_job)
            pz.save_config(self.cfg_path, self.cfg)
            if self.hook_thread:
                self.hook_thread.stop()
            self.app.restore()
        finally:
            self.root.destroy()


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_ini = os.path.join(os.path.dirname(here), "pointer-zoom.ini")
    parser = argparse.ArgumentParser(description="指针缩放图形界面")
    parser.add_argument("--config", default=default_ini)
    args = parser.parse_args()

    cfg = pz.load_config(args.config)
    root = tk.Tk()
    ZoomGui(root, cfg, args.config)
    root.mainloop()


if __name__ == "__main__":
    main()
