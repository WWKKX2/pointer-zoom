# -*- coding: utf-8 -*-
"""指针缩放 —— 平时大、按下鼠标时按曲线动画缩小、松开动画恢复。

设计要点
--------
1. 用系统原生光标缩放（未公开 SPI 0x2029 / SPI_SETCURSORBASESIZE），
   指针始终是**真正的系统光标**，所以动画期间指针可以自由移动、正常点击，
   不需要拦截、转发或重绘任何输入 —— 交互零影响。
2. 低级鼠标钩子回调只做「置一个标志位」这一件事，绝不在回调里做耗时操作，
   否则会给每一次鼠标事件带来延迟（超过 LowLevelHooksTimeout 还会被系统摘钩）。
3. 动画在独立线程里跑，按 perf_counter 计时，与鼠标事件完全解耦。
4. 全程不写注册表：临时改尺寸只带 SPIF_SENDCHANGE。
   注册表里始终保存着用户自己的设置，也就是我们的「原始大小」真值来源。

用法
----
    pythonw pointer_zoom.py            正常运行（无控制台窗口）
    python  pointer_zoom.py --reset    恢复原始大小后退出
    python  pointer_zoom.py --curves   列出所有可用动画曲线
"""

import argparse
import atexit
import configparser
import ctypes
import os
import signal
import sys
import threading
import time
import winreg
from ctypes import wintypes

# ---------------------------------------------------------------- 基础常量

SPI_SETCURSORBASESIZE = 0x2029
SPIF_SENDCHANGE = 0x02

# 何时广播 WM_SETTINGCHANGE。动画中途逐帧广播是闪烁的元凶，见 apply_size。
BROADCAST_SETTLE = "settle"        # 只在动画落定时广播一次（默认）
BROADCAST_EVERY = "every_frame"    # 逐帧广播（旧行为，留作退路）

CURSOR_KEY = r"Control Panel\Cursors"
A11Y_KEY = r"SOFTWARE\Microsoft\Accessibility"

SIZE_MIN = 1
SIZE_MAX = 256          # Windows 设置页第 15 档就是 256px
SIZE_FLOOR = 4          # 回弹类曲线会过冲，给个下限防止缩成看不见

WH_MOUSE_LL = 14
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x4000
VK = {
    "lbutton": 0x01, "rbutton": 0x02, "mbutton": 0x04,
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10,
    **{chr(c).lower(): c for c in range(0x41, 0x5B)},
    **{str(d): 0x30 + d for d in range(10)},
}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
HHOOK = wintypes.HANDLE

# 关键：不显式声明 argtypes 的话，ctypes 会把 Python int 按 32 位 c_int 转换，
# 64 位下的 lParam（是个指针）会直接 OverflowError，钩子回调每次必炸。
user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT
]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                     wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = HHOOK
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int,
                                  wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT),
                ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND),
                ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD),
                ("pt", wintypes.POINT)]


user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int          # 注意可能是 -1，不能用 BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT


# ---------------------------------------------------------------- 动画曲线
# 全部返回 [0,1] 的进度；back / elastic 会过冲，调用方负责钳制尺寸。


def _c_linear(t):
    return t


def _c_ease_in_quad(t):
    return t * t


def _c_ease_out_quad(t):
    return 1 - (1 - t) ** 2


def _c_ease_in_out_quad(t):
    return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2


def _c_ease_in_cubic(t):
    return t ** 3


def _c_ease_out_cubic(t):
    return 1 - (1 - t) ** 3


def _c_ease_in_out_cubic(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def _c_ease_in_quart(t):
    return t ** 4


def _c_ease_out_quart(t):
    return 1 - (1 - t) ** 4


def _c_ease_out_expo(t):
    return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


def _c_ease_in_expo(t):
    return 0.0 if t <= 0 else 2 ** (10 * t - 10)


def _c_ease_in_out_expo(t):
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    return 2 ** (20 * t - 10) / 2 if t < 0.5 else (2 - 2 ** (-20 * t + 10)) / 2


def _c_ease_out_back(t, s=1.70158):
    return 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2


def _c_ease_in_back(t, s=1.70158):
    return (s + 1) * t ** 3 - s * t * t


def _c_ease_in_out_back(t, s=1.70158):
    s *= 1.525
    if t < 0.5:
        return ((2 * t) ** 2 * ((s + 1) * 2 * t - s)) / 2
    return ((2 * t - 2) ** 2 * ((s + 1) * (t * 2 - 2) + s) + 2) / 2


def _c_ease_out_elastic(t):
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    c4 = (2 * 3.141592653589793) / 3
    return 2 ** (-10 * t) * __import__("math").sin((t * 10 - 0.75) * c4) + 1


def _c_ease_out_bounce(t):
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


CURVES = {
    "linear": _c_linear,
    "ease_in_quad": _c_ease_in_quad,
    "ease_out_quad": _c_ease_out_quad,
    "ease_in_out_quad": _c_ease_in_out_quad,
    "ease_in_cubic": _c_ease_in_cubic,
    "ease_out_cubic": _c_ease_out_cubic,
    "ease_in_out_cubic": _c_ease_in_out_cubic,
    "ease_in_quart": _c_ease_in_quart,
    "ease_out_quart": _c_ease_out_quart,
    "ease_in_expo": _c_ease_in_expo,
    "ease_out_expo": _c_ease_out_expo,
    "ease_in_out_expo": _c_ease_in_out_expo,
    "ease_in_back": _c_ease_in_back,
    "ease_out_back": _c_ease_out_back,
    "ease_in_out_back": _c_ease_in_out_back,
    "ease_out_elastic": _c_ease_out_elastic,
    "ease_out_bounce": _c_ease_out_bounce,
}

CURVE_NOTES = {
    "linear": "匀速，最朴素",
    "ease_out_quad": "快出慢停，轻快",
    "ease_out_cubic": "比 quad 更明显的减速，推荐给「按下缩小」",
    "ease_out_quart": "减速最强烈，一按就到位",
    "ease_out_expo": "几乎瞬间到达，末尾极缓",
    "ease_in_out_cubic": "两头慢中间快，柔和",
    "ease_in_back": "先往回缩一点再冲出去",
    "ease_out_back": "冲过头再弹回来，推荐给「松开恢复」",
    "ease_in_out_back": "两端都带回弹",
    "ease_out_elastic": "橡皮筋式抖动，最活泼",
    "ease_out_bounce": "落地弹跳",
}


# ---------------------------------------------------------------- 尺寸读写

def read_system_cursor_size():
    """读用户自己的指针尺寸设置，也就是我们的「原始大小」真值。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CURSOR_KEY) as key:
            value = winreg.QueryValueEx(key, "CursorBaseSize")[0]
            if isinstance(value, int) and value > 0:
                return value
    except (FileNotFoundError, OSError):
        pass
    return 32


def read_accessibility_step():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, A11Y_KEY) as key:
            return winreg.QueryValueEx(key, "CursorSize")[0]
    except (FileNotFoundError, OSError):
        return None


def apply_size(px, notify=False):
    """只改内存、不写注册表。返回是否成功。

    `notify=True` 会额外带上 SPIF_SENDCHANGE，向**所有顶层窗口**广播
    WM_SETTINGCHANGE。

    动画帧绝不能带它 —— 60fps 就是每秒 60 次全系统广播：

      · 每个响应 WM_SETTINGCHANGE 的程序都会重新读一遍系统光标设置。
        而我们的动画只改内存、不写注册表，注册表里还是「原始大小」，
        于是这些程序会把光标按旧值重设回去，下一帧我们再改回来 ——
        肉眼看到的就是动画期间指针一闪一闪。
      · 顺带还有一波没必要的重绘。

    所以动画中途一律用 flags=0（实测这一样会让光标实时改尺寸，
    耗时 0.70ms vs 带广播的 0.68ms，说明重绘发生在 SPI 调用里，
    跟广播无关），只在动画落定的那一帧广播一次。
    """
    px = int(max(SIZE_MIN, min(SIZE_MAX, px)))
    flags = SPIF_SENDCHANGE if notify else 0
    return bool(user32.SystemParametersInfoW(
        SPI_SETCURSORBASESIZE, 0, ctypes.c_void_p(px), flags))


# ---------------------------------------------------------------- 动画引擎

class Animator(threading.Thread):
    """独立线程驱动尺寸动画。

    外部只改 `target`，线程自己负责从「当前尺寸」平滑过渡过去，
    所以快速连点时不会跳变。
    """

    def __init__(self, fps=60):
        super().__init__(daemon=True)
        self.fps = max(10, min(144, int(fps)))
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()

        self.target = 32.0
        self.current = 32.0
        self._from = 32.0
        self._t0 = time.perf_counter()
        self._dur = 0.0
        self._curve = _c_linear
        self._strength = 1.70158
        self._applied = None          # 已下发给系统的整数像素值
        self._settle_done = True      # 当前这次动画是否已经广播过落定帧
        self._active = False
        self.broadcast = BROADCAST_SETTLE

    def goto(self, size, duration_ms, curve_name, strength=1.70158):
        with self._lock:
            self._from = self.current
            self.target = float(size)
            self._dur = max(0.0, duration_ms / 1000.0)
            self._t0 = time.perf_counter()
            self._curve = CURVES.get(curve_name, _c_linear)
            self._strength = strength
            self._active = self._dur > 0 and abs(self.target - self.current) > 0.5
            if not self._active:
                self.current = self.target
            else:
                self._settle_done = False
        if not self._active:
            # 关键：0ms / snap 必须真的下发到系统。
            # 之前这里只改了内存里的 current 就返回，于是「启动时切到平时大小」
            # 「恢复原始大小」「快捷键停用」全是空操作 —— 指针一直停在旧尺寸，
            # 直到下一次动画的第一帧才被顺手带过去，表现为突然一跳。
            self._apply_now(self.target, notify=True)
        self._wake.set()

    def snap(self, size):
        self.goto(size, 0, "linear")

    def _apply_now(self, size, notify):
        """把尺寸下发到系统，并记住下发过的整数像素值。

        去重按**整数像素**而不是浮点阈值：光标尺寸只认整数，
        浮点阈值会让「四舍五入后其实没变」的帧也去调一次 SPI。
        """
        px = int(round(max(SIZE_FLOOR, min(SIZE_MAX, size))))
        if px == self._applied and not notify:
            return False
        apply_size(px, notify=notify)
        self._applied = px
        return True

    def stop(self):
        self._stop.set()
        self._wake.set()

    def run(self):
        winmm.timeBeginPeriod(1)          # 把系统定时器精度提到 1ms
        try:
            while not self._stop.is_set():
                with self._lock:
                    active = self._active
                    target = self.target
                    frm, t0, dur, curve = self._from, self._t0, self._dur, self._curve
                    strength = self._strength

                if not active:
                    self._wake.wait(0.25)
                    self._wake.clear()
                    continue

                elapsed = time.perf_counter() - t0
                t = 1.0 if dur <= 0 else min(1.0, elapsed / dur)
                if curve in (_c_ease_in_back, _c_ease_out_back, _c_ease_in_out_back):
                    eased = curve(t, strength)
                else:
                    eased = curve(t)

                size = frm + (target - frm) * eased
                size = max(SIZE_FLOOR, min(SIZE_MAX, size))
                done = t >= 1.0

                with self._lock:
                    self.current = target if done else size
                    if done:
                        self._active = False

                if done:
                    # 落定：钉死最终值，并广播一次让系统与各程序同步
                    if not self._settle_done:
                        self._apply_now(target, notify=True)
                        self._settle_done = True
                    else:
                        self._apply_now(target, notify=False)
                else:
                    # 动画帧：只改内存，绝不广播
                    self._apply_now(
                        size, notify=self.broadcast == BROADCAST_EVERY)

                time.sleep(1.0 / self.fps)
        finally:
            winmm.timeEndPeriod(1)


# ---------------------------------------------------------------- 配置

INI_TEMPLATE = """\
; 指针缩放配置文件 —— 图形界面保存时也会覆写这里，手改同样有效

[general]
; 后台运行：关闭窗口时不退出，缩到通知栏（托盘图标），
; 左键点托盘图标找回窗口，右键菜单可以彻底退出。
; false = 点 × 直接退出
close_to_tray = {close_to_tray}

[pointer]
; 不点击时（平时）的指针大小，像素。范围 1-256。
; 也可以写 auto，表示沿用系统当前设置。
idle_size = {idle_size}
; 按下鼠标时缩小到的目标大小，像素。
click_size = {click_size}
; 触发按键：left / right / middle / both（左右键都触发）
trigger = {trigger}
; 是否启用动画
enabled = {enabled}

[animation]
; 按下（缩小）动画时长，毫秒。0 = 瞬间切换
press_duration_ms = {press_ms}
; 松开（恢复）动画时长，毫秒
release_duration_ms = {release_ms}
; 缩小用哪条曲线，可用 --curves 查看全部
press_curve = {press_curve}
; 恢复用哪条曲线
release_curve = {release_curve}
; back / elastic 这类过冲曲线的强度，默认 {strength}
curve_strength = {strength}
; 动画帧率。每一帧 = 系统重绘一次光标，帧率越高系统开销越大、也越容易看出闪。
; 60 已经足够顺滑（200ms 的动画也有 12 帧），想要更细腻可以往上调，卡就降到 30
fps = {fps}
; 什么时候广播 WM_SETTINGCHANGE：
;   settle      只在动画落定时广播一次（默认，动画最干净）
;   every_frame 每帧都广播。这是旧行为，帧率多高就每秒广播多少次；
;               响应广播的程序会按注册表里的旧尺寸把光标重设回去，
;               动画期间看起来就是一闪一闪。除非发现指针尺寸不生效，
;               否则不要改。
broadcast = {broadcast}

[hotkeys]
; 全局快捷键，格式 ctrl+alt+字母
toggle = {hk_toggle}
reset = {hk_reset}
quit = {hk_quit}
"""

# 出厂默认值 —— 就是作者自己长期用着最顺手的那一套
INI_DEFAULTS = {
    "close_to_tray": "true",
    "idle_size": 48, "click_size": 25, "trigger": "left", "enabled": "true",
    "press_ms": 80, "release_ms": 83,
    "press_curve": "ease_in_quad", "release_curve": "ease_out_quad",
    "strength": "3.0", "fps": 60, "broadcast": BROADCAST_SETTLE,
    "hk_toggle": "ctrl+alt+p", "hk_reset": "ctrl+alt+r", "hk_quit": "ctrl+alt+q",
}

DEFAULT_INI = INI_TEMPLATE.format(**INI_DEFAULTS)

_BROADCAST_ALIASES = {
    "settle": BROADCAST_SETTLE, "once": BROADCAST_SETTLE,
    "end": BROADCAST_SETTLE, "on_settle": BROADCAST_SETTLE,
    "every_frame": BROADCAST_EVERY, "every": BROADCAST_EVERY,
    "always": BROADCAST_EVERY, "frame": BROADCAST_EVERY,
}


def _norm_broadcast(text):
    """容错解析 broadcast 配置，认不出来就回到安全的默认值。"""
    return _BROADCAST_ALIASES.get((text or "").strip().lower(),
                                  BROADCAST_SETTLE)


def save_config(path, cfg):
    """把当前配置写回 ini。图形界面每次改动后调用。"""
    text = INI_TEMPLATE.format(
        close_to_tray="true" if cfg.get("close_to_tray") else "false",
        idle_size=cfg["idle_size"],
        click_size=cfg["click_size"],
        trigger=cfg["trigger"],
        enabled="true" if cfg["enabled"] else "false",
        press_ms=cfg["press_ms"],
        release_ms=cfg["release_ms"],
        press_curve=cfg["press_curve"],
        release_curve=cfg["release_curve"],
        strength=cfg["strength"],
        fps=cfg["fps"],
        broadcast=cfg.get("broadcast", BROADCAST_SETTLE),
        hk_toggle=cfg["hk_toggle"],
        hk_reset=cfg["hk_reset"],
        hk_quit=cfg["hk_quit"],
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def load_config(path):
    cfg = configparser.ConfigParser()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(DEFAULT_INI)
    cfg.read(path, encoding="utf-8")

    def gint(sec, key, default):
        try:
            return cfg.getint(sec, key)
        except (configparser.Error, ValueError):
            return default

    def gstr(sec, key, default):
        try:
            return cfg.get(sec, key).strip()
        except configparser.Error:
            return default

    # 兜底值一律取自 INI_DEFAULTS，不再各写一份字面量 ——
    # 否则改默认值时极易漏掉这里，造成「新生成的 ini」和「缺键时的兜底」不一致。
    # 注意 gstr/gint 在键缺失时是「原样返回默认值」，不做类型转换，
    # 所以这里要自己保证类型对得上（idle_size 的默认值是 int，得先转成 str）。
    D = INI_DEFAULTS
    idle_raw = str(gstr("pointer", "idle_size", D["idle_size"]))
    original = read_system_cursor_size()
    idle = original if idle_raw.lower() == "auto" else gint(
        "pointer", "idle_size", D["idle_size"])

    return {
        "close_to_tray": gstr("general", "close_to_tray",
                              D["close_to_tray"]).lower() in (
            "1", "true", "yes", "on"),
        "original_size": original,
        "idle_size": max(SIZE_MIN, min(SIZE_MAX, idle)),
        "click_size": max(SIZE_MIN, min(SIZE_MAX, gint(
            "pointer", "click_size", D["click_size"]))),
        "trigger": gstr("pointer", "trigger", D["trigger"]).lower(),
        "enabled": gstr("pointer", "enabled", D["enabled"]).lower() in (
            "1", "true", "yes", "on"),
        "press_ms": gint("animation", "press_duration_ms", D["press_ms"]),
        "release_ms": gint("animation", "release_duration_ms", D["release_ms"]),
        "press_curve": gstr("animation", "press_curve", D["press_curve"]),
        "release_curve": gstr("animation", "release_curve", D["release_curve"]),
        "strength": float(gstr("animation", "curve_strength", D["strength"])
                          or D["strength"]),
        "fps": gint("animation", "fps", D["fps"]),
        "broadcast": _norm_broadcast(
            gstr("animation", "broadcast", BROADCAST_SETTLE)),
        "hk_toggle": gstr("hotkeys", "toggle", D["hk_toggle"]),
        "hk_reset": gstr("hotkeys", "reset", D["hk_reset"]),
        "hk_quit": gstr("hotkeys", "quit", D["hk_quit"]),
    }


def parse_hotkey(text):
    """'ctrl+alt+p' -> (modifiers, vk)，解析失败返回 None。"""
    mods = 0
    vk = None
    for part in text.lower().split("+"):
        part = part.strip()
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in VK and vk is None:
            vk = VK[part]
    if vk is None or mods == 0:
        return None
    return mods | MOD_NOREPEAT, vk


# ---------------------------------------------------------------- 主程序

HK_TOGGLE, HK_RESET, HK_QUIT = 1, 2, 3


class PointerZoom:
    def __init__(self, cfg):
        self.cfg = cfg
        self.original = cfg["original_size"]
        self.enabled = cfg["enabled"]
        self.anim = Animator(cfg["fps"])
        self.pressed = False
        self.hotkeys = {}
        self.verbose = False
        self.quit_callback = None   # GUI 版设置它：快捷键退出走界面的完整退出流程
        self._hook = None
        self._hook_proc = None       # 必须持有引用，否则回调会被 GC 掉
        self._restored = False

    # ---- 生命周期

    def start(self):
        # 启动自愈：先把尺寸归位，修掉上一次异常退出的残留
        apply_size(self.original, notify=True)
        time.sleep(0.05)
        self.anim.current = float(self.original)
        self.anim.broadcast = self.cfg.get("broadcast", BROADCAST_SETTLE)
        self.anim.snap(self.cfg["idle_size"] if self.enabled else self.original)
        self.anim.start()
        atexit.register(self.restore)

    def restore(self):
        if self._restored:
            return
        self._restored = True
        self.anim.stop()
        apply_size(self.original, notify=True)
        print(f"[退出] 指针已恢复为原始大小 {self.original}px")

    # ---- 鼠标事件（钩子回调里只允许做这一件事）

    def on_button(self, down):
        if not self.enabled:
            return
        if down:
            if self.pressed:
                return
            self.pressed = True
            self.anim.goto(self.cfg["click_size"], self.cfg["press_ms"],
                           self.cfg["press_curve"], self.cfg["strength"])
            if self.verbose:
                print(f"[按下] {self.anim.current:.0f} -> {self.cfg['click_size']}px"
                      f"  {self.cfg['press_ms']}ms {self.cfg['press_curve']}")
        else:
            if not self.pressed:
                return
            self.pressed = False
            self.anim.goto(self.cfg["idle_size"], self.cfg["release_ms"],
                           self.cfg["release_curve"], self.cfg["strength"])
            if self.verbose:
                print(f"[松开] {self.anim.current:.0f} -> {self.cfg['idle_size']}px"
                      f"  {self.cfg['release_ms']}ms {self.cfg['release_curve']}")

    def _matches(self, msg_down, msg_up):
        trigger = self.cfg["trigger"]
        if trigger == "left":
            return msg_down == WM_LBUTTONDOWN or msg_up == WM_LBUTTONUP
        if trigger == "right":
            return msg_down == WM_RBUTTONDOWN or msg_up == WM_RBUTTONUP
        if trigger == "middle":
            return msg_down == WM_MBUTTONDOWN or msg_up == WM_MBUTTONUP
        if trigger == "both":
            return msg_down in (WM_LBUTTONDOWN, WM_RBUTTONDOWN) or \
                   msg_up in (WM_LBUTTONUP, WM_RBUTTONUP)
        return msg_down == WM_LBUTTONDOWN or msg_up == WM_LBUTTONUP

    def hook_proc(self, code, wparam, lparam):
        # 回调里绝不允许异常逃逸：一旦抛出去，这次 CallNextHookEx 就不会执行，
        # Windows 会判定钩子超时并静默摘掉它。所以整段包死。
        try:
            if code >= 0:
                if self._matches(wparam, 0):
                    self.on_button(True)
                elif self._matches(0, wparam):
                    self.on_button(False)
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    # ---- 快捷键

    def _register_one(self, hk_id, primary, label, fallbacks):
        for text in (primary,) + tuple(fallbacks):
            parsed = parse_hotkey(text)
            if not parsed:
                continue
            if user32.RegisterHotKey(None, hk_id, parsed[0], parsed[1]):
                if text != primary:
                    print(f"[提示] {primary} 被别的程序占用了，"
                          f"{label} 自动改用 {text}")
                return text
        print(f"[警告] {label} 的快捷键注册失败，请在 ini 里换一组")
        return None

    def register_hotkeys(self):
        self.hotkeys["toggle"] = self._register_one(
            HK_TOGGLE, self.cfg["hk_toggle"], "启用/停用",
            ["ctrl+alt+shift+p", "ctrl+alt+0"])
        self.hotkeys["reset"] = self._register_one(
            HK_RESET, self.cfg["hk_reset"], "恢复原始大小",
            ["ctrl+alt+shift+r", "ctrl+alt+9"])
        self.hotkeys["quit"] = self._register_one(
            HK_QUIT, self.cfg["hk_quit"], "退出",
            ["ctrl+alt+shift+q", "ctrl+alt+8"])

    def on_hotkey(self, hk_id):
        if hk_id == HK_TOGGLE:
            self.enabled = not self.enabled
            if self.enabled:
                self.anim.snap(self.cfg["idle_size"])
                print(f"[快捷键] 已启用（指针 {self.cfg['idle_size']}px）")
            else:
                self.pressed = False
                self.anim.snap(self.original)
                print(f"[快捷键] 已停用（指针恢复 {self.original}px）")
        elif hk_id == HK_RESET:
            self.pressed = False
            self.anim.snap(self.original)
            print(f"[快捷键] 指针已恢复原始大小 {self.original}px")
        elif hk_id == HK_QUIT:
            print("[快捷键] 退出")
            self.restore()
            if self.quit_callback:
                # GUI 版：交给主线程做完整退出（存配置、摘托盘、停钩子、关窗口）
                self.quit_callback()
            else:
                user32.PostQuitMessage(0)

    # ---- 钩子服务（可跑在任意线程，GUI 版会把它放到独立线程）

    def install_hook(self):
        self._hook_proc = HOOKPROC(self.hook_proc)     # 必须持引用，否则被 GC
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._hook_proc, None, 0)
        if not self._hook:
            raise ctypes.WinError(ctypes.get_last_error())

    def message_loop(self):
        """阻塞式消息循环：分发 WM_HOTKEY，其余交给默认处理。"""
        msg = MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                self.on_hotkey(int(msg.wParam))
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

    def teardown_hook(self):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        for hk_id in (HK_TOGGLE, HK_RESET, HK_QUIT):
            user32.UnregisterHotKey(None, hk_id)

    # ---- 控制台版主循环

    def run(self):
        self.start()
        self.install_hook()
        self.register_hotkeys()
        self.banner()
        try:
            self.message_loop()
        finally:
            self.teardown_hook()
            self.restore()


class HookThread(threading.Thread):
    """把钩子 + 快捷键注册 + 消息循环放到独立线程，好让 tkinter 独占主线程。"""

    def __init__(self, app):
        super().__init__(daemon=True, name="hook")
        self.app = app
        self.thread_id = None
        self.error = None

    def run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        try:
            self.app.install_hook()
            self.app.register_hotkeys()
            self.app.message_loop()
        except Exception as exc:                       # noqa: BLE001
            self.error = exc
        finally:
            self.app.teardown_hook()

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)

    def banner(self):
        c = self.cfg
        print("=" * 64)
        print("指针缩放 已启动 —— 指针全程是真实系统光标，动画期间照常移动点击")
        print("=" * 64)
        print(f"  原始大小（退出时恢复） : {self.original}px"
              f"   [系统设置第 {read_accessibility_step()} 档]")
        print(f"  平时大小               : {c['idle_size']}px")
        print(f"  按下缩小到             : {c['click_size']}px")
        print(f"  触发按键               : {c['trigger']}")
        print(f"  按下动画               : {c['press_ms']}ms / {c['press_curve']}")
        print(f"  松开动画               : {c['release_ms']}ms / {c['release_curve']}")
        print(f"  帧率                   : {c['fps']}fps")
        print("-" * 64)
        print(f"  {self.hotkeys.get('toggle') or '(未注册)':<16} 启用 / 停用")
        print(f"  {self.hotkeys.get('reset') or '(未注册)':<16} 立刻恢复原始大小")
        print(f"  {self.hotkeys.get('quit') or '(未注册)':<16} 退出")
        print("=" * 64)
        if c["idle_size"] >= SIZE_MAX:
            print("[提示] 平时大小已是上限 256px，按下缩小仍有动画，但无法再往上放")
        sys.stdout.flush()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    here = os.path.dirname(os.path.abspath(__file__))
    default_ini = os.path.join(os.path.dirname(here), "pointer-zoom.ini")

    parser = argparse.ArgumentParser(description="指针缩放：平时大，按下按曲线缩小")
    parser.add_argument("--config", default=default_ini, help="配置文件路径")
    parser.add_argument("--reset", action="store_true", help="恢复原始大小后退出")
    parser.add_argument("--curves", action="store_true", help="列出所有可用动画曲线")
    parser.add_argument("--verbose", action="store_true", help="打印每次按下 / 松开")
    args = parser.parse_args()

    if args.curves:
        print("可用动画曲线（写在 ini 的 press_curve / release_curve）:\n")
        for name, note in CURVE_NOTES.items():
            print(f"  {name:<20} {note}")
        others = [n for n in CURVES if n not in CURVE_NOTES]
        if others:
            print(f"\n  其他: {', '.join(sorted(others))}")
        return

    if args.reset:
        size = read_system_cursor_size()
        ok = apply_size(size, notify=True)
        print(f"[重置] 指针已恢复为 {size}px  返回={ok}")
        return

    cfg = load_config(args.config)
    print(f"[配置] {args.config}")

    app = PointerZoom(cfg)
    app.verbose = args.verbose

    def _sigint(_sig, _frm):
        app.restore()
        os._exit(0)

    signal.signal(signal.SIGINT, _sigint)
    try:
        app.run()
    except KeyboardInterrupt:
        app.restore()
    except Exception as exc:
        app.restore()
        print(f"[错误] {exc}")
        raise


if __name__ == "__main__":
    main()
