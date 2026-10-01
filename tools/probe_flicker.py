# -*- coding: utf-8 -*-
"""闪烁根因实验 —— 高频采样光标的「可见性」与「句柄切换」。

为什么用子进程采样
------------------
第一版把采样器放在同进程的线程里，结果一个样本都收不到：采样线程在
ctypes 密集循环里拿不到 GIL，被驱动动画的主线程活活饿死。换到独立进程
后两边各跑各的解释器，零争用。

「闪」在系统层面只有一个可观测的定义
------------------------------------
GetCursorInfo().flags 里：

    CURSOR_SHOWING    = 0x0001   光标正在显示
    CURSOR_SUPPRESSED = 0x0002   光标被抑制（触摸输入等）

两个都没有 => 这一瞬间屏幕上没有光标 => 肉眼看到的一次「闪」。
另外句柄（hCursor）被重建时，旧位图释放到新位图绘制的间隙同样是一帧空白。

对照组
------
  A 基线（完全不动 SPI）        —— 自然抖动有多大
  B SPIF_SENDCHANGE 每帧        —— 当前实现
  C flags=0 每帧                —— 只去掉广播
  D flags=0 + 整数去重          —— 再去掉重复值调用
  E SPIF_SENDCHANGE + 去重

    python probe_flicker.py
"""

import ctypes
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pointer_zoom as pz                       # noqa: E402
from win_capture import get_cursor_info         # noqa: E402

SPIF_SENDCHANGE = 0x02
CURSOR_SHOWING = 0x0001
CURSOR_SUPPRESSED = 0x0002

FROM_PX, TO_PX = 64, 24
DUR_MS = 400
FPS = 60
REPEAT = 3
SAMPLE_SEC = DUR_MS / 1000 + 0.25


def apply(px, flags):
    return bool(pz.user32.SystemParametersInfoW(
        0x2029, 0, ctypes.c_void_p(int(px)), flags))


# ------------------------------------------------------------ 子进程采样器

def sampler_main(seconds):
    """独立进程里高频轮询 GetCursorInfo，结束时把统计打到 stdout。"""
    get = pz.user32.GetCursorInfo
    ci = get_cursor_info()
    cbsize = ctypes.sizeof(ci)

    n = invis = 0
    flags_seen = {}
    handles = {}
    switches = 0
    prev_h = None
    t_end = time.perf_counter() + seconds

    while time.perf_counter() < t_end:
        # 坑：GetCursorInfo 会把 cbSize 清成 0，不每次重设的话，
        # 第二次起就 ERROR_INVALID_PARAMETER(87) 静默失败 —— 实测
        # 30 万次循环里只有第 1 次是真的成功。
        ci.cbSize = cbsize
        if get(ctypes.byref(ci)):
            fl, h = ci.flags, ci.hCursor
            n += 1
            if not (fl & (CURSOR_SHOWING | CURSOR_SUPPRESSED)):
                invis += 1
            flags_seen[fl] = flags_seen.get(fl, 0) + 1
            if h:
                handles[h] = handles.get(h, 0) + 1
                if prev_h is not None and h != prev_h:
                    switches += 1
                prev_h = h

    print(json.dumps({
        "n": n,
        "invis": invis,
        "flags": flags_seen,
        "handles": len(handles),
        "switches": switches,
        "hz": n / max(1e-6, seconds),
    }))


# ------------------------------------------------------------ 驱动端

def drive(flags, dedup):
    steps = max(1, int(DUR_MS / 1000 * FPS))
    applied = None
    t0 = time.perf_counter()
    for i in range(steps + 1):
        px = int(round(FROM_PX + (TO_PX - FROM_PX) * i / steps))
        if not dedup or applied != px:
            apply(px, flags)
            applied = px
        gap = t0 + (i + 1) / FPS - time.perf_counter()
        if gap > 0:
            time.sleep(gap)


def run_case(flags, dedup, warm=0.35):
    agg = {"n": 0.0, "invis": 0.0, "handles": 0.0, "switches": 0.0,
           "hz": 0.0, "flags": {}, "ok": 0}
    for _ in range(REPEAT):
        apply(FROM_PX, 0)
        time.sleep(warm)

        child = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__),
             "--sampler", str(SAMPLE_SEC)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8")
        time.sleep(0.12)                      # 等子进程把采样循环跑起来
        if flags is None:
            time.sleep(DUR_MS / 1000)
        else:
            drive(flags, dedup)
        out, _ = child.communicate(timeout=10)

        try:
            st = json.loads(out.strip().splitlines()[-1])
        except Exception:
            continue
        agg["ok"] += 1
        for k in ("n", "invis", "handles", "switches", "hz"):
            agg[k] += st[k]
        for fl, c in st["flags"].items():
            agg["flags"][fl] = agg["flags"].get(fl, 0) + c
    r = max(1, agg["ok"])
    for k in ("n", "invis", "handles", "switches", "hz"):
        agg[k] /= r
    return agg


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    original = pz.read_system_cursor_size()
    print(f"\n当前系统指针尺寸 = {original}px")
    print(f"模拟动画：{FROM_PX}px -> {TO_PX}px / {DUR_MS}ms / {FPS}fps，"
          f"每组重复 {REPEAT} 次，采样在独立子进程中进行\n")

    cases = [
        ("A 基线（完全不动 SPI）", None, False),
        ("B SENDCHANGE 每帧", SPIF_SENDCHANGE, False),
        ("C flags=0 每帧", 0, False),
        ("D flags=0 + 去重", 0, True),
        ("E SENDCHANGE + 去重", SPIF_SENDCHANGE, True),
    ]

    print(f"  {'策略':<24}{'不可见':>9}{'句柄数':>8}{'句柄切换':>9}"
          f"{'采样':>10}{'采样率':>9}")
    print("  " + "-" * 68)
    results = {}
    try:
        for label, flags, dedup in cases:
            st = run_case(flags, dedup)
            results[label] = st
            print(f"  {label:<24}{st['invis']/max(1,st['n'])*100:>8.3f}%"
                  f"{st['handles']:>8.1f}{st['switches']:>9.1f}"
                  f"{st['n']:>10.0f}{st['hz']/1000:>8.0f}k")
            if st["flags"]:
                fs = ", ".join(f"0x{int(k):04x} x{int(v)}" for k, v in
                               sorted(st["flags"].items(),
                                      key=lambda kv: -kv[1])[:3])
                print(f"      flags 分布: {fs}")
    finally:
        pz.apply_size(original)
        print(f"\n[恢复] 指针已还原为 {original}px")

    print("\n" + "=" * 68)
    print("判读")
    print("=" * 68)
    base = results.get("A 基线（完全不动 SPI）", {"n": 1, "invis": 0.0,
                                                  "switches": 0.0})
    b_invis = base["invis"] / max(1, base["n"])
    b_sw = base["switches"]
    print(f"  基线：不可见 {b_invis*100:.3f}%  句柄切换 {b_sw:.1f} 次\n")
    for label, st in results.items():
        if label.startswith("A"):
            continue
        r = st["invis"] / max(1, st["n"])
        d = r - b_invis
        if d > 0.005:
            verdict = "  <== 光标会消失，这就是「闪」"
        elif st["switches"] > b_sw + 3:
            verdict = "  <== 句柄反复重建，可能闪"
        else:
            verdict = "  OK"
        print(f"  {label:<24} 不可见 {r*100:6.3f}% (基线+{d*100:5.3f})"
              f"  切换 {st['switches']:5.1f}{verdict}")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--sampler":
        sampler_main(float(sys.argv[2]))
    else:
        main()
