# -*- coding: utf-8 -*-
"""托盘模块测试 —— 全部纯逻辑，不建真图标、不碰真实通知栏。

流程约定（和项目其它测试一致）
  ROOT = 仓库根；src/ 加入 sys.path
"""

import os
import queue
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pz_tray  # noqa: E402
import pointer_zoom as pz  # noqa: E402

_PASS = _FAIL = 0


def check(name, cond, detail=""):
    global _PASS, _FAIL
    ok = bool(cond)
    _PASS += ok
    _FAIL += not ok
    mark = "通过" if ok else "失败"
    line = f"[{mark}] {name}"
    if detail and not ok:
        line += f"  ->  {detail}"
    print(line)
    return ok


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("=" * 62)
    print("托盘模块测试（纯逻辑，不碰真实通知栏）")
    print("=" * 62)

    # ---------------------------------------------------------- 图标位图
    print("\n[1] 图标位图生成")
    xor = pz_tray.icon_xor_bits()
    and_mask = pz_tray.icon_and_bits()
    check("XOR 位图大小 = 32*32*4", len(xor) == 32 * 32 * 4, len(xor))
    check("AND 掩码大小 = 128 字节", len(and_mask) == 128, len(and_mask))
    check("AND 掩码全 0（按 XOR 的 alpha 绘制）", not any(and_mask))

    # 四角必须透明
    corners = [(0, 0), (31, 0), (0, 31), (31, 31)]
    bad = [c for c in corners if xor[(c[1] * 32 + c[0]) * 4 + 3] != 0]
    check("四角透明", not bad, f"{bad}")

    # 箭身内部一点 (14.5, 10.5) 应该是白色不透明
    off = (10 * 32 + 14) * 4
    check("箭身不透明白色",
          xor[off + 3] == 255 and xor[off] > 200 and xor[off + 2] > 200,
          f"rgba={tuple(xor[off:off + 4])}")

    # 箭头左边缘 (x=11) 应该是深色描边
    off = (10 * 32 + 11) * 4
    edge = tuple(xor[off:off + 4])
    check("边缘有深色描边",
          edge[3] == 255 and edge[0] < 100 and edge[2] < 100, f"rgba={edge}")

    # 每个像素要么全透明，要么不透明（半透明只该出现在理论上，这里没有抗锯齿）
    alphas = {xor[i] for i in range(3, len(xor), 4)}
    check("alpha 只有 0/255 两档", alphas <= {0, 255}, f"{sorted(alphas)}")

    # ---------------------------------------------------------- 配置
    print("\n[2] close_to_tray 配置")
    check("DEFAULT_INI 写了 close_to_tray", "close_to_tray = true"
          in pz.DEFAULT_INI)
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "pointer-zoom.ini")
        cfg = pz.load_config(path)              # 不存在 -> 落默认模板
        check("默认开启后台运行", cfg["close_to_tray"] is True)
        cfg["close_to_tray"] = False
        pz.save_config(path, cfg)
        check("关掉后能读回来",
              pz.load_config(path)["close_to_tray"] is False)
        cfg["close_to_tray"] = True
        pz.save_config(path, cfg)
        check("再开能读回来", pz.load_config(path)["close_to_tray"] is True)
        # 旧 ini 没有 [general] 段也不能炸
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("[pointer]\nidle_size = 80\n")
        check("旧配置缺键时默认开启",
              pz.load_config(path)["close_to_tray"] is True)
        pz.save_config(path, pz.load_config(path))   # 缺键也不能写炸
        check("缺键时 save_config 不炸且补上",
              "close_to_tray = true" in open(path, encoding="utf-8").read())

    # ---------------------------------------------------------- 动作分发
    print("\n[3] 动作分发（不建真窗口）")
    q = queue.Queue()
    try:
        pz_tray.TrayIcon(["not", "a", "queue"])
        check("actions 必须是 Queue", False, "没抛 TypeError")
    except TypeError:
        check("actions 必须是 Queue", True)

    t = pz_tray.TrayIcon(q)
    t._handle(1234, pz_tray.WM_APP_TRAY, 0, pz_tray.WM_LBUTTONUP)
    check("左键单击 -> show", q.get_nowait() == "show")
    t._handle(1234, pz_tray.WM_APP_TRAY, 0, pz_tray.WM_LBUTTONDBLCLK)
    check("双击 -> show", q.get_nowait() == "show")
    t._handle(1234, pz_tray.WM_APP_TRAY, 0, 0x0200)   # WM_MOUSEMOVE，不该有动作
    t._handle(1234, 0x9999, 0, 0)                     # 无关消息
    check("无关消息不产生动作", q.empty())

    # 回调里抛异常必须被吞掉（托盘窗口的 wndproc 不能逃异常）
    class Boom:
        def put(self, _x):
            raise RuntimeError("boom")

    t.actions = Boom()
    rc = t._proc(None, pz_tray.WM_APP_TRAY, 0, pz_tray.WM_LBUTTONUP)
    check("回调异常被吞且 wndproc 正常返回", isinstance(rc, int))

    print("\n" + "=" * 62)
    print(f"通过 {_PASS} 项，失败 {_FAIL} 项")
    print("=" * 62)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
