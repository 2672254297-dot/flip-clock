#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
翻页时钟 · Win32 层：窗口 / DPI / 光标 / 空闲时间 / 进程。
"""

import os
import sys

if os.name != 'nt':
    sys.exit('本项目依赖 Windows API，仅支持 Windows。')

# 控制台里 Python 自带 Unicode 输出；被重定向到管道/文件时改成 UTF-8，避免中文乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        if not _stream.isatty():
            _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import ctypes
from ctypes import wintypes

from flipclock_common import PID_PATH, log

# ---------------------------------------------------------------- Win32
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
try:
    user32.SetProcessDPIAware()
except Exception:
    pass


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetCursor.argtypes = [ctypes.c_void_p]
user32.LoadCursorW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.LoadCursorW.restype = ctypes.c_void_p
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259


def pid_alive(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    code = wintypes.DWORD()
    ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return bool(ok) and code.value == STILL_ACTIVE


def read_pid():
    try:
        with open(PID_PATH, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except FileNotFoundError:
        return 0
    except (OSError, ValueError) as exc:
        log("读取 overlay.pid 失败（按无实例处理）:", repr(exc))
        return 0

HWND_TOPMOST = wintypes.HWND(-1)
SW_HIDE = 0
SW_SHOW = 5
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040

GWL_STYLE = -16
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
MONITOR_DEFAULTTONEAREST = 2
VK_MENU = 0x12
SW_RESTORE = 9


def alt_down():
    """任意一侧 Alt 是否按下（全局，不要求本进程有焦点）。"""
    try:
        return bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
    except Exception as exc:
        log("读取 Alt 状态失败（按未按下处理）:", repr(exc))
        return False


def idle_seconds():
    """系统级空闲秒数（鼠标或键盘最近一次输入到现在）。"""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    now = kernel32.GetTickCount()
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


def target_rect(mode):
    """返回 (x, y, w, h)：鼠标所在屏幕 / 主屏 / 全部屏幕。"""
    if mode == "all":
        return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
                user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
                user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
                user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    if mode == "primary":
        return (0, 0, user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN))

    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    mon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        r = mi.rcMonitor
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    return (0, 0, user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN))


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def find_windows_by_title(title):
    """标题完全等于 title 的所有顶层窗口。"""
    hits = []

    def cb(hwnd, _):
        if window_title(hwnd) == title:
            hits.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return hits


def find_windows_by_pids(pids):
    """按进程归属找 Chromium 顶层窗口（kiosk 模式标题为空时靠这个兜底）。"""
    if not pids:
        return []
    hits = []

    def cb(hwnd, _):
        if window_pid(hwnd) in pids:
            if window_class(hwnd) == "Chrome_WidgetWin_1":
                hits.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return hits


def strip_frame(hwnd):
    """去掉标题栏与边框，变成无边框窗口。"""
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    style &= ~(WS_CAPTION | WS_THICKFRAME | WS_BORDER | WS_DLGFRAME |
               WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
    style |= WS_POPUP
    user32.SetWindowLongW(hwnd, GWL_STYLE, style)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                        SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


def show_window(hwnd, rect):
    """把窗口摆到指定矩形上、置顶显示；返回系统实际生效的矩形。"""
    x, y, w, h = rect
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_SHOWWINDOW | SWP_NOACTIVATE)
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.BringWindowToTop(hwnd)
    if not user32.SetForegroundWindow(hwnd):
        try:
            user32.SwitchToThisWindow(hwnd, True)
        except Exception:
            pass
    got = RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(got)):
        return (got.left, got.top, got.right - got.left, got.bottom - got.top)
    return None


def dpi_aware_here():
    """当前线程切换到 Per-Monitor V2，保证 SetWindowPos / 显示器矩形都是物理像素。"""
    try:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def hide_window(hwnd):
    user32.ShowWindow(hwnd, SW_HIDE)


_cursor_hidden = False
IDC_ARROW = 32512
CURSOR_SHOWCURSOR_MAX = 64      # ShowCursor 计数调整的循环上限（防止计数异常时死循环）


def force_hide_cursor():
    """把系统光标显示计数压到负数，并立刻清掉当前指针形状。

    单次 ShowCursor(False) 只把计数减 1；别的程序（播放器、全屏应用）调用过
    ShowCursor 就会把计数抬上去，导致"隐藏了但箭头还在"。

    这里**每次都压到 <0**（而不是只在第一次压）：隐藏态期间如果又被别的程序抬高，
    下一次调用就能纠正回来；只压一次的话箭头会一直留着。
    最后用 SetCursor(NULL) 立刻生效（页面侧另有 cursor:none 兜底）。
    """
    global _cursor_hidden
    for _ in range(CURSOR_SHOWCURSOR_MAX):
        if user32.ShowCursor(False) < 0:
            break
    _cursor_hidden = True
    user32.SetCursor(None)


def set_cursor_visible(visible):
    global _cursor_hidden
    if visible:
        if _cursor_hidden:
            for _ in range(CURSOR_SHOWCURSOR_MAX):
                if user32.ShowCursor(True) >= 0:
                    break
            _cursor_hidden = False
        user32.SetCursor(user32.LoadCursorW(None, IDC_ARROW))
    else:
        force_hide_cursor()
