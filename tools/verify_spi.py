"""验证 SPI_SETCURSORBASESIZE (0x2029) 是否在本机可用。

只读探测 + 一次短暂改变 + 立即恢复。全程 try/finally 保证恢复。
"""

import ctypes
import sys
import time
import winreg
from ctypes import wintypes

SPI_SETCURSORBASESIZE = 0x2029
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

CURSOR_KEY = r"Control Panel\Cursors"
A11Y_KEY = r"SOFTWARE\Microsoft\Accessibility"

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT
]
user32.SystemParametersInfoW.restype = wintypes.BOOL


def read_dword(root, path, name):
    try:
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"<err {exc.winerror}>"


def call_spi(px, flags):
    ctypes.set_last_error(0)
    ok = user32.SystemParametersInfoW(
        SPI_SETCURSORBASESIZE, 0, ctypes.c_void_p(px), flags
    )
    return bool(ok), ctypes.get_last_error()


def main():
    print("=" * 62)
    print("SPI_SETCURSORBASESIZE (0x2029) 本机验证")
    print("=" * 62)

    base = read_dword(winreg.HKEY_CURRENT_USER, CURSOR_KEY, "CursorBaseSize")
    step = read_dword(winreg.HKEY_CURRENT_USER, A11Y_KEY, "CursorSize")
    print(f"[读取] CursorBaseSize = {base!r}   (None = 注册表未写, 系统按 32px 处理)")
    print(f"[读取] Accessibility\\CursorSize = {step!r}  (1-15 档)")
    print(f"[读取] GetSystemMetrics(SM_CXCURSOR) = "
          f"{user32.GetSystemMetrics(13)}px  (13 = SM_CXCURSOR)")

    orig = base if isinstance(base, int) and base > 0 else 32
    print(f"[判定] 当前实际指针尺寸按 {orig}px 计\n")

    # --- 探测 1: 不带任何标志，纯内存改动，不写注册表 ---
    target = min(orig * 2, 256)
    print(f"[探测 1] SystemParametersInfo(0x2029, size={target}, flags=0)")
    ok, err = call_spi(target, 0)
    print(f"         -> 返回 {ok}  GetLastError={err}")
    if not ok:
        print("         SPI 未被识别（ERROR_INVALID_PARAMETER=87 即不支持）")
    time.sleep(1.2)

    print(f"[恢复 1] SystemParametersInfo(0x2029, size={orig}, flags=0)")
    ok_r, err_r = call_spi(orig, 0)
    print(f"         -> 返回 {ok_r}  GetLastError={err_r}")
    print(f"[回读] CursorBaseSize = "
          f"{read_dword(winreg.HKEY_CURRENT_USER, CURSOR_KEY, 'CursorBaseSize')!r}"
          "   (flags=0 时不应改变)\n")

    # --- 探测 2: 带 SPIF_SENDCHANGE，模拟动画帧的调用方式 ---
    print(f"[探测 2] SystemParametersInfo(0x2029, size={target}, flags=SPIF_SENDCHANGE)")
    ok2, err2 = call_spi(target, SPIF_SENDCHANGE)
    print(f"         -> 返回 {ok2}  GetLastError={err2}")
    time.sleep(1.2)

    print(f"[恢复 2] SystemParametersInfo(0x2029, size={orig}, flags=SPIF_SENDCHANGE)")
    ok3, err3 = call_spi(orig, SPIF_SENDCHANGE)
    print(f"         -> 返回 {ok3}  GetLastError={err3}")
    print(f"[回读] CursorBaseSize = "
          f"{read_dword(winreg.HKEY_CURRENT_USER, CURSOR_KEY, 'CursorBaseSize')!r}"
          "   (SPIF_SENDCHANGE 不写注册表)\n")

    # --- 探测 3: 连续 12 帧，测动画可行性 ---
    print("[探测 3] 连续 12 帧从大变到小，模拟 60fps 动画")
    t0 = time.perf_counter()
    fails = 0
    for i in range(12):
        px = round(target - (target - orig) * i / 11)
        okf, _ = call_spi(px, SPIF_SENDCHANGE)
        fails += 0 if okf else 1
        time.sleep(1 / 60)
    dt = time.perf_counter() - t0
    print(f"         -> 12 帧耗时 {dt*1000:.0f}ms，失败 {fails} 帧")
    ok4, _ = call_spi(orig, SPIF_SENDCHANGE)
    print(f"[恢复 3] -> 返回 {ok4}")
    print(f"[回读] CursorBaseSize = "
          f"{read_dword(winreg.HKEY_CURRENT_USER, CURSOR_KEY, 'CursorBaseSize')!r}")

    print("\n" + "=" * 62)
    print("判定")
    print("=" * 62)
    if ok and ok2:
        print("SPI 被系统识别 -> 方案成立")
        print("请回想刚才屏幕上指针是否真的变大过 1.2 秒：")
        print("  · 变大过        -> 完美，直接进入开发")
        print("  · 完全没变化    -> 需要移动鼠标才刷新，或需改用 SetSystemCursor 位图方案")
    else:
        print("SPI 返回失败 -> 本机不支持 0x2029，改用 SetSystemCursor 位图方案")


if __name__ == "__main__":
    base0 = read_dword(winreg.HKEY_CURRENT_USER, CURSOR_KEY, "CursorBaseSize")
    orig0 = base0 if isinstance(base0, int) and base0 > 0 else 32
    try:
        main()
    finally:
        ok, err = call_spi(orig0, SPIF_SENDCHANGE)
        print(f"\n[最终兜底恢复] 尺寸={orig0}  返回={ok}  err={err}")
        sys.exit(0)
