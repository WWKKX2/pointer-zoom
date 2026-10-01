# -*- coding: utf-8 -*-
"""尺寸轨迹实验 —— 动画期间「真实渲染出来的光标」到底多大。

前面的实验已经排除了「光标消失」和「句柄重建」，那两个维度都是 0。
剩下的可能只有一个：**渲染尺寸本身在跳**（比如某一帧突然弹回默认大小）。

做法
----
子进程里把当前系统光标用 DrawIconEx 画到一张纯色离屏位图上，量出非背景
像素的包围盒 —— 这就是肉眼看到的尺寸。主进程照常跑 60fps 的 SPI 动画。
结束后把尺寸序列按时间打印出来，看它是否单调。

    python probe_size.py
    python probe_size.py --sizer 0.65      # 只跑采样器
"""

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pointer_zoom as pz                       # noqa: E402
import win_capture as wc                        # noqa: E402

SPIF_SENDCHANGE = 0x02
SM_CXCURSOR, SM_CYCURSOR = 13, 14
DI_NORMAL = 0x0003
BOX = 320


def apply(px, flags):
    return bool(pz.user32.SystemParametersInfoW(
        0x2029, 0, ctypes.c_void_p(int(px)), flags))


# ------------------------------------------------------------ 渲染尺寸测量

gdi32 = wc.gdi32


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


pz.user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
pz.user32.GetIconInfo.restype = wintypes.BOOL
gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.GetObjectW.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.DeleteObject.restype = wintypes.BOOL

_get_icon_info = pz.user32.GetIconInfo
_get_object = gdi32.GetObjectW
_delete = gdi32.DeleteObject
_ICONINFO_SIZE = ctypes.sizeof(ICONINFO)
_BITMAP_SIZE = ctypes.sizeof(BITMAP)


def cursor_bitmap_size(ci):
    """当前光标的**位图**尺寸 + 热点 —— 这就是肉眼看到的尺寸。

    比 DrawIconEx 抓图快三个数量级：GetIconInfo 拿到位图句柄，
    GetObject 直接读出 bmWidth/bmHeight，O(1)。
    """
    if not (ci.flags & wc.CURSOR_SHOWING) or not ci.hCursor:
        return 0, 0, 0, 0
    ii = ICONINFO()
    if not _get_icon_info(ci.hCursor, ctypes.byref(ii)):
        return 0, 0, 0, 0
    try:
        hbm = ii.hbmColor or ii.hbmMask
        if not hbm:
            return 0, 0, 0, 0
        bm = BITMAP()
        if _get_object(hbm, _BITMAP_SIZE, ctypes.byref(bm)) == 0:
            return 0, 0, 0, 0
        return bm.bmWidth, bm.bmHeight, ii.xHotspot, ii.yHotspot
    finally:
        if ii.hbmColor:
            _delete(ii.hbmColor)
        if ii.hbmMask:
            _delete(ii.hbmMask)


def sizer_main(seconds):
    """独立进程：高频记录 (t, 位图宽, 位图高, 热点x, 热点y)。"""
    get = pz.user32.GetCursorInfo
    ci = wc.get_cursor_info()
    cbsize = ctypes.sizeof(ci)

    rows = []
    t0 = time.perf_counter()
    t_end = t0 + seconds
    while time.perf_counter() < t_end:
        ci.cbSize = cbsize          # 坑：GetCursorInfo 会把 cbSize 清 0
        if get(ctypes.byref(ci)):
            rows.append((time.perf_counter() - t0,) + cursor_bitmap_size(ci))
        else:
            rows.append((time.perf_counter() - t0, 0, 0, 0, 0))

    seq = []
    for r in rows:
        key = r[1:]
        if not seq or seq[-1][1] != key:
            seq.append([round(r[0], 5), key])
    print(json.dumps({"n": len(rows), "seq": seq,
                      "hz": len(rows) / max(1e-6, seconds)}))


# ------------------------------------------------------------ 驱动端

def drive(from_px, to_px, dur_ms, fps, flags, dedup):
    steps = max(1, int(dur_ms / 1000 * fps))
    applied = None
    t0 = time.perf_counter()
    for i in range(steps + 1):
        px = int(round(from_px + (to_px - from_px) * i / steps))
        if not dedup or applied != px:
            apply(px, flags)
            applied = px
        gap = t0 + (i + 1) / fps - time.perf_counter()
        if gap > 0:
            time.sleep(gap)


def trace(label, flags, dedup, from_px=64, to_px=24, dur_ms=400, fps=60):
    print("=" * 72)
    print(f"{label}    {from_px}px -> {to_px}px / {dur_ms}ms / {fps}fps")
    print("=" * 72)
    apply(from_px, 0)
    time.sleep(0.35)

    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--sizer", "0.9"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")
    time.sleep(0.12)
    t_drive = time.perf_counter()
    drive(from_px, to_px, dur_ms, fps, flags, dedup)
    t_done = time.perf_counter()
    out, errout = child.communicate(timeout=10)
    if errout.strip():
        print("  子进程 stderr:", errout.strip()[:400])
    try:
        data = json.loads(out.strip().splitlines()[-1])
    except Exception as exc:
        print("  解析失败:", exc, out[:300])
        return

    seq = data["seq"]
    off = 0.12                      # 子进程 t0 比 t_drive 早约 0.12s
    print(f"  采样 {data['n']} 次 @ {data['hz']/1000:.0f}kHz，"
          f"位图变化点 {len(seq)} 个\n")
    print(f"  {'t(ms)':>8}{'位图宽':>8}{'位图高':>8}{'热点':>12}   说明")
    print("  " + "-" * 62)
    prev = None
    widths = []
    for t, (w, h, hx, hy) in seq:
        rel = (t - off) * 1000
        note = ""
        if prev is not None and prev[0] != w:
            note = f"{prev[0]} -> {w}"
        if prev is None or prev[0] != w:
            widths.append(w)
        prev = (w, h, hx, hy)
        print(f"  {rel:>8.1f}{w:>8}{h:>8}{f'{hx},{hy}':>12}   {note}")
    print(f"\n  宽度序列: {widths}")
    if len(widths) > 2:
        ups = sum(1 for i in range(1, len(widths))
                  if (widths[i] - widths[i - 1]) * (widths[-1] - widths[0]) > 0)
        downs = len(widths) - 1 - ups
        print(f"  单调性: {ups} 段同向 / {downs} 段反向"
              f"{'   <== 有反向跳动' if downs else '   （单调）'}")
    print(f"\n  （动画区间：0 ~ {dur_ms}ms）\n")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    original = pz.read_system_cursor_size()
    print(f"\n当前系统指针尺寸 = {original}px\n")
    try:
        print("先确认 SPI 改尺寸后，光标位图是否真的跟着变：")
        for px in (32, 48, 64, 96, 128, 200):
            apply(px, SPIF_SENDCHANGE)
            time.sleep(0.3)
            ci = wc.get_cursor_info()
            w, h, hx, hy = cursor_bitmap_size(ci)
            print(f"  设定 {px:>3}px -> 位图 {w:>3}x{h:<3} 热点 {hx},{hy}")
        print()

        trace("A 当前实现：SPIF_SENDCHANGE 每帧", SPIF_SENDCHANGE, False)
        trace("B 去掉广播：flags=0 每帧", 0, False)
    finally:
        pz.apply_size(original)
        print(f"[恢复] 指针已还原为 {original}px")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--sizer":
        sizer_main(float(sys.argv[2]))
    else:
        main()
