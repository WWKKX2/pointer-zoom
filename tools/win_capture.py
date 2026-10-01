# -*- coding: utf-8 -*-
"""屏幕抓图的公用底层 —— GDI 抓图 + 标准库手写 PNG（不依赖 Pillow）。

    from win_capture import capture_bgra, save_png
    buf = capture_bgra(100, 100, 200, 200)
    save_png(buf, 200, 200, "shot.png")
"""

import ctypes
import struct
import zlib
from ctypes import wintypes

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


def capture_bgra(x, y, w, h):
    """抓屏幕上一块矩形，返回自上而下的 BGRA 原始字节。"""
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
        info.biHeight = -h                      # 负高度 = 自上而下
        info.biPlanes = 1
        info.biBitCount = 32
        info.biCompression = BI_RGB
        info.biSizeImage = w * h * 4
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0) == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes(buf)
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


# ---- 带光标版本 ----
# 注意：BitBlt 从屏幕 DC 抓到的画面**不包含光标**（Windows 把光标做在合成层，
# 不属于桌面 DC 的内容）。想测量光标真实渲染尺寸，必须用 DrawIconEx 自己画上去。

DI_NORMAL = 0x0003
CURSOR_SHOWING = 0x0001


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetCursorInfo.restype = wintypes.BOOL
user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                              wintypes.HICON, ctypes.c_int, ctypes.c_int,
                              wintypes.UINT, wintypes.HBRUSH, wintypes.UINT]
user32.DrawIconEx.restype = wintypes.BOOL


def get_cursor_info():
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    user32.GetCursorInfo(ctypes.byref(ci))
    return ci


def capture_with_cursor(x, y, w, h, draw_at=(20, 20)):
    """抓背景，并把当前系统光标用 DrawIconEx 画到 (draw_at) 处。

    返回 (BGRA 字节, 是否画上了光标)。
    """
    ci = get_cursor_info()
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    drawn = False
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, SRCCOPY):
            raise ctypes.WinError(ctypes.get_last_error())
        if ci.flags & CURSOR_SHOWING and ci.hCursor:
            drawn = bool(user32.DrawIconEx(mem, draw_at[0], draw_at[1],
                                           ci.hCursor, 0, 0, 0, None, DI_NORMAL))
        info = BITMAPINFOHEADER()
        info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.biWidth = w
        info.biHeight = -h
        info.biPlanes = 1
        info.biBitCount = 32
        info.biCompression = BI_RGB
        info.biSizeImage = w * h * 4
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0) == 0:
            raise ctypes.WinError(ctypes.get_last_error())
        return bytes(buf), drawn
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
def save_png(bgra, w, h, path):
    """把 capture_bgra 的结果写成 PNG（BGRA -> RGB）。"""
    rows = bytearray()
    for row in range(h):
        rows.append(0)                          # 每行的 filter byte
        line = bgra[row * w * 4:(row + 1) * w * 4]
        for i in range(0, len(line), 4):
            rows.append(line[i + 2])
            rows.append(line[i + 1])
            rows.append(line[i])
    png = (b"\x89PNG\r\n\x1a\n"
           + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
           + _chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return path
