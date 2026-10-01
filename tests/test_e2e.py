# -*- coding: utf-8 -*-
"""端到端测试：合成点击 -> 钩子 -> 状态机 -> 动画线程，全链路走一遍。

安全性说明
----------
测试钩子在最外层 **把事件吞掉（返回 1）**，所以合成的点击不会落到屏幕上
任何一个窗口里，不会误触任何东西。同时内层仍然调用真实的 `hook_proc`，
所以走的是和线上完全一样的代码路径。

    python test_e2e.py
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pointer_zoom as pz  # noqa: E402

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

pz.user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, ctypes.POINTER(wintypes.ULONG)]

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


def pump(seconds=0.0):
    """抽干消息队列，让钩子有机会被调用。"""
    msg = pz.MSG()
    deadline = time.perf_counter() + seconds
    while True:
        while pz.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            pz.user32.TranslateMessage(ctypes.byref(msg))
            pz.user32.DispatchMessageW(ctypes.byref(msg))
        if time.perf_counter() >= deadline:
            return
        time.sleep(0.002)


def click(flag):
    pz.user32.mouse_event(flag, 0, 0, 0, None)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = pz.load_config(os.path.join(ROOT, "pointer-zoom.ini"))

    print("=" * 62)
    print("端到端测试：合成点击 -> 钩子 -> 动画")
    print("=" * 62)
    print(f"  原始 {cfg['original_size']}px   平时 {cfg['idle_size']}px   "
          f"按下 {cfg['click_size']}px")

    app = pz.PointerZoom(cfg)
    callback_times = []
    seen = []

    def proc(code, wparam, lparam):
        t0 = time.perf_counter()
        app.hook_proc(code, wparam, lparam)      # 真实代码路径
        callback_times.append((time.perf_counter() - t0) * 1e6)
        if wparam in (pz.WM_LBUTTONDOWN, pz.WM_LBUTTONUP):
            seen.append(wparam)
        return 1                                  # 吞掉事件，绝不落到任何窗口

    proc_ref = pz.HOOKPROC(proc)
    hook = None
    try:
        app.start()
        pump(0.05)

        hook = pz.user32.SetWindowsHookExW(pz.WH_MOUSE_LL, proc_ref, None, 0)
        if not hook:
            print(f"  [FAIL] 钩子安装失败 err={ctypes.get_last_error()}")
            return 1

        print("\n1) 合成左键按下")
        click(MOUSEEVENTF_LEFTDOWN)
        pump(0.30)
        check("钩子收到 WM_LBUTTONDOWN", pz.WM_LBUTTONDOWN in seen, f"seen={seen}")
        check("状态机进入 pressed", app.pressed is True)
        check("动画目标 = click_size", app.anim.target == float(cfg["click_size"]),
              f"target={app.anim.target}")
        check("动画曲线 = press_curve",
              app.anim._curve is pz.CURVES[cfg["press_curve"]])
        check("尺寸已真的降下来", app.anim.current < cfg["idle_size"] - 5,
              f"current={app.anim.current:.1f}px")

        print("\n2) 合成左键松开")
        click(MOUSEEVENTF_LEFTUP)
        pump(0.45)
        check("钩子收到 WM_LBUTTONUP", pz.WM_LBUTTONUP in seen)
        check("状态机退出 pressed", app.pressed is False)
        check("动画目标 = idle_size", app.anim.target == float(cfg["idle_size"]),
              f"target={app.anim.target}")
        check("动画曲线 = release_curve",
              app.anim._curve is pz.CURVES[cfg["release_curve"]])
        check("尺寸已回到平时大小",
              abs(app.anim.current - cfg["idle_size"]) < 2,
              f"current={app.anim.current:.1f}px")

        print("\n3) 交互性：回调耗时（不能阻塞鼠标输入）")
        if callback_times:
            worst = max(callback_times)
            avg = sum(callback_times) / len(callback_times)
            check(f"回调平均 {avg:.0f}us / 最差 {worst:.0f}us，远低于 300ms 超时",
                  worst < 5000, f"{len(callback_times)} 次调用")
        else:
            check("回调被调用", False)

        print("\n4) 快速连点不跳变")
        before = app.anim.current
        click(MOUSEEVENTF_LEFTDOWN)
        pump(0.05)
        click(MOUSEEVENTF_LEFTUP)
        pump(0.05)
        click(MOUSEEVENTF_LEFTDOWN)
        pump(0.05)
        mid = app.anim.current
        check("连点过程中尺寸连续（起点采样自当前值）",
              app.anim._from == app.anim.current or abs(app.anim._from - before) < 60,
              f"from={app.anim._from:.1f} current={mid:.1f}")
        click(MOUSEEVENTF_LEFTUP)
        pump(0.5)
        check("连点后仍能回到平时大小",
              abs(app.anim.current - cfg["idle_size"]) < 2,
              f"current={app.anim.current:.1f}px")

        print("\n5) 停用后不再响应")
        app.enabled = False
        app.anim.snap(cfg["original_size"])
        pump(0.05)
        click(MOUSEEVENTF_LEFTDOWN)
        pump(0.1)
        check("停用状态下按下不触发", app.pressed is False)
        click(MOUSEEVENTF_LEFTUP)
        pump(0.05)
    finally:
        if hook:
            pz.user32.UnhookWindowsHookEx(hook)
        app.restore()
        time.sleep(0.1)
        pz.apply_size(cfg["original_size"])
        print(f"\n[清理] 指针已还原为 {cfg['original_size']}px")

    print("\n" + "=" * 62)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    for name in FAIL:
        print(f"  FAILED: {name}")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
