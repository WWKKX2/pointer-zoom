# -*- coding: utf-8 -*-
"""界面截图辅助 —— 建好窗口，把自己那块矩形截下来存成 PNG。

只捕获窗口自身的坐标范围，不会碰屏幕上其它内容。
不依赖 Pillow：GDI 抓图 + 标准库 zlib 手写 PNG。

    python test_gui_shot.py --seconds 12
"""

import argparse
import ctypes
import os
import struct
import sys
import time
import tkinter as tk
import zlib
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pointer_zoom as pz      # noqa: E402
import pointer_gui as pg       # noqa: E402

user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                            wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]

SRCCOPY = 0x00CC0020
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def capture_region(x, y, w, h, out_path):
    """抓屏幕上一块矩形并存成 PNG。"""
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY):
            raise ctypes.WinError(ctypes.get_last_error())

        info = BITMAPINFOHEADER()
        info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.biWidth = w
        info.biHeight = -h              # 负高度 = 自上而下，省得后面翻转
        info.biPlanes = 1
        info.biBitCount = 32
        info.biCompression = BI_RGB
        info.biSizeImage = w * h * 4

        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
        if got == 0:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)

    raw = bytes(buf)
    rows = bytearray()
    for row in range(h):
        rows.append(0)                                   # 每行的 filter byte
        line = raw[row * w * 4:(row + 1) * w * 4]
        for i in range(0, len(line), 4):
            rows.append(line[i + 2])                     # BGRA -> RGB
            rows.append(line[i + 1])
            rows.append(line[i])
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
           + _chunk(b"IEND", b""))
    with open(out_path, "wb") as fh:
        fh.write(png)
    return out_path


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--out", default=os.path.join(ROOT, "docs", "images",
                                                      "gui-preview.png"))
    args = parser.parse_args()

    cfg_path = args.config or os.path.join(ROOT, "pointer-zoom.ini")
    cfg = pz.load_config(cfg_path)

    root = tk.Tk()
    gui = pg.ZoomGui(root, cfg, cfg_path)
    try:
        for _ in range(40):
            root.update_idletasks()
            root.update()
            time.sleep(0.02)

        x, y = root.winfo_rootx(), root.winfo_rooty()
        w, h = root.winfo_width(), root.winfo_height()
        print(f"窗口 {w}x{h} @ ({x},{y})")
        capture_region(x, y, w, h, args.out)
        print(f"已保存 {args.out}")
        sys.stdout.flush()

        end = time.perf_counter() + args.seconds
        while time.perf_counter() < end:
            root.update()
            time.sleep(0.02)
    finally:
        gui.on_close()
        pz.apply_size(cfg["original_size"])
        print("已关闭并还原指针")


if __name__ == "__main__":
    main()
