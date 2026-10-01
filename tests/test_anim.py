# -*- coding: utf-8 -*-
"""动画引擎离线冒烟测试 —— 不用钩子，直接驱动 Animator 跑一轮按下 / 松开。

跑起来屏幕上指针会真的动一次，可以直接用眼睛看效果；
同时打印尺寸时间线，方便对比不同缓动曲线的数值形态。

    python test_anim.py
    python test_anim.py --press-curve ease_out_elastic --release-curve ease_out_bounce
"""

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pointer_zoom as pz  # noqa: E402


def sample(anim, duration_ms, fps=60):
    """按帧采样当前尺寸，返回 [(ms, size), ...]"""
    out = []
    t0 = time.perf_counter()
    deadline = duration_ms / 1000.0 + 0.06
    while time.perf_counter() - t0 < deadline:
        out.append(((time.perf_counter() - t0) * 1000, anim.current))
        time.sleep(1.0 / fps)
    return out


def bar(size, lo, hi, width=34):
    span = max(1e-6, hi - lo)
    filled = int(round((size - lo) / span * width))
    return "█" * max(0, min(width, filled))


def render(title, samples, lo, hi):
    print(f"\n  {title}")
    step = max(1, len(samples) // 12)
    for ms, size in samples[::step]:
        print(f"    {ms:6.0f}ms  {size:6.1f}px  {bar(size, lo, hi)}")
    if samples:
        ms, size = samples[-1]
        print(f"    {ms:6.0f}ms  {size:6.1f}px  {bar(size, lo, hi)}  <- 结束")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="动画引擎冒烟测试")
    parser.add_argument("--config", default=os.path.join(ROOT, "pointer-zoom.ini"))
    parser.add_argument("--press-curve", default=None)
    parser.add_argument("--release-curve", default=None)
    parser.add_argument("--idle-size", type=int, default=None)
    parser.add_argument("--click-size", type=int, default=None)
    args = parser.parse_args()

    cfg = pz.load_config(args.config)
    for key, val in (("press_curve", args.press_curve),
                     ("release_curve", args.release_curve),
                     ("idle_size", args.idle_size),
                     ("click_size", args.click_size)):
        if val is not None:
            cfg[key] = val

    print("=" * 64)
    print("动画引擎冒烟测试")
    print("=" * 64)
    print(f"  原始大小 {cfg['original_size']}px   平时 {cfg['idle_size']}px   "
          f"按下 {cfg['click_size']}px")
    print(f"  按下 {cfg['press_ms']}ms / {cfg['press_curve']}")
    print(f"  松开 {cfg['release_ms']}ms / {cfg['release_curve']}")

    anim = pz.Animator(cfg["fps"])
    original = cfg["original_size"]
    try:
        pz.apply_size(original)
        time.sleep(0.1)
        anim.current = float(original)
        anim.start()

        # 1) 起步到平时大小
        anim.goto(cfg["idle_size"], cfg["release_ms"], "ease_out_cubic")
        time.sleep(cfg["release_ms"] / 1000.0 + 0.1)

        # 2) 模拟按下 -> 缩小
        anim.goto(cfg["click_size"], cfg["press_ms"],
                  cfg["press_curve"], cfg["strength"])
        press = sample(anim, cfg["press_ms"])
        time.sleep(0.15)

        # 3) 模拟松开 -> 恢复
        anim.goto(cfg["idle_size"], cfg["release_ms"],
                  cfg["release_curve"], cfg["strength"])
        release = sample(anim, cfg["release_ms"])
        time.sleep(0.15)

        lo = min(cfg["click_size"], cfg["idle_size"]) - 6
        hi = max(cfg["click_size"], cfg["idle_size"]) + 6
        render("按下（缩小）", press, lo, hi)
        render("松开（恢复）", release, lo, hi)

        press_end = press[-1][1] if press else 0
        release_end = release[-1][1] if release else 0
        ok_press = abs(press_end - cfg["click_size"]) < 2
        ok_release = abs(release_end - cfg["idle_size"]) < 2

        print("\n" + "=" * 64)
        print(f"  按下结束 {press_end:.1f}px  期望 {cfg['click_size']}px   "
              f"{'通过' if ok_press else '失败'}")
        print(f"  松开结束 {release_end:.1f}px  期望 {cfg['idle_size']}px   "
              f"{'通过' if ok_release else '失败'}")
        print("=" * 64)
        return 0 if (ok_press and ok_release) else 1
    finally:
        anim.stop()
        pz.apply_size(original)
        print(f"\n[恢复] 指针已还原为 {original}px")


if __name__ == "__main__":
    sys.exit(main())
