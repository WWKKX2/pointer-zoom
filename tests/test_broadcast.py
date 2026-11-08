# -*- coding: utf-8 -*-
"""闪烁 / 抽搐修复的回归测试 —— 盯住「什么时候广播 WM_SETTINGCHANGE」。

背景
----
「缩放动画时会闪」的根因是 apply_size() 写死了 SPIF_SENDCHANGE，
于是 60fps 的动画每秒向全系统广播 60 次 WM_SETTINGCHANGE。响应广播的
程序会重新读一遍系统光标设置，而我们的动画只改内存、不写注册表，
注册表里还是旧尺寸 —— 它们就把光标按旧值重设回去，下一帧再改回来，
肉眼看到的就是一闪一闪。（v1.0.1 修复：动画帧 flags=0。）

「偶尔抽搐」是同一个根因的最后残渣（v1.1.2 修复）：动画落定帧还带着
一次广播。注册表里永远是原始大小（本项目从不写注册表），按住时缩小
到位的光标（比如 25px）在落定广播后被响应者按注册表值拉回 48px ——
空闲时落定值恰好等于注册表值看不出来，按住时才看得见，所以「偶尔」。

修复后：默认**从不广播**（none）。旧 ini 里的 settle 自动升级为 none；
every_frame 作为唯一的显式广播途径保留（诊断退路）。

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
    print("\n[2] snap / 0ms 动画必须真的下发到系统（且不广播）")
    rec = Recorder()
    pz.apply_size = rec
    anim = pz.Animator(60)
    anim.current = 48.0
    anim.snap(96)
    check("snap(96) 立刻下发了一次", rec.calls == [(96, False)], rec.calls)

    anim.snap(96)
    check("snap 到同一个值被整数去重吞掉（系统状态本来就一致）",
          len(rec.calls) == 1, rec.calls)

    anim.snap(32)
    check("snap(32) 下发 32", rec.calls[-1] == (32, False), rec.calls[-1])
    anim.stop()


def test_animation_does_not_broadcast_per_frame():
    print("\n[3] 动画期间不逐帧广播（默认 none 模式）")
    rec = run_anim(pz.BROADCAST_NONE, 96, 32, 300)

    frames = len(rec.calls)
    notified = rec.notified
    check("动画确实跑了多帧", frames >= 8, f"{frames} 次下发")
    check("全程零广播", len(notified) == 0,
          f"广播 {len(notified)} 次 / 共 {frames} 次下发")
    check("落定值精确等于目标 32px", rec.calls[-1][0] == 32, rec.calls[-1])


def test_settle_mode_no_longer_broadcasts():
    print("\n[4] settle（历史遗留值）也不再广播 —— 抽搐修复的核心")
    # v1.1.1 及之前：落定帧 notify=True 广播一次，被响应者按注册表
    # 原始值拉回，表现为偶尔抽搐。现在 settle 与 none 等价。
    rec = run_anim(pz.BROADCAST_SETTLE, 96, 32, 300)
    check("settle 模式全程零广播", len(rec.notified) == 0,
          f"{len(rec.notified)} 次 / {len(rec.calls)} 次")
    check("settle 模式落定值仍是 32px", rec.calls[-1][0] == 32,
          rec.calls[-1])


def test_dedup_on_integer_pixels():
    print("\n[5] 去重按整数像素（相邻两次不下发同一个值）")
    rec = run_anim(pz.BROADCAST_NONE, 96, 32, 400)
    px = rec.pixels
    dup = [i for i in range(1, len(px)) if px[i] == px[i - 1]]
    check("全程无相邻重复下发", not dup, f"重复位置 {dup[:5]}")

    # 慢动画：从 96 到 94 只差 2px，整数去重应该把下发压到很少
    rec2 = run_anim(pz.BROADCAST_NONE, 96, 94, 400)
    check("小幅动画的下发次数被整数去重压下来",
          len(rec2.calls) <= 5, f"{len(rec2.calls)} 次下发 {rec2.pixels}")


def test_every_frame_mode_still_works():
    print("\n[6] every_frame 退路模式仍然逐帧广播")
    rec = run_anim(pz.BROADCAST_EVERY, 96, 32, 300)
    check("每帧都带广播", len(rec.notified) >= 5,
          f"{len(rec.notified)} 次广播 / 共 {len(rec.calls)} 次下发")
    check("落定值仍然是 32px", rec.calls[-1][0] == 32, rec.calls[-1][0])


def test_broadcast_parsing():
    print("\n[7] broadcast 配置解析与容错")
    check("none -> none", pz._norm_broadcast("none") == pz.BROADCAST_NONE)
    check("settle -> none（旧配置自动升级）",
          pz._norm_broadcast("settle") == pz.BROADCAST_NONE)
    check("once -> none（别名）",
          pz._norm_broadcast("once") == pz.BROADCAST_NONE)
    check("EVERY_FRAME -> every_frame",
          pz._norm_broadcast("EVERY_FRAME") == pz.BROADCAST_EVERY)
    check("always -> every_frame（别名）",
          pz._norm_broadcast("always") == pz.BROADCAST_EVERY)
    check("空值 -> 安全的默认 none",
          pz._norm_broadcast("") == pz.BROADCAST_NONE)
    check("乱写 -> 安全的默认 none",
          pz._norm_broadcast("banana") == pz.BROADCAST_NONE)
    check("None -> 安全的默认 none",
          pz._norm_broadcast(None) == pz.BROADCAST_NONE)


def test_ini_roundtrip():
    print("\n[8] INI 模板与读写往返")
    check("DEFAULT_INI 里写了 broadcast = none",
          "broadcast = " + pz.BROADCAST_NONE in pz.DEFAULT_INI)
    check("DEFAULT_INI 里两个模式都解释过",
          "settle" in pz.DEFAULT_INI and "every_frame" in pz.DEFAULT_INI)

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pointer-zoom.ini")

        cfg = pz.load_config(path)          # 文件不存在 -> 落默认模板
        check("首次加载默认值是 none",
              cfg["broadcast"] == pz.BROADCAST_NONE, cfg["broadcast"])

        cfg["broadcast"] = pz.BROADCAST_EVERY
        pz.save_config(path, cfg)
        again = pz.load_config(path)
        check("改成 every_frame 后能读回来",
              again["broadcast"] == pz.BROADCAST_EVERY, again["broadcast"])

        # 关键回归：老用户的 ini 里写的是 settle，升级后必须自动变成 none
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[animation]\nbroadcast = settle\n")
        check("旧 ini 的 settle 自动升级为 none（抽搐修复）",
              pz.load_config(path)["broadcast"] == pz.BROADCAST_NONE)

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[animation]\nbroadcast = 乱写的\n")
        check("ini 里乱写时回落到 none",
              pz.load_config(path)["broadcast"] == pz.BROADCAST_NONE)


def test_config_missing_key_is_safe():
    print("\n[9] 旧配置（没有 broadcast 键）也要能用")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pointer-zoom.ini")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[pointer]\nidle_size = 80\nclick_size = 24\n"
                     "[animation]\nfps = 60\n")
        cfg = pz.load_config(path)
        check("缺失时补上 none", cfg["broadcast"] == pz.BROADCAST_NONE)
        check("原有键没被弄丢", cfg["idle_size"] == 80 and cfg["fps"] == 60)
        pz.save_config(path, cfg)           # 不能因为缺键而抛异常
        check("save_config 能处理缺失键",
              "broadcast = none" in open(path, encoding="utf-8").read())


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("=" * 64)
    print("闪烁 / 抽搐修复回归测试（不触碰真实光标）")
    print("=" * 64)

    real_apply = pz.apply_size
    try:
        test_apply_size_flags()
        test_snap_actually_applies()
        test_animation_does_not_broadcast_per_frame()
        test_settle_mode_no_longer_broadcasts()
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
