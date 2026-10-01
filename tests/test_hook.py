# -*- coding: utf-8 -*-
"""钩子诊断 —— 装一次 WH_MOUSE_LL，统计几秒内收到的鼠标事件，然后干净卸载。

不合成任何输入、不改动指针尺寸，纯粹用来验证：
  1. SetWindowsHookEx 能否装上
  2. 回调签名是否正确（lParam 是 64 位指针，argtypes 写错必崩）
  3. CallNextHookEx 能否正常返回（返回失败说明钩子会被系统摘掉）
  4. 按键事件能否被正确区分

    python test_hook.py
    python test_hook.py --seconds 8
"""

import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pointer_zoom as pz  # noqa: E402

NAMES = {
    pz.WM_LBUTTONDOWN: "左键按下", pz.WM_LBUTTONUP: "左键松开",
    pz.WM_RBUTTONDOWN: "右键按下", pz.WM_RBUTTONUP: "右键松开",
    pz.WM_MBUTTONDOWN: "中键按下", pz.WM_MBUTTONUP: "中键松开",
    0x0200: "鼠标移动", 0x020A: "滚轮",
}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="鼠标钩子诊断")
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args()

    counts = {}
    errors = []

    def proc(code, wparam, lparam):
        try:
            if code >= 0:
                counts[wparam] = counts.get(wparam, 0) + 1
        except Exception as exc:            # noqa: BLE001
            errors.append(repr(exc))
        try:
            return pz.user32.CallNextHookEx(None, code, wparam, lparam)
        except Exception as exc:            # noqa: BLE001
            errors.append(f"CallNextHookEx: {exc}")
            return 0

    proc_ref = pz.HOOKPROC(proc)            # 必须持有引用，否则被 GC

    print("=" * 60)
    print("鼠标钩子诊断")
    print("=" * 60)

    hook = pz.user32.SetWindowsHookExW(pz.WH_MOUSE_LL, proc_ref, None, 0)
    if not hook:
        err = ctypes.get_last_error()
        print(f"[失败] SetWindowsHookEx 返回 0，GetLastError={err}")
        return 1
    print(f"[OK  ] 钩子安装成功 hHook=0x{hook:016X}")

    print(f"[....] 采样 {args.seconds:.0f} 秒，请随意晃动鼠标、点击几下 ……")
    deadline = time.perf_counter() + args.seconds
    msg = pz.MSG()
    while time.perf_counter() < deadline:
        # 钩子需要消息循环；PeekMessage 轮询避免阻塞
        while pz.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            pz.user32.TranslateMessage(ctypes.byref(msg))
            pz.user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.005)

    pz.user32.UnhookWindowsHookEx(hook)
    print("[OK  ] 钩子已卸载\n")

    total = sum(counts.values())
    print(f"共收到 {total} 个事件：")
    for msg_id, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  0x{msg_id:04X}  {NAMES.get(msg_id, '其他'):<8}  {n:>6}")

    print()
    if errors:
        print(f"[失败] 回调抛出 {len(errors)} 次异常，前 3 条：")
        for e in errors[:3]:
            print(f"   {e}")
        return 1
    if total == 0:
        print("[警告] 一个事件都没收到 —— 钩子装上了但没生效")
        return 1

    buttons = [m for m in counts if m in (pz.WM_LBUTTONDOWN, pz.WM_LBUTTONUP,
                                          pz.WM_RBUTTONDOWN, pz.WM_RBUTTONUP,
                                          pz.WM_MBUTTONDOWN, pz.WM_MBUTTONUP)]
    print(f"[OK  ] 回调零异常，CallNextHookEx 正常返回")
    if buttons:
        print(f"[OK  ] 捕获到 {len(buttons)} 种按键事件，点击触发链路可用")
    else:
        print("[提示] 采样期间没有点击，只收到移动/滚轮事件 —— 属正常，"
              "按键分支已由 test_logic.py 覆盖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
