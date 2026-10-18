# -*- coding: utf-8 -*-
r"""通知栏（托盘）图标 —— 纯标准库实现

为什么不用第三方库（pystray / infi.systray）
------------------------------------------
本项目除 tkinter 外零依赖，托盘这点事用 ctypes 也就一百来行：
Shell_NotifyIconW + 一个隐藏窗口收回调消息 + TrackPopupMenu 弹菜单。

线程模型
--------
托盘窗口活在**自己的线程**里（消息循环是阻塞的）。它绝不去碰 tkinter ——
所有用户动作（点击图标、菜单选择）只往 actions 队列里丢字符串，
主线程在 _tick 里轮询处理。和钩子线程同一条规矩：跨线程只传数据，不调界面。

    托盘线程   RegisterClassW + CreateWindowExW + Shell_NotifyIconW + 消息循环
    主线程     after() 轮询 actions 队列：show -> 显示窗口，quit -> 完整退出

图标
----
不加载 .ico 文件，CreateIcon 用 GDI 现画一枚 32x32 的白色箭头（就是光标
的样子，深色描边保证浅色任务栏上也看得清）。纯 Python 逐像素光栅化，
32x32 只有 1024 个像素，开销可以忽略。

回调消息的坑
------------
- Shell_NotifyIconW 的回调带的是**旧版语义**（不调 NIM_SETVERSION）：
  lParam = 鼠标消息（WM_LBUTTONUP 之类），wParam = 图标 ID。够用。
- explorer 崩溃重启后托盘图标会集体消失，系统会广播注册过的
  "TaskbarCreated" 消息 —— 收到就重加一次图标。
- 退出必须 NIM_DELETE，否则图标挂在通知栏直到鼠标扫过才消失。
  进程被强杀（os._exit / 任务管理器）时清理不到，这是平台限制。

用法
----
    actions = queue.Queue()
    tray = TrayIcon(actions)
    tray.start()          # 返回 False = 建失败（不致命，界面照常）
    ...
    tray.stop()           # 完整退出时调用，摘图标 + 结束线程
"""

import atexit
import ctypes
import os
import queue
import threading
from ctypes import wintypes

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

# ---- Shell_NotifyIconW ----
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_ubyte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                       ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL

# ---- 隐藏窗口 ----


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

# ---- 图标 ----

user32.CreateIcon.argtypes = [
    wintypes.HINSTANCE, ctypes.c_int, ctypes.c_int, ctypes.c_ubyte,
    ctypes.c_ubyte, ctypes.c_char_p, ctypes.c_char_p]
user32.CreateIcon.restype = wintypes.HICON
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL

# ---- 菜单 ----

user32.CreatePopupMenu.argtypes = []
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                               ctypes.c_size_t, wintypes.LPCWSTR]
user32.AppendMenuW.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.DestroyMenu.restype = wintypes.BOOL

# ---- 消息常量 ----
WM_NULL = 0x0000
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_APP_TRAY = 0x8000                       # WM_APP，托盘回调专用
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080

ID_SHOW, ID_QUIT = 1, 2

# 图标就是一枚光标箭头，坐标按 32x32 设计
_ARROW_POLY = [(11, 3), (11, 25), (16, 20), (19, 27), (22, 26), (19, 19),
               (26, 19)]
_ARROW_FILL = (246, 248, 251, 255)         # 白色箭身
_ARROW_EDGE = (30, 32, 38, 255)            # 深色描边，浅色任务栏上也看得清


def _point_in_poly(x, y, pts):
    """even-odd 规则的 point-in-polygon。"""
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y):
            t = (y - yi) / (yj - yi)
            if x < xi + t * (xj - xi):
                inside = not inside
        j = i
    return inside


def _dist_to_edges(x, y, pts):
    best = 1e9
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        dx, dy = x2 - x1, y2 - y1
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else ((x - x1) * dx + (y - y1) * dy) / l2
        t = max(0.0, min(1.0, t))
        px, py = x1 + t * dx, y1 + t * dy
        d = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
        if d < best:
            best = d
    return best


def icon_xor_bits(size=32):
    """图标的 XOR 位图（32bpp RGBA，逐行）。白色箭头 + 深色描边，其余透明。"""
    out = bytearray()
    for yy in range(size):
        for xx in range(size):
            cx, cy = xx + 0.5, yy + 0.5
            if _dist_to_edges(cx, cy, _ARROW_POLY) <= 1.4:
                out += bytes(_ARROW_EDGE)
            elif _point_in_poly(cx, cy, _ARROW_POLY):
                out += bytes(_ARROW_FILL)
            else:
                out += b"\x00\x00\x00\x00"
    return bytes(out)


def icon_and_bits(size=32):
    """单色 AND 掩码：全 0 = 每个像素都按 XOR 位图（含 alpha）绘制。

    行宽按 WORD 对齐（32px 正好 4 字节，无需 padding）。
    """
    stride = ((size + 15) // 16) * 2
    return bytes(stride * size)


class TrayIcon:
    """通知栏图标。用户动作以字符串形式丢进 actions 队列：show / quit。"""

    TIP = "指针缩放 —— 按下鼠标时光标缩小"

    def __init__(self, actions):
        if not isinstance(actions, queue.Queue):
            raise TypeError("actions 必须是 queue.Queue（跨线程只传数据）")
        self.actions = actions
        self._thread = None
        self._hwnd = None
        self._icon = None
        self._added = False
        self._ready = threading.Event()
        self._wndproc_ref = None       # WNDPROC 回调必须持强引用，否则被 GC
        self._cb_msg = WM_APP_TRAY
        self._taskbar_created = 0
        atexit.register(self._cleanup)

    # ------------------------------------------------------------ 生命周期

    def start(self):
        """起托盘线程。返回 False = 建失败（调用方降级为无托盘运行）。"""
        self._thread = threading.Thread(target=self._run, name="pz-tray",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(3.0)
        return self._added

    def stop(self, timeout=2.0):
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        self._cleanup()

    # ------------------------------------------------------------ 线程体

    def _run(self):
        try:
            wc = WNDCLASSW()
            wc.lpfnWndProc = WNDPROC(self._proc)
            self._wndproc_ref = wc.lpfnWndProc        # 防 GC，这条没商量
            wc.lpszClassName = "PZTrayWnd_%d" % os.getpid()
            wc.hInstance = kernel32.GetModuleHandleW(None)
            if not user32.RegisterClassW(ctypes.byref(wc)):
                return
            # explorer 重启后系统广播这条消息，收到要重新加图标
            self._taskbar_created = user32.RegisterWindowMessageW(
                "TaskbarCreated")
            self._hwnd = user32.CreateWindowExW(
                0, wc.lpszClassName, "指针缩放托盘", 0,
                0, 0, 0, 0, None, None, wc.hInstance, None)
            if not self._hwnd:
                return
            self._icon = user32.CreateIcon(
                None, 32, 32, 1, 32, icon_and_bits(), icon_xor_bits())
            self._add()
            self._ready.set()

            msg = wintypes.MSG()
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):        # WM_QUIT 或出错，都可能
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._cleanup()
            self._ready.set()             # 别让 start() 干等满 3 秒

    # ------------------------------------------------------------ 消息处理

    def _proc(self, hwnd, msg, wp, lp):
        # 和鼠标钩子同一个规矩：回调里绝不能让异常逃逸
        try:
            self._handle(hwnd, int(msg), int(wp), int(lp))
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _handle(self, hwnd, msg, wp, lp):
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
        elif msg == WM_DESTROY:
            self._cleanup()
            user32.PostQuitMessage(0)
        elif msg == self._cb_msg:
            if lp in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.actions.put("show")
            elif lp == WM_RBUTTONUP:
                self._show_menu(hwnd)
        elif msg == self._taskbar_created and self._added:
            self._add()                   # explorer 重启，图标丢了重加

    def _show_menu(self, hwnd):
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, MF_STRING, ID_SHOW, "显示主窗口")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, ID_QUIT, "退出")
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # 不先 SetForegroundWindow 的话，菜单不会在点别处时消失
        user32.SetForegroundWindow(hwnd)
        ret = user32.TrackPopupMenu(
            hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY,
            pt.x, pt.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(hmenu)
        if ret == ID_SHOW:
            self.actions.put("show")
        elif ret == ID_QUIT:
            self.actions.put("quit")

    # ------------------------------------------------------------ 图标增删

    def _add(self):
        if not self._hwnd:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self._cb_msg
        nid.hIcon = self._icon
        nid.szTip = self.TIP
        self._added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _cleanup(self):
        """摘图标。stop() / WM_DESTROY / atexit 都会走到，幂等。"""
        if self._added and self._hwnd:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._hwnd
            nid.uID = 1
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self._added = False
        if self._icon:
            user32.DestroyIcon(self._icon)
            self._icon = None
