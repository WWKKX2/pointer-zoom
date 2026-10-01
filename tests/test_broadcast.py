# -*- coding: utf-8 -*-
"""闪烁修复的回归测试 —— 盯住「什么时候广播 WM_SETTINGCHANGE」。

背景
----
「缩放动画时会闪」的根因是 apply_size() 写死了 SPIF_SENDCHANGE，
于是 60fps 的动画每秒向全系统广播 60 次 WM_SETTINGCHANGE。响应广播的
程序会重新读一遍系统光标设置，而我们的动画只改内存、不写注册表，
注册表里还是旧尺寸 —— 它们就把光标按旧值重设回去，下一帧再改回来，
肉眼看到的就是一闪一闪。

修复后：动画帧一律 flags=0，只在动画落定那一帧广播一次。

这些测试完全不碰真实光标：把 pz.apply_size 换成记录器，
只看调用序列，所以可以随便跑、不会把桌面指针弄乱。

    python test_broadcast.py
"""

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pointer_zoom as pz  # noqa: E402

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK ] {name}{('   ' + str(extra)) if extra else ''}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}{('   ' + str(extra)) if extra else ''}")


class Recorder:
    """顶掉 pz.apply_size，只记录 (px, notify)。"""

    def __init__(self):
        self.calls = []

    def __call__(self, px, notify=False):
        self.calls.append((int(px), bool(notify)))
        return True

    @property
    def pixels(self):
        return [c[0] for c in self.calls]

    @property
    def notified(self):
        return [c for c in self.calls if c[1]]


def run_anim(broadcast, from_px, to_px, ms, fps=60, settle_wait=0.45):
    """跑一次真实 Animator 动画，返回记录器。"""
    rec = Recorder()
    pz.apply_size = rec
    anim = pz.Animator(fps)
    anim.broadcast = broadcast
    anim.current = float(from_px)
    anim.start()
    try:
        anim.goto(to_px, ms, "linear")
        time.sleep(ms / 1000.0 + settle_wait)
    finally:
        anim.stop()
    return rec


# ---------------------------------------------------------------- 用例

def test_apply_size_flags():
    print("\n[1] apply_size 的 fWinIni 标志位")
    fake = type("FakeU32", (), {"calls": None})()
    fake.calls = []

    def spi(action, uip, pv, flags):
        fake.calls.append((action, uip, pv.value, flags))
        return 1

    fake.SystemParametersInfoW = spi
    real_u32 = pz.user32
    pz.user32 = fake
    try:
        pz.apply_size(64, notify=True)
        pz.apply_size(64, notify=False)
        pz.apply_size(64)                    # 默认不广播
        pz.apply_size(999, notify=False)     # 上界钳制
        pz.apply_size(-5, notify=False)      # 下界钳制
    finally:
        pz.user32 = real_u32

    check("notify=True  -> SPIF_SENDCHANGE(0x02)",
          fake.calls[0] == (0x2029, 0, 64, pz.SPIF_SENDCHANGE), fake.calls[0])
    check("notify=False -> flags=0（不广播）",
          fake.calls[1] == (0x2029, 0, 64, 0), fake.calls[1])
    check("省略 notify 时默认不广播",
          fake.calls[2][3] == 0, fake.calls[2])
    check("超过 256 被钳到 256", fake.calls[3][2] == 256, fake.calls[3][2])
    check("小于 1 被钳到 1", fake.calls[4][2] == 1, fake.calls[4][2])
    check("动作号始终是 0x2029",
          all(c[0] == 0x2029 for c in fake.calls))


def test_snap_actually_applies():
    print("\n[2] snap / 0ms 动画必须真的下发到系统")
    rec = Recorder()
    pz.apply_size = rec
    anim = pz.Animator(60)
    anim.current = 48.0
    anim.snap(96)
    check("snap(96) 立刻下发了一次", rec.calls == [(96, True)], rec.calls)

    anim.snap(96)
    check("snap 到同一个值也会再下发（幂等但不吞）",
          len(rec.calls) == 2, rec.calls)

    anim.snap(32)
    check("snap(32) 下发 32", rec.calls[-1] == (32, True), rec.calls[-1])
    anim.stop()


def test_animation_does_not_broadcast_per_frame():
    print("\n[3] 动画期间不逐帧广播（默认 settle 模式）")
    rec = run_anim(pz.BROADCAST_SETTLE, 96, 32, 300)

    frames = len(rec.calls)
    notified = rec.notified
    check("动画确实跑了多帧", frames >= 8, f"{frames} 次下发")
    check("广播次数恰好 1 次（不是每帧）", len(notified) == 1,
          f"广播 {len(notified)} 次 / 共 {frames} 次下发")
    check("唯一那次广播发生在落定帧",
          rec.calls[-1][1] is True and rec.calls[-1][0] == 32, rec.calls[-1])
    check("落定值精确等于目标 32px", rec.calls[-1][0] == 32, rec.calls[-1][0])


def test_dedup_on_integer_pixels():
    print("\n[4] 去重按整数像素（相邻两次不下发同一个值）")
    rec = run_anim(pz.BROADCAST_SETTLE, 96, 32, 400)
    px = rec.pixels
    dup = [i for i in range(1, len(px)) if px[i] == px[i - 1]]
    # 唯一允许的重复是落定帧：值虽然没变，但要补一次广播。
    check("相邻重复最多只有落定帧那一处",
          all(i == len(px) - 1 for i in dup), f"重复位置 {dup[:5]}")

    # 慢动画：从 96 到 94 只差 2px，整数去重应该把下发压到很少
    rec2 = run_anim(pz.BROADCAST_SETTLE, 96, 94, 400)
    check("小幅动画的下发次数被整数去重压下来",
          len(rec2.calls) <= 5, f"{len(rec2.calls)} 次下发 {rec2.pixels}")


def test_every_frame_mode_still_works():
    print("\n[5] every_frame 退路模式仍然逐帧广播")
    rec = run_anim(pz.BROADCAST_EVERY, 96, 32, 300)
    check("每帧都带广播", len(rec.notified) >= 5,
          f"{len(rec.notified)} 次广播 / 共 {len(rec.calls)} 次下发")
    check("落定值仍然是 32px", rec.calls[-1][0] == 32, rec.calls[-1][0])


def test_broadcast_parsing():
    print("\n[6] broadcast 配置解析与容错")
    check("settle -> settle",
          pz._norm_broadcast("settle") == pz.BROADCAST_SETTLE)
    check("EVERY_FRAME -> every_frame",
          pz._norm_broadcast("EVERY_FRAME") == pz.BROADCAST_EVERY)
    check("always -> every_frame（别名）",
          pz._norm_broadcast("always") == pz.BROADCAST_EVERY)
    check("once -> settle（别名）",
          pz._norm_broadcast("once") == pz.BROADCAST_SETTLE)
    check("空值 -> 安全的默认 settle",
          pz._norm_broadcast("") == pz.BROADCAST_SETTLE)
    check("乱写 -> 安全的默认 settle",
          pz._norm_broadcast("banana") == pz.BROADCAST_SETTLE)
    check("None -> 安全的默认 settle",
          pz._norm_broadcast(None) == pz.BROADCAST_SETTLE)


def test_ini_roundtrip():
    print("\n[7] INI 模板与读写往返")
    check("DEFAULT_INI 里写了 broadcast",
          "broadcast = " + pz.BROADCAST_SETTLE in pz.DEFAULT_INI)
    check("DEFAULT_INI 里两个模式都解释过",
          "settle" in pz.DEFAULT_INI and "every_frame" in pz.DEFAULT_INI)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pointer-zoom.ini")

        cfg = pz.load_config(path)          # 文件不存在 -> 落默认模板
        check("首次加载默认值是 settle",
              cfg["broadcast"] == pz.BROADCAST_SETTLE, cfg["broadcast"])

        cfg["broadcast"] = pz.BROADCAST_EVERY
        pz.save_config(path, cfg)
        again = pz.load_config(path)
        check("改成 every_frame 后能读回来",
              again["broadcast"] == pz.BROADCAST_EVERY, again["broadcast"])

        cfg["broadcast"] = pz.BROADCAST_SETTLE
        pz.save_config(path, cfg)
        check("改回 settle 后能读回来",
              pz.load_config(path)["broadcast"] == pz.BROADCAST_SETTLE)

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[animation]\nbroadcast = 乱写的\n")
        check("ini 里乱写时回落到 settle",
              pz.load_config(path)["broadcast"] == pz.BROADCAST_SETTLE)


def test_config_missing_key_is_safe():
    print("\n[8] 旧配置（没有 broadcast 键）也要能用")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pointer-zoom.ini")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[pointer]\nidle_size = 80\nclick_size = 24\n"
                     "[animation]\nfps = 60\n")
        cfg = pz.load_config(path)
        check("缺失时补上 settle", cfg["broadcast"] == pz.BROADCAST_SETTLE)
        check("原有键没被弄丢", cfg["idle_size"] == 80 and cfg["fps"] == 60)
        pz.save_config(path, cfg)           # 不能因为缺键而抛异常
        check("save_config 能处理缺失键",
              "broadcast = settle" in open(path, encoding="utf-8").read())


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("=" * 64)
    print("闪烁修复回归测试（不触碰真实光标）")
    print("=" * 64)

    real_apply = pz.apply_size
    try:
        test_apply_size_flags()
        test_snap_actually_applies()
        test_animation_does_not_broadcast_per_frame()
        test_dedup_on_integer_pixels()
        test_every_frame_mode_still_works()
        test_broadcast_parsing()
        test_ini_roundtrip()
        test_config_missing_key_is_safe()
    finally:
        pz.apply_size = real_apply

    print("\n" + "=" * 64)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
