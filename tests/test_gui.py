# -*- coding: utf-8 -*-
"""图形界面冒烟测试 —— 真的把窗口建起来、跑几轮事件循环、把每个控件都动一遍。

跑的时候窗口会闪现一下，指针也会按配置动几下，结束后自动还原。

    python test_gui.py
"""

import os
import sys
import tempfile
import time
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pointer_zoom as pz      # noqa: E402
import pointer_gui as pg       # noqa: E402
import pz_ui as ui             # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


def pump(root, seconds=0.25):
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.01)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = pz.load_config(os.path.join(ROOT, "pointer-zoom.ini"))
    tmp_ini = os.path.join(tempfile.gettempdir(), "pointer-zoom-test.ini")

    print("=" * 62)
    print("图形界面冒烟测试")
    print("=" * 62)

    root = tk.Tk()
    gui = None
    # tkinter 回调里抛出的异常只会往 stderr 刷一段 traceback，界面照样「正常」显示，
    # 极难发现（v1.1 的按钮就踩过：mix() 收到色板键名，Configure 事件里炸掉）。
    # 装一个捕获器，把这类问题变成可断言的失败。
    cb_errors = []

    def _record_exc(exc, val, tb):
        cb_errors.append("%s: %s" % (exc.__name__, val))

    root.report_callback_exception = _record_exc
    try:
        gui = pg.ZoomGui(root, cfg, tmp_ini)
        pump(root, 0.6)

        check("窗口与所有控件构建成功", True)
        check("画布已绘制曲线", len(gui.canvas.find_all()) > 5,
              f"{len(gui.canvas.find_all())} 个图元")

        print("\n1) 每个控件都动一遍")
        gui.var_idle.set(120)
        gui._on_size_change()
        pump(root, 0.15)
        check("平时大小 -> 120px 已生效",
              gui.cfg["idle_size"] == 120 and abs(gui.app.anim.current - 120) < 3,
              f"current={gui.app.anim.current:.1f}")

        gui.var_click.set(24)
        gui._on_size_change()
        check("按下大小 -> 24px", gui.cfg["click_size"] == 24)

        gui.var_press_ms.set(90)
        gui.var_release_ms.set(300)
        gui.var_fps.set(30)
        gui._on_anim_change()
        check("时长 / 帧率已同步",
              gui.cfg["press_ms"] == 90 and gui.cfg["release_ms"] == 300
              and gui.cfg["fps"] == 30 and gui.app.anim.fps == 30)

        gui.var_trigger.set("both")
        gui._on_trigger_change()
        check("触发键 -> 左右键", gui.cfg["trigger"] == "both")

        gui.var_strength.set(2.4)
        gui._on_curve_change()
        check("回弹强度 -> 2.4", abs(gui.cfg["strength"] - 2.4) < 1e-6)

        print("\n2) 17 条曲线全部画一遍")
        bad = []
        for name in pg.CURVES:
            gui.var_press_curve.set(name)
            gui.var_release_curve.set(name)
            gui._on_curve_change()
            root.update()
            if len(gui.canvas.find_all()) < 5:
                bad.append(name)
        check("全部曲线预览绘制无异常", not bad, f"异常: {bad}" if bad else "17 条")

        print("\n3) 预览动画跑一遍")
        gui.var_press_curve.set("ease_out_cubic")
        gui.var_release_curve.set("ease_out_back")
        gui._on_curve_change()
        gui.preview()
        pump(root, 1.4)
        check("预览后回到平时大小",
              abs(gui.app.anim.current - gui.cfg["idle_size"]) < 3,
              f"current={gui.app.anim.current:.1f}px")

        print("\n4) 启用开关")
        gui.var_enabled.set(False)
        gui._on_toggle_enabled()
        pump(root, 0.2)
        check("停用后指针恢复原始大小",
              abs(gui.app.anim.current - gui.app.original) < 3,
              f"current={gui.app.anim.current:.1f}px  original={gui.app.original}")
        gui.var_enabled.set(True)
        gui._on_toggle_enabled()
        pump(root, 0.2)
        check("重新启用后回到平时大小",
              abs(gui.app.anim.current - gui.cfg["idle_size"]) < 3)

        print("\n5) 保存配置并回读")
        gui.save_now()
        check("配置文件已写出", os.path.exists(tmp_ini))
        reloaded = pz.load_config(tmp_ini)
        same = all(reloaded[k] == gui.cfg[k] for k in
                   ("idle_size", "click_size", "press_ms", "release_ms",
                    "fps", "trigger", "press_curve", "release_curve"))
        check("保存后回读一致", same,
              f"idle={reloaded['idle_size']} click={reloaded['click_size']} "
              f"curve={reloaded['press_curve']}")
        check("回弹强度回读一致",
              abs(reloaded["strength"] - gui.cfg["strength"]) < 1e-6)

        print("\n6) 钩子线程状态")
        check("钩子线程已启动", gui.hook_thread is not None
              and gui.hook_thread.is_alive())
        check("钩子无错误", gui.hook_thread.error is None,
              f"{gui.hook_thread.error}")
        # 快捷键可能被其它程序占用，那是环境问题不是代码问题；
        # 这里只断言注册链路本身没坏（解析 + RegisterHotKey + 降级都跑通了）。
        registered = [k for k, v in gui.app.hotkeys.items() if v]
        check(f"快捷键注册链路正常（{len(registered)}/3 成功）",
              len(registered) >= 1, f"{gui.app.hotkeys}")

        print("\n7) 自绘控件与滚动区")
        check("前半段无回调异常", not cb_errors, f"{cb_errors[:2]}")

        # 先快照，再往下用 —— 后面要建临时控件，别把它混进来
        canvases = [o for o in gui.ui._extras
                    if isinstance(o, tk.Canvas) and o is not gui.canvas]
        empties = [type(o).__name__ for o in canvases if not o.find_all()]
        check("自绘控件都画出了内容", not empties,
              f"空白: {empties}" if empties else "%d 个" % len(canvases))

        # 三种按钮各建一遍，跑完 update 再看图元数 —— 光建出来不算，
        # 真正的 bug 藏在 Configure 事件的 redraw 里。
        # 注意：不能往 root 里 pack（主窗口已被 geometry/minsize 固定，
        # 新 pack 进去的控件会被压成 1x1 且不映射），得用独立 Toplevel。
        top = tk.Toplevel(root)
        top.withdraw()
        kinds = {}
        for kind in ("primary", "secondary", "ghost"):
            b = ui.Button(top, gui.ui, kind, kind=kind, bg="bg")
            b.pack()
            kinds[kind] = b
        top.deiconify()
        pump(root, 0.3)
        bad = [k for k, b in kinds.items() if len(b.find_all()) < 2]
        check("三种按钮都画全了", not bad, f"异常: {bad}" if bad else "3 种")
        top.destroy()
        pump(root, 0.15)

        # 滚动：内容必须比可视区高，scrollregion 要覆盖全部内容
        vis = gui.scroll.winfo_height()
        total = gui.viewport.winfo_reqheight()
        check("滚动区已启用", vis > 1 and total > vis,
              f"可视 {vis} / 内容 {total}")
        region = gui.scroll.cget("scrollregion")
        check("scrollregion 覆盖全部内容", str(int(total)) in region, region)
        check("滚动前位于顶部", gui.scroll.yview()[0] < 1e-6)
        gui.scroll.yview_moveto(1.0)
        pump(root, 0.25)
        check("可以滚到底", gui.scroll.yview()[1] > 0.999, f"{gui.scroll.yview()}")
        gui.scroll.yview_moveto(0.0)
        pump(root, 0.25)

        # 缩放：窗口拖宽后卡片必须跟着变宽，并且高度要按新宽度重量
        before_w = [c.winfo_width() for c in gui.viewport.winfo_children()]
        before_h = gui.viewport.winfo_reqheight()
        root.geometry("900x600")
        pump(root, 0.6)
        after_w = [c.winfo_width() for c in gui.viewport.winfo_children()]
        after_h = gui.viewport.winfo_reqheight()
        check("窗口可缩放", all(root.resizable()), f"{root.resizable()}")
        check("卡片随窗口变宽", after_w and before_w
              and after_w[0] > before_w[0] + 100,
              f"{before_w[0]} -> {after_w[0]}px")
        # 真正的断言是「每张卡片都按新宽度重量过」，不是高度一定变 ——
        # 界面里没有会随宽度换行的长文本，高度不变是正常结果
        cards = [c for c in gui.viewport.winfo_children()]
        unsynced = [c.winfo_width() for c in cards
                    if getattr(c, "_fit_w", -1) != c.winfo_width()]
        check("所有卡片已按新宽度重量", not unsynced,
              f"未同步宽度: {unsynced}" if unsynced else "%d 张" % len(cards))
        new_region = gui.scroll.cget("scrollregion")
        check("scrollregion 宽度跟着更新", new_region != region,
              f"{region} -> {new_region}")
        region = new_region
        root.geometry("620x760")
        pump(root, 0.5)

        print("\n8) 换肤")
        gui.var_theme.set("dark")
        gui._on_theme_change()
        pump(root, 0.35)
        check("深色主题重绘无异常", not cb_errors, f"{cb_errors[:2]}")
        empties = [type(o).__name__ for o in canvases if not o.find_all()]
        check("深色下控件仍非空", not empties,
              f"空白: {empties}" if empties else "%d 个" % len(canvases))
        gui.var_theme.set("light")
        gui._on_theme_change()
        pump(root, 0.35)
        check("切回浅色无异常", not cb_errors, f"{cb_errors[:2]}")

        check("全程无回调异常", not cb_errors, f"{cb_errors[:3]}")

    finally:
        if gui:
            gui.on_close()
        else:
            root.destroy()
        time.sleep(0.3)
        pz.apply_size(cfg["original_size"])
        try:
            os.remove(tmp_ini)
        except OSError:
            pass
        print(f"\n[清理] 指针已还原为 {cfg['original_size']}px")

    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for name in FAIL:
        print(f"  FAILED: {name}")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
