# -*- coding: utf-8 -*-
"""指针缩放 —— 图形界面

线程模型
--------
   主线程    tkinter 界面（tkinter 必须独占主线程）
   钩子线程  SetWindowsHookEx + RegisterHotKey + 消息循环
   动画线程  尺寸缓动（Animator 自带）

界面**不做任何跨线程回调**：tkinter 不是线程安全的，从钩子线程里调 after()
或改控件迟早会崩。统一用 after() 在主线程轮询读状态，简单且稳。

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

import pointer_zoom as pz  # noqa: E402

CURVES = sorted(pz.CURVES.keys())
TRIGGERS = [("left", "左键"), ("right", "右键"),
            ("middle", "中键"), ("both", "左右键")]

PRESS_COLOR = "#1f6feb"
RELEASE_COLOR = "#d2691e"
GRID_COLOR = "#e4e4e4"
AXIS_COLOR = "#9a9a9a"

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

        root.title("指针缩放")
        root.resizable(False, False)

        style = ttk.Style()
        for theme in ("vista", "winnative", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break

        self._build()
        self._start_engine()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(60, self._tick)

    # ------------------------------------------------------------ 界面构建

    def _build(self):
        pad = {"padx": 10, "pady": 4}
        outer = ttk.Frame(self.root, padding=10)
        outer.grid(row=0, column=0, sticky="nsew")

        # --- 状态 ---
        box = ttk.LabelFrame(outer, text=" 状态 ", padding=10)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)

        self.var_current = tk.StringVar(value="--")
        ttk.Label(box, textvariable=self.var_current,
                  font=("Segoe UI", 22)).grid(row=0, column=0, rowspan=2,
                                              sticky="w", padx=(0, 14))

        self.var_hint = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.var_hint,
                  foreground="#666").grid(row=0, column=1, sticky="sw")
        self.bar = tk.Canvas(box, height=10, width=250, highlightthickness=1,
                             highlightbackground="#d0d0d0", bg="#f4f4f4")
        self.bar.grid(row=1, column=1, sticky="w", pady=(2, 0))

        opts = ttk.Frame(box)
        opts.grid(row=0, column=2, rowspan=2, sticky="e")
        self.var_enabled = tk.BooleanVar(value=self.cfg["enabled"])
        ttk.Checkbutton(opts, text="启用", variable=self.var_enabled,
                        command=self._on_toggle_enabled).grid(row=0, column=0,
                                                              sticky="w")
        self.var_top = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="窗口置顶", variable=self.var_top,
                        command=lambda: self.root.attributes(
                            "-topmost", self.var_top.get())
                        ).grid(row=1, column=0, sticky="w")
        self.root.attributes("-topmost", True)

        # --- 大小 ---
        box = ttk.LabelFrame(outer, text=" 大小 ", padding=10)
        box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        box.columnconfigure(1, weight=1)

        self.var_idle = tk.IntVar(value=self.cfg["idle_size"])
        self._slider_row(box, 0, "平时大小", 1, pz.SIZE_MAX, self.var_idle, "px",
                         self._on_size_change)
        self.var_click = tk.IntVar(value=self.cfg["click_size"])
        self._slider_row(box, 1, "按下大小", 1, pz.SIZE_MAX, self.var_click, "px",
                         self._on_size_change)
        ttk.Label(box, text="平时 = 不点击时的大小；按下 = 按下鼠标时缩小到的目标",
                  foreground="#888").grid(row=2, column=0, columnspan=3,
                                          sticky="w", pady=(4, 0))

        # --- 动画 ---
        box = ttk.LabelFrame(outer, text=" 动画 ", padding=10)
        box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        box.columnconfigure(1, weight=1)

        self.var_press_ms = tk.IntVar(value=self.cfg["press_ms"])
        self._slider_row(box, 0, "按下时长", 0, 800, self.var_press_ms, "ms",
                         self._on_anim_change)
        self.var_release_ms = tk.IntVar(value=self.cfg["release_ms"])
        self._slider_row(box, 1, "松开时长", 0, 800, self.var_release_ms, "ms",
                         self._on_anim_change)
        self.var_fps = tk.IntVar(value=self.cfg["fps"])
        self._slider_row(box, 2, "帧率", 10, 144, self.var_fps, "fps",
                         self._on_anim_change)

        # --- 曲线 ---
        box = ttk.LabelFrame(outer, text=" 曲线 ", padding=10)
        box.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="按下").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.var_press_curve = tk.StringVar(value=self.cfg["press_curve"])
        cb1 = ttk.Combobox(box, textvariable=self.var_press_curve, values=CURVES,
                           state="readonly", width=22)
        cb1.grid(row=0, column=1, sticky="w")
        cb1.bind("<<ComboboxSelected>>", lambda e: self._on_curve_change())
        self.lbl_press_hint = ttk.Label(box, text="", foreground="#888")
        self.lbl_press_hint.grid(row=0, column=2, sticky="w", padx=(10, 0))

        ttk.Label(box, text="松开").grid(row=1, column=0, sticky="w",
                                         padx=(0, 8), pady=(4, 0))
        self.var_release_curve = tk.StringVar(value=self.cfg["release_curve"])
        cb2 = ttk.Combobox(box, textvariable=self.var_release_curve, values=CURVES,
                           state="readonly", width=22)
        cb2.grid(row=1, column=1, sticky="w", pady=(4, 0))
        cb2.bind("<<ComboboxSelected>>", lambda e: self._on_curve_change())
        self.lbl_release_hint = ttk.Label(box, text="", foreground="#888")
        self.lbl_release_hint.grid(row=1, column=2, sticky="w",
                                   padx=(10, 0), pady=(4, 0))

        self.var_strength = tk.DoubleVar(value=self.cfg["strength"])
        self._slider_row(box, 2, "回弹强度", 1.0, 3.0, self.var_strength, "",
                         self._on_curve_change, is_int=False)

        self.canvas = tk.Canvas(box, height=130, bg="white",
                                highlightthickness=1,
                                highlightbackground="#d0d0d0")
        self.canvas.grid(row=3, column=0, columnspan=3, sticky="ew",
                         pady=(8, 0))
        self.canvas.bind("<Configure>", lambda e: self.draw_preview())
        box.columnconfigure(0, weight=1)

        # --- 触发 ---
        box = ttk.LabelFrame(outer, text=" 触发按键 ", padding=10)
        box.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.var_trigger = tk.StringVar(value=self.cfg["trigger"])
        for i, (key, label) in enumerate(TRIGGERS):
            ttk.Radiobutton(box, text=label, value=key,
                            variable=self.var_trigger,
                            command=self._on_trigger_change
                            ).grid(row=0, column=i, padx=(0, 12))

        # --- 按钮 ---
        row = ttk.Frame(outer)
        row.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(row, text="试一下", command=self.preview).grid(row=0, column=0)
        ttk.Button(row, text="恢复原始大小", command=self.reset
                   ).grid(row=0, column=1, padx=6)
        ttk.Button(row, text="保存配置", command=self.save_now
                   ).grid(row=0, column=2)
        ttk.Button(row, text="退出", command=self.on_close
                   ).grid(row=0, column=3, padx=(6, 0))

        self.lbl_footer = ttk.Label(outer, text="", foreground="#888")
        self.lbl_footer.grid(row=6, column=0, sticky="w", pady=(8, 0))

        self._refresh_curve_hints()
        self.root.after(30, self.draw_preview)

    def _slider_row(self, parent, row, label, lo, hi, var, suffix, callback,
                    is_int=True):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w",
                                           padx=(0, 8), pady=3)

        def on_move(_value):
            v = int(round(var.get())) if is_int else round(var.get(), 2)
            if is_int and var.get() != v:
                var.set(v)
            callback()

        scale = ttk.Scale(parent, from_=lo, to=hi, variable=var,
                          orient="horizontal", command=on_move, length=260)
        scale.grid(row=row, column=1, sticky="ew", pady=3)

        holder = ttk.Frame(parent)
        holder.grid(row=row, column=2, sticky="w", padx=(8, 0))
        spin = ttk.Spinbox(holder, from_=lo, to=hi, textvariable=var,
                           width=5, command=callback,
                           increment=1 if is_int else 0.05)
        spin.grid(row=0, column=0)
        if suffix:
            ttk.Label(holder, text=suffix, foreground="#666"
                      ).grid(row=0, column=1, padx=(3, 0))

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
        c["click_size"] = max(pz.SIZE_MIN, min(pz.SIZE_MAX, self.var_click.get()))
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
        self._flash(f"已恢复原始大小 {self.app.original}px")

    def save_now(self):
        self._apply()
        pz.save_config(self.cfg_path, self.cfg)
        self._flash(f"配置已保存到 {os.path.basename(self.cfg_path)}")

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
        # 窗口还没映射时 winfo_width() 返回 1，不能用 `or` 兜底（1 是真值）
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w <= 1:
            w = 440
        if h <= 1:
            h = 130
        left, right, top, bottom = 42, w - 12, 12, h - 20

        c = self.cfg
        press_ms = max(1, c["press_ms"])
        release_ms = max(1, c["release_ms"])
        span = max(press_ms, release_ms)

        def sample(curve_name, ms, frm, to):
            fn = pz.CURVES.get(curve_name, pz.CURVES["linear"])
            pts = []
            steps = max(8, int(ms / 8))
            for i in range(steps + 1):
                t = i / steps
                v = fn(t)
                size = frm + (to - frm) * v
                pts.append((t * ms, max(pz.SIZE_FLOOR,
                                        min(pz.SIZE_MAX, size))))
            return pts

        press = sample(c["press_curve"], press_ms, c["idle_size"], c["click_size"])
        release = sample(c["release_curve"], release_ms, c["click_size"],
                         c["idle_size"])
        all_sizes = [s for _, s in press] + [s for _, s in release]
        lo = min(all_sizes) - 6
        hi = max(all_sizes) + 6
        if hi - lo < 10:
            lo, hi = lo - 5, hi + 5

        def px(t_ms, size):
            x = left + (t_ms / span) * (right - left)
            y = bottom - (size - lo) / (hi - lo) * (bottom - top)
            return x, y

        for frac in (0.25, 0.5, 0.75):
            y = bottom - frac * (bottom - top)
            cv.create_line(left, y, right, y, fill=GRID_COLOR)
        cv.create_line(left, bottom, right, bottom, fill=AXIS_COLOR)
        cv.create_line(left, top, left, bottom, fill=AXIS_COLOR)

        cv.create_text(left - 6, top, text=f"{hi:.0f}", anchor="e",
                       fill="#999", font=("Segoe UI", 8))
        cv.create_text(left - 6, bottom, text=f"{lo:.0f}", anchor="e",
                       fill="#999", font=("Segoe UI", 8))
        cv.create_text(left, h - 8, text="0ms", anchor="w",
                       fill="#999", font=("Segoe UI", 8))
        cv.create_text(right, h - 8, text=f"{span:.0f}ms", anchor="e",
                       fill="#999", font=("Segoe UI", 8))

        for pts, color in ((press, PRESS_COLOR), (release, RELEASE_COLOR)):
            flat = [coord for p in pts for coord in px(*p)]
            cv.create_line(*flat, fill=color, width=2, smooth=True,
                           capstyle="round", joinstyle="round")
            x0, y0 = px(*pts[0])
            x1, y1 = px(*pts[-1])
            cv.create_oval(x0 - 3, y0 - 3, x0 + 3, y0 + 3, fill=color,
                           outline=color)
            cv.create_oval(x1 - 3, y1 - 3, x1 + 3, y1 + 3, fill=color,
                           outline=color)

        cv.create_line(right - 150, 16, right - 138, 16, fill=PRESS_COLOR,
                       width=2)
        cv.create_text(right - 132, 16, text=f"按下 {press_ms:.0f}ms",
                       anchor="w", fill="#555", font=("Segoe UI", 8))
        cv.create_line(right - 150, 30, right - 138, 30, fill=RELEASE_COLOR,
                       width=2)
        cv.create_text(right - 132, 30, text=f"松开 {release_ms:.0f}ms",
                       anchor="w", fill="#555", font=("Segoe UI", 8))

    # ------------------------------------------------------------ 轮询刷新

    def _tick(self):
        cur = self.app.anim.current
        self.var_current.set(f"{cur:.0f}")
        state = "按住" if self.app.pressed else ("启用" if self.app.enabled
                                               else "已停用")
        self.var_hint.set(f"px   {state}")

        w = self.bar.winfo_width()
        if w <= 1:
            w = 250
        frac = max(0.0, min(1.0, cur / pz.SIZE_MAX))
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, w * frac, 12,
                                  fill=PRESS_COLOR if self.app.pressed
                                  else "#4c9a4c", outline="")

        if self.var_enabled.get() != self.app.enabled:
            self.var_enabled.set(self.app.enabled)

        if (self.hook_thread and self.hook_thread.error
                and not self._hook_error_reported):
            self._hook_error_reported = True
            self.lbl_footer.config(
                text=f"钩子启动失败：{self.hook_thread.error}", foreground="red")

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
