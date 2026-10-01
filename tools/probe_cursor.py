# -*- coding: utf-8 -*-
"""光标闪烁诊断 —— 定位「缩放动画时会闪」的根源。

三个实验：

1. **SPI 调用耗时**
   每次改尺寸要花多久。如果只有 1~2ms，说明系统只是改了个值；
   如果要几十毫秒，说明每帧都在重新光栅化光标位图，那必然会闪。

2. **光标对象重建次数**
   动画期间不断读 GetCursorInfo，数出现多少个不同的 HCURSOR。
   重建意味着中间有一瞬没有光标。

3. **实际渲染尺寸**
   用 DrawIconEx 把当前系统光标画到离屏位图上，量它的真实包围盒。
   用来确认「某种调用方式下光标到底有没有真的改变大小」。

    python probe_cursor.py
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pointer_zoom as pz                       # noqa: E402
from win_capture import (capture_bgra, capture_with_cursor,   # noqa: E402
                         get_cursor_info)

SPIF_SENDCHANGE = 0x02
REGION = 300
DRAW_AT = (24, 24)
SIZES = [32, 48, 64, 96, 128, 160]


def apply(px, flags):
    return bool(pz.user32.SystemParametersInfoW(
        0x2029, 0, ctypes.c_void_p(px), flags))


def diff_bbox(ref, cur, w, h, thr=24):
    minx, miny, maxx, maxy, count = w, h, -1, -1, 0
    for i in range(0, len(ref), 4):
        if (abs(ref[i] - cur[i]) > thr
                or abs(ref[i + 1] - cur[i + 1]) > thr
                or abs(ref[i + 2] - cur[i + 2]) > thr):
            p = i // 4
            px, py = p % w, p // w
            count += 1
            if px < minx:
                minx = px
            if px > maxx:
                maxx = px
            if py < miny:
                miny = py
            if py > maxy:
                maxy = py
    if maxx < 0:
        return 0, None
    return count, (minx, miny, maxx - minx + 1, maxy - miny + 1)


def part1_latency():
    print("=" * 68)
    print("1. SPI 调用耗时（区分「只改了个值」还是「每帧重新光栅化」）")
    print("=" * 68)
    out = {}
    for label, flags in (("SPIF_SENDCHANGE", SPIF_SENDCHANGE), ("flags=0", 0)):
        pz.apply_size(48)
        time.sleep(0.2)
        ts = []
        for i in range(40):
            px = 32 if i % 2 == 0 else 96          # 交替，保证每次都真的变
            t0 = time.perf_counter()
            apply(px, flags)
            ts.append((time.perf_counter() - t0) * 1000)
        ts.sort()
        out[label] = ts
        print(f"  {label:<18} 平均 {sum(ts)/len(ts):6.2f}ms   "
              f"中位 {ts[len(ts)//2]:6.2f}ms   最差 {ts[-1]:6.2f}ms")
    pz.apply_size(96)
    print()
    return out


def part2_handle_churn():
    print("=" * 68)
    print("2. 光标对象重建次数（重建 = 中间有一瞬没有光标）")
    print("=" * 68)
    out = {}
    for label, flags in (("SPIF_SENDCHANGE", SPIF_SENDCHANGE), ("flags=0", 0)):
        pz.apply_size(96)
        time.sleep(0.25)
        handles = []
        steps = 12
        for i in range(steps):
            apply(round(96 - (96 - 32) * i / (steps - 1)), flags)
            handles.append(get_cursor_info().hCursor)
            time.sleep(1 / 60)
        out[label] = len(set(handles))
        print(f"  {label:<18} {steps} 帧里出现 {out[label]:>2} 个不同光标句柄")
    pz.apply_size(96)
    print()
    return out


def part3_rendered_size():
    print("=" * 68)
    print("3. 实际渲染尺寸（DrawIconEx 把系统光标画到离屏位图上量包围盒）")
    print("=" * 68)

    sw = pz.user32.GetSystemMetrics(0)
    sh = pz.user32.GetSystemMetrics(1)
    x = max(0, sw // 2 - REGION // 2)
    y = max(0, sh // 2 - REGION // 2)

    ref = capture_bgra(x, y, REGION, REGION)
    print(f"  采样 {REGION}x{REGION}，光标画在 ({DRAW_AT[0]},{DRAW_AT[1]})")
    print(f"  {'设定值':<8}{'SENDCHANGE':<26}{'flags=0':<26}")
    print("  " + "-" * 62)

    table = {}
    for size in SIZES:
        cells = []
        for flags in (SPIF_SENDCHANGE, 0):
            apply(size, flags)
            time.sleep(0.25)
            cur, drawn = capture_with_cursor(x, y, REGION, REGION, DRAW_AT)
            count, box = diff_bbox(ref, cur, REGION, REGION)
            if not drawn:
                cells.append("未画出光标")
            elif box is None:
                cells.append("无变化")
            else:
                cells.append(f"{box[2]}x{box[3]} ({count}px)")
        table[size] = cells
        print(f"  {size:<8}{cells[0]:<26}{cells[1]:<26}")

    pz.apply_size(96)
    print()
    return table


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    original = pz.read_system_cursor_size()
    print(f"\n当前系统指针尺寸 = {original}px\n")
    try:
        lat = part1_latency()
        churn = part2_handle_churn()
        table = part3_rendered_size()

        print("=" * 68)
        print("结论")
        print("=" * 68)
        avg = sum(lat["SPIF_SENDCHANGE"]) / len(lat["SPIF_SENDCHANGE"])
        if avg < 5:
            print(f"· 单次调用平均 {avg:.2f}ms —— 系统只是改了个值，没有重新光栅化。")
            print("  闪烁更可能来自逐帧的 WM_SETTINGCHANGE 广播引发的重绘。")
        else:
            print(f"· 单次调用平均 {avg:.2f}ms —— 每次都在重新光栅化光标，"
                  "这就是闪烁的根源，必须降低调用频率。")

        if max(churn.values()) >= 10:
            print("· 光标对象在动画期间被反复重建。")
        else:
            print("· 光标对象没有被频繁重建。")

        for size, cells in table.items():
            print(f"· 设定 {size:>3}px  ->  SENDCHANGE: {cells[0]:<22} "
                  f"flags=0: {cells[1]}")
    finally:
        pz.apply_size(original)
        print(f"\n[恢复] 指针已还原为 {original}px")


if __name__ == "__main__":
    main()
