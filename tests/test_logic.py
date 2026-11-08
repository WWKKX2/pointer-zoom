# -*- coding: utf-8 -*-
"""纯逻辑回归测试 —— 不安装钩子、不合成鼠标事件、不改动指针尺寸。

    python test_logic.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pointer_zoom as pz  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


def base_cfg(**over):
    cfg = {
        "original_size": 48, "idle_size": 96, "click_size": 32,
        "trigger": "left", "enabled": True,
        "press_ms": 140, "release_ms": 220,
        "press_curve": "ease_out_cubic", "release_curve": "ease_out_back",
        "strength": 1.70158, "fps": 60,
        "hk_toggle": "ctrl+alt+p", "hk_reset": "ctrl+alt+r", "hk_quit": "ctrl+alt+q",
    }
    cfg.update(over)
    return cfg


print("=" * 60)
print("1. 缓动曲线完整性")
print("=" * 60)
for name, fn in sorted(pz.CURVES.items()):
    a, b = fn(0.0), fn(1.0)
    check(f"{name:<20} f(0)={a:.4f}  f(1)={b:.4f}",
          abs(a) < 1e-6 and abs(b - 1.0) < 1e-6)

print()
print("=" * 60)
print("2. 触发键匹配")
print("=" * 60)
cases = [
    ("left", pz.WM_LBUTTONDOWN, True), ("left", pz.WM_RBUTTONDOWN, False),
    ("right", pz.WM_RBUTTONDOWN, True), ("right", pz.WM_LBUTTONDOWN, False),
    ("middle", pz.WM_MBUTTONDOWN, True), ("middle", pz.WM_LBUTTONDOWN, False),
    ("both", pz.WM_RBUTTONDOWN, True), ("both", pz.WM_LBUTTONDOWN, True),
    ("both", pz.WM_MBUTTONDOWN, False),
]
for trigger, msg, expect in cases:
    app = pz.PointerZoom(base_cfg(trigger=trigger))
    check(f"trigger={trigger:<7} 按下 0x{msg:04X} -> {expect}",
          app._matches(msg, 0) is expect)

print()
print("=" * 60)
print("3. 按下 / 松开状态迁移")
print("=" * 60)
app = pz.PointerZoom(base_cfg())
app.anim.current = 96.0
app.on_button(True)
check("按下 -> 目标为 click_size", app.anim.target == 32.0, f"target={app.anim.target}")
check("按下 -> pressed 置位", app.pressed is True)
check("按下 -> 用 press_curve", app.anim._curve is pz.CURVES["ease_out_cubic"])
check("按下 -> 时长为 press_ms", abs(app.anim._dur - 0.140) < 1e-6)

app.on_button(True)
check("按住时重复按下被忽略（防重入）", app.pressed is True)

app.on_button(False)
check("松开 -> 目标为 idle_size", app.anim.target == 96.0, f"target={app.anim.target}")
check("松开 -> pressed 复位", app.pressed is False)
check("松开 -> 用 release_curve", app.anim._curve is pz.CURVES["ease_out_back"])

app.on_button(False)
check("未按住时重复松开被忽略", app.pressed is False)

app_dis = pz.PointerZoom(base_cfg(enabled=False))
app_dis.anim.current = 48.0
app_dis.on_button(True)
check("停用时按下不触发", app_dis.pressed is False and app_dis.anim.target == 32.0)

print()
print("=" * 60)
print("4. 动画可打断：新动画从当前尺寸起步")
print("=" * 60)
app = pz.PointerZoom(base_cfg())
app.anim.current = 70.0
app.on_button(True)
check("起点采样自当前尺寸而非上一次目标", app.anim._from == 70.0,
      f"from={app.anim._from}")
app.anim.current = 45.0
app.on_button(False)
check("中途松开从中间尺寸起步", app.anim._from == 45.0, f"from={app.anim._from}")

print()
print("=" * 60)
print("5. 尺寸钳制")
print("=" * 60)
app = pz.PointerZoom(base_cfg())
app.anim.current = 250.0
app.anim.goto(999, 0, "linear")
check("goto 不越界（由 apply_size 钳制）", app.anim.target == 999.0,
      "目标值原样保留，钳制发生在写入系统时")
check("SIZE_MAX = 256", pz.SIZE_MAX == 256)
check("SIZE_FLOOR = 4（回弹过冲下限）", pz.SIZE_FLOOR == 4)

print()
print("=" * 60)
print("6. 快捷键解析")
print("=" * 60)
for text, expect_ok in [("ctrl+alt+p", True), ("ctrl+shift+r", True),
                        ("alt+q", True), ("p", False), ("ctrl+", False),
                        ("ctrl+alt+f13", False)]:
    r = pz.parse_hotkey(text)
    check(f"'{text}' -> {'可解析' if expect_ok else '应拒绝'}",
          (r is not None) is expect_ok, f"{r}")
r = pz.parse_hotkey("ctrl+alt+p")
check("ctrl+alt+p 修饰键含 NOREPEAT", r[0] & pz.MOD_NOREPEAT and r[0] & pz.MOD_CONTROL
      and r[0] & pz.MOD_ALT and r[1] == 0x50)

print()
print("=" * 60)
print("7. 新增流行曲线（sine / smootherstep / cubic-bezier）")
print("=" * 60)
import math

ss = pz.CURVES["smootherstep"]
check("smootherstep(0.5) = 0.5", abs(ss(0.5) - 0.5) < 1e-9, ss(0.5))
check("smootherstep 两端极缓", ss(0.1) < 0.05 and ss(0.9) > 0.95,
      f"f(0.1)={ss(0.1):.4f} f(0.9)={ss(0.9):.4f}")

check("ease_out_quint(0.5) = 0.96875",
      abs(pz.CURVES["ease_out_quint"](0.5) - 0.96875) < 1e-9)
check("ease_out_sine(0.5) = √2/2",
      abs(pz.CURVES["ease_out_sine"](0.5) - math.sin(math.pi / 4)) < 1e-9)
check("ease_in_out_sine(0.5) = 0.5",
      abs(pz.CURVES["ease_in_out_sine"](0.5) - 0.5) < 1e-9)

# cubic-bezier 求解器：css_ease 的中点值有公开参考值可对照
check("css_ease(0.5) ≈ 0.8024（浏览器参考值）",
      abs(pz.CURVES["css_ease"](0.5) - 0.8024) < 1e-3,
      pz.CURVES["css_ease"](0.5))
for name in ("css_ease", "material_emphasized", "material_decelerate"):
    ys = [pz.CURVES[name](i / 40.0) for i in range(41)]
    mono = all(b >= a - 1e-9 for a, b in zip(ys, ys[1:]))
    check(f"{name} 全程单调不减且不越界",
          mono and min(ys) >= -1e-9 and max(ys) <= 1.0 + 1e-9,
          f"min={min(ys):.4f} max={max(ys):.4f}")
check("material_decelerate 起步快（f(0.25)>0.6）",
      pz.CURVES["material_decelerate"](0.25) > 0.6,
      pz.CURVES["material_decelerate"](0.25))
check("material_emphasized 中段快（f(0.5)>0.75）",
      pz.CURVES["material_emphasized"](0.5) > 0.75,
      pz.CURVES["material_emphasized"](0.5))
check("共 24 条曲线", len(pz.CURVES) == 24, len(pz.CURVES))

print()
print("=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for name in FAIL:
        print(f"  FAILED: {name}")
print("=" * 60)
sys.exit(1 if FAIL else 0)
