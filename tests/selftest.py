#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
翻页时钟 · 端到端自测（Windows，仅用 Python 标准库）

它做的事，就是「从零把整套流程真跑一遍」：
  1. 停掉在跑的宿主 / 时钟浏览器，可选删除 .edge-profile（--fresh，冷启动路径）
  2. 以指定的空闲阈值启动宿主（默认 30 秒，方便测试）
  3. 等到空闲达标 → 断言窗口真的全屏 + 置顶 + 无白边（顺带截图，并检查像素）
  4. 移动鼠标 → 断言页面淡出、窗口隐藏、宿主继续运行
  5. 通过 /__config 把阈值改成 10 秒 → 再次跳出（验证设置写入链路）
  6. 按住 Alt → 点齿轮开设置面板 → 鼠标留在面板内（松开 Alt）→ 断言不退出
     鼠标移出面板 → 断言立即退出（宿主日志里能看到「保持显示 / 保持解除」）
  7. 关掉宿主，断言浏览器进程与端口都清理干净

用法：
    python selftest.py              # 全流程（默认 --idle 30）
    python selftest.py --idle 20    # 自定义阈值（秒）
    python selftest.py --warm       # 不删 .edge-profile（浏览器已就绪，更快）
    python selftest.py --no-shot    # 不截图
    python selftest.py --keep       # 测完不关宿主（方便你接手手动玩）

退出码：0 = 全部通过；1 = 有失败项；2 = 环境问题（找不到端口/浏览器等）。

注意：测试会真的让屏保全屏跳出来几秒，请先保存工作。
"""

import argparse
import ctypes
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.request
import zlib
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))        # tests/
PROJECT_ROOT = os.path.dirname(HERE)                    # 项目根
APP_DIR = os.path.join(PROJECT_ROOT, "app")             # 程序本体
ENTRY = os.path.join(APP_DIR, "flipclock_overlay.py")   # 宿主入口
sys.path.insert(0, APP_DIR)

import flipclock_overlay as fc          # noqa: E402  （复用宿主里的 Win32 封装与路径常量）

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
# 第 5 个参数是 ULONG_PTR（指针宽度），写成 DWORD 会 ArgumentError
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                               ctypes.POINTER(wintypes.ULONG)]
user32.keybd_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_MOVE = 0x0001
KEYEVENTF_KEYUP = 0x0002
VK_MENU = 0x12
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

RESULT = []          # (name, ok, detail)
# 自测产物统一放在 tests/（日志、截图、伪造空闲文件）
LOG_PATH = os.path.join(HERE, "selftest-host.log")
SHOT_FULL = os.path.join(HERE, "selftest-fullscreen.png")
SHOT_PANEL = os.path.join(HERE, "selftest-panel.png")
SHOT_MANUAL = os.path.join(HERE, "selftest-manual.png")
IDLE_FILE = os.path.join(HERE, "selftest-idle.txt")     # 测试专用：伪造系统空闲秒数


def set_idle(sec):
    """写伪造的空闲秒数（宿主用 --idle-source 读取，不再依赖真的没人碰电脑）。"""
    with open(IDLE_FILE, "w", encoding="utf-8") as fh:
        fh.write("%.1f" % sec)


def bump_idle(sec, ms=60):
    """把伪造空闲推到指定秒数，等宿主 tick 反应。"""
    set_idle(sec)
    time.sleep(ms / 1000.0)


def check(name, ok, detail=""):
    RESULT.append((name, bool(ok), detail))
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  — " + detail) if detail else ""), flush=True)
    return bool(ok)


def skip(name, detail=""):
    """环境原因无法验证（例如前台是高权限窗口，Windows 会丢弃本进程的合成输入）。"""
    RESULT.append((name, None, detail))
    print("  [SKIP] %s%s" % (name, ("  — " + detail) if detail else ""), flush=True)
    return False


def injection_works(port):
    """先确认合成键盘输入真的能送达（前台若是高完整性窗口，keybd_event 会被系统丢弃）。"""
    for _ in range(3):
        alt(True)
        time.sleep(0.35)
        ok = bool((state(port) or {}).get("alt"))
        alt(False)
        time.sleep(0.25)
        if ok:
            return True
    return False


# 齿轮中心相对视口右上角的 CSS 偏移：right/top = 14px 安全区 + 44px/2 = 36px
GEAR_INSET = 36


def mouse_injection_works(port, gx, gy):
    """确认合成鼠标移动能送达页面：把指针移到齿轮感应区，页面应回报 gear=True。

    与 injection_works（键盘）配对使用。鼠标与键盘可能一个通一个不通
    （例如用户正在同时操作鼠标，或前台窗口只吞掉鼠标消息），
    因此这里单独探测，避免把"输入没送到"误判成"点了没反应"。
    """
    for _ in range(3):
        move_to(gx, gy)
        time.sleep(0.35)
        if (state(port) or {}).get("page", {}).get("gear"):
            return True
    return False


def step(text):
    print("\n== " + text, flush=True)


# ----------------------------------------------------------------- 基础工具
def state(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/__state" % port, timeout=1.0) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def post(port, path, payload, timeout=2.0):
    # 宿主对改配置类接口要求带本次运行的 token（写在 runtime.json 里）
    headers = {"Content-Type": "application/json"}
    token = fc.read_runtime().get("token")
    if token:
        headers["X-FlipClock-Token"] = str(token)
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_until(fn, timeout, interval=0.2):
    end = time.time() + timeout
    while time.time() < end:
        val = fn()
        if val:
            return val
        time.sleep(interval)
    return None


def alt_free(port, timeout=6.0):
    """等外部把 Alt 松开（屏保按住 Alt 时按设计不退出、指针也保持可见）。"""
    return wait_until(lambda: not (state(port) or {}).get("alt", False), timeout, interval=0.3) is not None


def bring_cursor(x, y, dx=2):
    """真实移动光标（顺带让系统空闲时间归零）。"""
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_MOVE, dx, 0, 0, None)
    time.sleep(0.12)


def simulate_input():
    """模拟"用户回来了"：真实移动光标 + 把伪造空闲归零。"""
    p = fc.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    bring_cursor(p.x + 2, p.y + 2)
    set_idle(0)


def move_to(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)


def click_at(x, y):
    move_to(x, y)
    time.sleep(0.2)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    time.sleep(0.08)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
    time.sleep(0.35)


def alt(down):
    user32.keybd_event(VK_MENU, 0, 0 if down else KEYEVENTF_KEYUP, None)
    time.sleep(0.15)


def log_text():
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except Exception:
        return ""


def host_log_has(needle):
    return needle in log_text()


# ----------------------------------------------------------------- 截图（GDI → PNG）
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def grab_screen():
    """返回 (w, h, bgra_bytes，自上而下)。"""
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    hdc = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(memdc, bmp)
    gdi32.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, SRCCOPY)
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h                      # 负值 = 自上而下
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0                  # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(0, hdc)
    return w, h, buf.raw


def save_png(path, w, h, bgra):
    raw = bytearray(w * h * 3)
    mv = memoryview(bgra)
    raw[0::3] = mv[2::4]      # R
    raw[1::3] = mv[1::4]      # G
    raw[2::3] = mv[0::4]      # B
    rows = bytearray()
    stride = w * 3
    for y in range(h):
        rows.append(0)
        rows += raw[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)


def pixel_stats(w, h, bgra):
    """(顶部 3 行最亮值, 全屏亮像素数, 中心区域平均亮度)"""
    mv = memoryview(bgra)

    def lum_at(x, y):
        i = (y * w + x) * 4
        return (mv[i] * 114 + mv[i + 1] * 587 + mv[i + 2] * 299) // 1000

    top_max = 0
    for y in range(0, 3):
        for x in range(0, w, 3):
            top_max = max(top_max, lum_at(x, y))
    bright = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if lum_at(x, y) > 200:
                bright += 1
    tot = 0
    n = 0
    for y in range(int(h * 0.4), int(h * 0.6), 3):
        for x in range(int(w * 0.2), int(w * 0.8), 3):
            tot += lum_at(x, y)
            n += 1
    return top_max, bright, (tot / max(1, n))


# ----------------------------------------------------------------- 窗口
def clock_windows(pids):
    hits = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        if fc.window_class(hwnd) != "Chrome_WidgetWin_1":
            return True
        if pids and fc.window_pid(hwnd) not in pids:
            return True
        r = fc.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        hits.append((hwnd, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return True

    user32.EnumWindows(fc.WNDENUMPROC(cb), 0)
    return hits


def dpi_scale(hwnd):
    try:
        return user32.GetDpiForWindow(hwnd) / 96.0
    except Exception:
        return 1.25


# ----------------------------------------------------------------- 流程
def raw_http(port, path, method="GET", origin=None, token=None, host=None, body=None):
    """裸 socket 发请求：避免 HTTP 客户端把 //C:/... 这类 URL 归一化掉。"""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    req = "%s %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n" % (
        method, path, host or ("127.0.0.1:%d" % port))
    if origin:
        req += "Origin: %s\r\n" % origin
    if token is not None:
        req += "X-FlipClock-Token: %s\r\n" % token
    payload = (body or "").encode("utf-8")
    if method == "POST":
        req += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(payload)
    req += "\r\n"
    sock.sendall(req.encode("utf-8") + payload)
    data = b""
    while True:
        piece = sock.recv(4096)
        if not piece:
            break
        data += piece
    sock.close()
    text = data.decode("utf-8", "replace")
    return text.split("\r\n")[0], text


def security_phase(args):
    """第 8 步：安全回归 —— 报告里那三个严重项对应的攻击必须全部被拒。"""
    step("8. 安全回归：任意文件读取 / 跨站遥控 / profile 暴露")
    # 这一阶段会 POST 一个非法 theme 试探清洗逻辑（可能写到 theme.json），先备份
    theme_backup = None
    try:
        with open(fc.THEME_PATH, "r", encoding="utf-8") as fh:
            theme_backup = fh.read()
    except OSError:
        pass
    logfh = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
    set_idle(0)
    proc = subprocess.Popen([sys.executable, ENTRY,
                             "--no-browser", "--dry-run", "--idle-source", IDLE_FILE],
                            stdout=logfh, stderr=subprocess.STDOUT, cwd=APP_DIR,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        port = wait_runtime_for(proc)
        check("靶子宿主已就绪", bool(port), "port=%s" % port)
        if not port:
            return
        token = str(fc.read_runtime().get("token") or "")
        check("runtime.json 提供了本次运行的 token（设置页/自测据此鉴权）", len(token) >= 8)
        security_attacks(port)
        security_allowed(port, token)
    finally:
        subprocess.run([sys.executable, ENTRY, "--stop"],
                       capture_output=True, text=True, errors="replace", timeout=60)
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception as exc:
                print("   结束靶子宿主失败:", repr(exc))
        logfh.close()
        check("停止后 runtime.json 已清理", not os.path.isfile(fc.RUNTIME_PATH))
        if theme_backup is not None:
            try:
                with open(fc.THEME_PATH, "w", encoding="utf-8") as fh:
                    fh.write(theme_backup)
            except OSError as exc:
                print("   还原 theme.json 失败:", repr(exc))


def security_attacks(port):
    """攻击面：必须全部被拒。"""
    attacks = [
        ("绝对路径读取本机文件", "//C:/Windows/win.ini"),
        ("盘符路径读取本机文件", "/C:\\Windows\\win.ini"),
        ("相对路径穿越", "/../flipclock_overlay.py"),
        ("读取浏览器 profile", "/.edge-profile/Default/Preferences"),
        ("读取 config.json", "/config.json"),
    ]
    for name, path in attacks:
        head, _ = raw_http(port, path)
        check("拒绝：%s" % name, ("404" in head or "403" in head), head)

    head, body = raw_http(port, "/__exit", method="POST", origin="http://evil.example")
    check("拒绝：跨站 POST /__exit（CSRF）", "403" in head, head)
    check("跨站响应不带 CORS 放行头", "access-control-allow-origin" not in body.lower())

    head, _ = raw_http(port, "/__state", host="evil.example")
    check("拒绝：伪造 Host（DNS 重绑定）", "403" in head, head)
    head, _ = raw_http(port, "/__exit", method="POST")
    check("拒绝：无 token 的 POST /__exit", "403" in head, head)
    head, _ = raw_http(port, "/__config", method="POST", token="wrong-token",
                       body='{"enabled": false}')
    check("拒绝：错误 token 的 POST /__config", "403" in head, head)
    # img 超过 MAX_IMAGE_LEN：请求本身在 MAX_BODY 之内，所以是「200 但被清洗忽略」
    # （超体量走 413，见下面 security_allowed 的 MAX_BODY 用例）
    head, _ = raw_http(port, "/__theme", method="POST", token=str(fc.read_runtime().get("token") or ""),
                       body='{"img": "' + "x" * (fc.MAX_IMAGE_LEN + 10) + '"}')
    check("超长 img 被清洗忽略（200 但不落盘）", "200" in head, head)
    check("theme 里没有超长 img", len(str(fc.load_theme().get("img") or "")) <= fc.MAX_IMAGE_LEN,
          "img 长度=%d" % len(str(fc.load_theme().get("img") or "")))

    # 用「体量合法但取值非法」的请求验证清洗逻辑（img 用短值，只测类型/范围）
    head, _ = raw_http(port, "/__theme", method="POST", token=str(fc.read_runtime().get("token") or ""),
                       body='{"card": "not-a-color", "mask": 999, "bg": "rgb(1,2,3)"}')
    check("放行但清洗：非法 theme 取值被忽略", "200" in head, head)
    check("MAX_BODY 能装下合法图片（否则换背景图会被 413 挡掉）",
          fc.MAX_BODY > fc.MAX_IMAGE_LEN, "MAX_BODY=%d MAX_IMAGE_LEN=%d" % (fc.MAX_BODY, fc.MAX_IMAGE_LEN))
    # 真实尺寸的图片 dataURL 必须能提交成功（模拟设置页选本地图片）
    data_url = "data:image/png;base64," + ("A" * (256 * 1024))
    head, _ = raw_http(port, "/__theme", method="POST", token=str(fc.read_runtime().get("token") or ""),
                       body=json.dumps({"img": data_url}))
    check("256KB 的背景图片能正常提交（换图不被 413）", "200" in head, head)
    theme = fc.load_theme()
    check("theme 里的非法颜色未被写入", theme.get("card") != "not-a-color", "card=%s" % theme.get("card"))
    check("theme 里的 mask 被夹到 0..100", 0 <= int(theme.get("mask", 0)) <= 100, "mask=%s" % theme.get("mask"))
    check("theme 里超长的 img 未被写入", len(str(theme.get("img") or "")) <= fc.MAX_IMAGE_LEN,
          "img 长度=%d" % len(str(theme.get("img") or "")))


def security_allowed(port, token):
    """正常链路：必须仍然可用。"""
    head, _ = raw_http(port, "/__state")
    check("放行：GET /__state", "200" in head, head)
    head, _ = raw_http(port, "/__hold", method="POST", token=token, body='{"hold":true}')
    check("放行：带 token POST /__hold", "200" in head, head)
    head, _ = raw_http(port, "/?t=" + token)
    check("放行：页面（带 token）", "200" in head, head)
    head, _ = raw_http(port, "/")
    check("无 token 打开页面 → 302 自动补 token", "302" in head, head)
    # 整理后截图已移出服务根（app/），因此不再对外提供 —— 这是刻意的收窄
    head, _ = raw_http(port, "/preview-default.png")
    check("拒绝：截图不在服务根内（已移出 app/）", "404" in head, head)
    head, _ = raw_http(port, "/selftest-fullscreen.png")
    check("拒绝：自测截图同样不可访问", "404" in head, head)
    head, _ = raw_http(port, "/index.html?t=" + token, origin="http://127.0.0.1:%d" % port)
    check("同源页面可加载（带 token）", "200" in head, head)
    _, body = raw_http(port, "/__state", origin="http://127.0.0.1:%d" % port)
    check("同源请求的 CORS 只回显自身来源（不是通配 *）",
          "access-control-allow-origin: http://127.0.0.1:%d" % port in body.lower().replace("\r", ""))
    _, body = raw_http(port, "/__state", origin="http://evil.example")
    check("跨站读 /__state 不返回数据也不放行 CORS",
          "403" in body and "access-control-allow-origin" not in body.lower())
    # 超大请求体：必须被 413 拒绝（中等-2）
    head, _ = raw_http(port, "/__theme", method="POST", token=token,
                       body="x" * (fc.MAX_BODY + 1024))
    check("拒绝：超过 MAX_BODY 的请求体（413）", "413" in head, head)


def wait_runtime_for(proc, timeout=30):
    """等 runtime.json 属于【这个】进程再返回端口。

    坑：上一个宿主退出时可能还没来得及删 runtime.json，
    直接 read_runtime() 会拿到**旧实例的 token**，于是后续带 token 的请求全变 403。
    所以必须比对 pid。
    """
    end = time.time() + timeout
    while time.time() < end:
        rt = fc.read_runtime()
        if rt.get("port") and proc is not None and int(rt.get("pid") or 0) == proc.pid:
            return int(rt["port"])
        time.sleep(0.2)
    return None


def manual_cursor_checks(args, port):
    """手动模式：指针「停 3 秒隐藏 / 动一下显示 / 再自动隐藏」，且鼠标移动不退出。"""
    bump_idle(5)                     # 伪造「3 秒没输入」
    check("无输入满 3 秒 → 指针自动隐藏（cursor=False）",
          bool(wait_until(lambda: (state(port) or {}).get("cursor") is False, 3)))
    simulate_input()                # 动一下鼠标：指针出现，但时钟不退出
    check("动一下鼠标 → 指针临时显示（cursor=True）",
          bool(wait_until(lambda: (state(port) or {}).get("cursor") is True, 2)))
    time.sleep(1.0)
    check("手动模式下鼠标移动不会退出（visible 仍为 True）", bool((state(port) or {}).get("visible")))
    bump_idle(5)                     # 再伪造「3 秒没输入」→ 指针应再次自动隐藏
    check("停止操作后指针再次自动隐藏",
          bool(wait_until(lambda: (state(port) or {}).get("cursor") is False, 3)))
    check("页面再次隐藏指针（html.nocursor）",
          bool(wait_until(lambda: (state(port) or {}).get("page", {}).get("nocursor"), 2)))
    if not args.no_shot:
        sw, sh, buf = grab_screen()
        save_png(SHOT_MANUAL, sw, sh, buf)
        top_max, bright, _ = pixel_stats(sw, sh, buf)
        check("手动模式同样无白边 + 数字已渲染", top_max < 40 and bright > 1500,
              "top_max=%d bright=%d" % (top_max, bright))
        print("   截图: %s" % SHOT_MANUAL)


def manual_phase(args):
    """第 7 步：手动模式（等价 clock.bat）——立即显示、不自动退出、指针自动显隐、Esc 退出。"""
    step("7. 手动模式（--manual / clock.bat）：立即显示 · 不自动退出 · 指针自动显隐 · Esc 退出")
    logfh = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
    set_idle(0)
    proc = subprocess.Popen([sys.executable, ENTRY, "--manual",
                             "--idle-source", IDLE_FILE],
                            stdout=logfh, stderr=subprocess.STDOUT, cwd=APP_DIR,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        t0 = time.time()
        port = wait_runtime_for(proc, timeout=30)
        shown = wait_until(lambda: (state(port or 0) or {}).get("visible"), 45) if port else None
        check("手动模式：启动后立即全屏显示（%.1fs，不等空闲阈值）" % (time.time() - t0), bool(shown),
              "port=%s" % port)
        if not shown or not port:
            return
        st = state(port)
        check("宿主上报 manual=True（页面据此进入手动模式）", bool(st.get("manual")))
        check("页面进入手动模式（html.manual）",
              bool(wait_until(lambda: (state(port) or {}).get("page", {}).get("manual"), 3)))
        wins = clock_windows(fc.profile_browser_pids())
        rect_s = ("%dx%d @ (%d,%d)" % (wins[0][1][2], wins[0][1][3], wins[0][1][0], wins[0][1][1])) if wins else "无窗口"
        check("手动模式窗口全屏", bool(wins) and wins[0][1][2] >= 1900 and wins[0][1][3] >= 1000, rect_s)
        manual_cursor_checks(args, port)
        post(port, "/__exit", {})                    # 等价于按 Esc
        check("Esc(/__exit) → 宿主退出", bool(wait_until(lambda: state(port) is None, 15)))
        check("时钟浏览器进程已清理", not fc.profile_browser_pids())
        check("端口 %d 已释放" % port, not fc.port_in_use(port))
        check("日志确认手动模式标记", host_log_has("手动模式"))
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception as exc:
                print("   结束手动模式宿主失败:", repr(exc))
        logfh.close()


def parse_args(argv):
    ap = argparse.ArgumentParser(description="翻页时钟端到端自测")
    ap.add_argument("--idle", type=float, default=30.0, help="第一次跳出的空闲阈值（秒，默认 30）")
    ap.add_argument("--idle2", type=float, default=10.0, help="第二次跳出的阈值（秒，默认 10，走 /__config 写入）")
    ap.add_argument("--fresh", dest="fresh", action="store_true", default=True, help="删除 .edge-profile（默认）")
    ap.add_argument("--warm", dest="fresh", action="store_false", help="保留 .edge-profile，启动更快")
    ap.add_argument("--no-shot", action="store_true", help="不截图")
    ap.add_argument("--keep", action="store_true", help="测完不关宿主")
    return ap.parse_args(argv)


def phase_reset(args):
    """0. 清理旧进程与运行数据。返回 (port, config.json 备份)。"""
    step("0. 清理旧进程与运行数据")
    # 第 4 步会通过 /__config 改阈值（会写 config.json），先备份，收尾时还原
    try:
        with open(fc.CONFIG_PATH, "r", encoding="utf-8") as fh:
            config_backup = fh.read()
    except OSError:
        config_backup = None
    subprocess.run([sys.executable, ENTRY, "--stop"],
                   capture_output=True, text=True, errors="replace", timeout=60)
    fc.kill_profile_browser(quiet=True)
    for path in (LOG_PATH, IDLE_FILE, fc.RUNTIME_PATH):
        try:
            os.remove(path)
        except OSError:
            pass
    if args.fresh:
        import shutil
        shutil.rmtree(fc.PROFILE_DIR, ignore_errors=True)
    print("   .edge-profile: %s" % ("已删除（冷启动）" if args.fresh else "保留（热启动）"))
    print("   残留时钟浏览器进程: %d" % len(fc.profile_browser_pids()))

    cfg = fc.load_config()
    port = int(cfg.get("port", 8756))
    check("端口 %d 空闲" % port, not fc.port_in_use(port))
    return port, config_backup


def phase_start_host(args, port, logfh):
    """1. 启动宿主并确认服务就绪。返回 (proc, ok)。"""
    step("1. 启动宿主（--idle-seconds %.0f）" % args.idle)
    t0 = time.time()
    # --idle-source：空闲时间由测试文件决定，不依赖"真的没人碰电脑"
    # （否则你一边用电脑一边跑自测，空闲永远归零，第一步就永远不跳出）
    set_idle(0)
    proc = subprocess.Popen([sys.executable, ENTRY,
                             "--idle-seconds", str(args.idle),
                             "--idle-source", IDLE_FILE],
                            stdout=logfh, stderr=subprocess.STDOUT, cwd=APP_DIR,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    st = wait_until(lambda: state(port), 30)
    check("宿主服务已就绪 (%.1fs)" % (time.time() - t0), bool(st), "/__state ok")
    if not st:
        print(log_text())
        return proc, False
    check("阈值生效 = %.0f 秒" % args.idle, abs(float(st["threshold"]) - args.idle) < 0.5,
          "threshold=%s" % st["threshold"])
    return proc, True


def phase_idle_trigger(args, port):
    """2. 空闲达标 → 全屏跳出，并核对全屏/置顶/无白边/指针隐藏。"""
    step("2. 空闲达标，自动全屏跳出")
    bump_idle(args.idle + 1)
    shown = wait_until(lambda: (state(port) or {}).get("visible"), 60)
    check("空闲达到 %.0f 秒后自动显示" % args.idle, bool(shown))
    if not shown:
        return False
    pids = fc.profile_browser_pids()
    wins = clock_windows(pids)
    check("时钟窗口存在（class=Chrome_WidgetWin_1）", bool(wins), "共 %d 个" % len(wins))
    if not wins:
        return True
    hwnd, rect = wins[0]
    x, y, w, h = rect
    check("窗口铺满屏幕", (x, y) == (0, 0) and w >= 1900 and h >= 1000, "%dx%d @ (%d,%d)" % (w, h, x, y))
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    check("窗口置顶 (WS_EX_TOPMOST)", bool(ex & WS_EX_TOPMOST), "exstyle=0x%X" % (ex & 0xFFFFFFFF))
    token = str(fc.read_runtime().get("token") or "")
    title = fc.window_title(hwnd)
    check("窗口标题不含 token（同机进程枚举不到鉴权凭证）",
          bool(token) and token not in title, "title=%r" % title)
    time.sleep(0.8)
    if alt_free(port, 6):
        check("全屏后指针自动隐藏（cursor=False）", (state(port) or {}).get("cursor") is False)
        check("页面同步隐藏指针（html.nocursor）",
              bool(wait_until(lambda: (state(port) or {}).get("page", {}).get("nocursor"), 2)))
    else:
        skip("全屏后指针自动隐藏 + 页面 html.nocursor",
             "外部一直按着 Alt（按设计此时指针保持可见、屏保不退出）")
    if not args.no_shot:
        sw, sh, buf = grab_screen()
        save_png(SHOT_FULL, sw, sh, buf)
        top_max, bright, mid = pixel_stats(sw, sh, buf)
        check("无标题栏/白边（顶部 3 行全黑）", top_max < 40, "top_max=%d" % top_max)
        check("数字已渲染（亮像素 > 1500）", bright > 1500, "bright=%d 中心亮度=%.1f" % (bright, mid))
        print("   截图: %s" % SHOT_FULL)
    return True


def phase_exit(port):
    """3. 有输入 → 淡出并隐藏，且宿主继续监测。"""
    step("3. 有输入 → 淡出并隐藏")
    if not alt_free(port, 8):
        skip("检测到输入后窗口隐藏 + 页面确认淡出 + 指针恢复",
             "外部一直按着 Alt：按设计「Alt 按住期间不退出、指针保持可见」，无法验证退出链路")
    else:
        simulate_input()
        gone = wait_until(lambda: (state(port) or {}).get("visible") is False, 12)
        if not gone and (state(port) or {}).get("alt"):
            skip("检测到输入后窗口隐藏 + 页面确认淡出 + 指针恢复",
                 "验证途中外部又按下了 Alt（屏保按设计不退出）")
        else:
            check("检测到输入后窗口隐藏", bool(gone))
            acked = wait_until(lambda: host_log_has("页面已确认淡出完成"), 4, interval=0.1)
            check("页面确认淡出完成后才隐藏窗口（不硬切）", bool(acked))
            # 注意：visible=False 在 hide() 开头就置位，指针要等淡出 ack 之后才恢复，
            # 所以这里必须等，不能立刻断言
            check("退出后指针恢复显示（cursor=True）",
                  bool(wait_until(lambda: (state(port) or {}).get("cursor") is True, 3)))
            check("页面同步恢复指针（去掉 html.nocursor）",
                  bool(wait_until(lambda: not (state(port) or {}).get("page", {}).get("nocursor", True), 2)))
    check("宿主仍在运行（继续监测）", state(port) is not None)
    check("时钟浏览器仍存活（常驻预热模式）", bool(fc.profile_browser_pids()))


def phase_reconfig(args, port):
    """4. 通过 /__config 改阈值 → 再次跳出。返回是否成功显示。"""
    step("4. 用 /__config 把阈值改成 %.0f 秒 → 再次跳出" % args.idle2)
    post(port, "/__config", {"idleSeconds": int(args.idle2)})
    st = state(port)
    check("新阈值已写入（并取消命令行临时覆盖）", abs(float(st["threshold"]) - args.idle2) < 0.5,
          "threshold=%s" % st["threshold"])
    check("宿主日志提示已取消 --idle-seconds 覆盖", host_log_has("已取消命令行 --idle-seconds"))
    bump_idle(args.idle2 + 1)
    shown2 = wait_until(lambda: (state(port) or {}).get("visible"), 40)
    check("按新阈值再次跳出", bool(shown2))
    return bool(shown2)


def open_panel_with_alt(port, x, y, css_w, s):
    """按住 Alt → 点齿轮开面板 → 光标停进面板。返回 (是否打开, 面板内命中点)。"""
    alt(True)
    time.sleep(0.4)
    check("按住 Alt 时宿主上报 alt=True", bool((state(port) or {}).get("alt")))

    # 齿轮中心：CSS 里是 right/top = 14px + 44px/2 = 36px（含安全区变量，桌面为 0）
    gx, gy = x + (css_w - GEAR_INSET) * s, y + GEAR_INSET * s

    # 先确认合成鼠标输入真的能送达页面：把指针移到齿轮感应区，页面应回报 gear=True。
    # 送不到（前台窗口权限更高、或用户正在用鼠标）时按环境问题跳过，而不是误报产品缺陷。
    if not mouse_injection_works(port, gx, gy):
        skip("面板保持（Alt 开设置 / 面板内不退出 / 移出即退出）",
             "合成鼠标输入未能送达页面（页面未回报 gear=True）；"
             "请让时钟窗口获得焦点、不要同时操作鼠标，再重跑 `python selftest.py`")
        return False, (x + (css_w - 180) * s, y + 200 * s)

    inside = (x + (css_w - 180) * s, y + 200 * s)
    # 注意：再点一次齿轮是"关"面板，所以只在页面回报 panel=False 时才补点
    opened = False
    attempt = 0
    for attempt in range(3):
        if not (state(port) or {}).get("page", {}).get("panel"):
            click_at(gx, gy)
            time.sleep(0.6)
        move_to(inside[0], inside[1])          # 光标停进面板
        if wait_until(lambda: (state(port) or {}).get("hold"), 1.5):
            opened = True
            break
    check("点齿轮 → 面板打开且光标在内 → hold=True（无需 Alt）", opened, "尝试 %d 次" % (attempt + 1))
    check("页面回报面板已打开（panel=True）",
          bool((state(port) or {}).get("page", {}).get("panel")))
    return opened, inside


def phase_panel(args, port, shown2):
    """5. Alt 打开设置 → 鼠标留在面板内不退出 → 移出面板立即退出。"""
    step("5. 面板保持：Alt 打开设置 → 鼠标留在面板内 → 松开 Alt 也不退出")
    wins = clock_windows(fc.profile_browser_pids())
    if not shown2 or not wins:
        check("面板保持测试前置条件（窗口存在）", False)
        return
    if not injection_works(port):
        # 前台是高完整性窗口时 Windows 会丢弃本进程的 keybd_event/mouse_event，
        # 这时"点齿轮"根本送不到页面——属于环境问题，不算产品失败
        skip("面板保持（Alt 开设置 / 面板内不退出 / 移出即退出）",
             "前台窗口权限更高，系统丢弃了本进程的合成键鼠输入；"
             "请让桌面获得焦点后重跑 `python selftest.py`（其余项不受影响）")
        return

    hwnd, (x, y, w, h) = wins[0]
    s = dpi_scale(hwnd)
    css_w, css_h = w / s, h / s
    opened, inside = open_panel_with_alt(port, x, y, css_w, s)
    if not args.no_shot:
        sw, sh, buf = grab_screen()
        save_png(SHOT_PANEL, sw, sh, buf)
        print("   截图: %s" % SHOT_PANEL)

    check("鼠标在面板内 → 宿主 hold=True（无需 Alt）", bool(opened), "命中点 %s" % (inside,))

    alt(False)                       # 松开 Alt：接下来全靠面板保持
    time.sleep(0.4)
    check("Alt 已松开（alt=False）", not (state(port) or {}).get("alt"))
    if not opened:
        return
    # 面板内再动一下 + 伪造"刚有输入"：因为 hold=true，不该退出
    user32.SetCursorPos(int(inside[0] + 3), int(inside[1] + 3))
    simulate_input()
    time.sleep(1.0)
    check("松开 Alt 后在面板内移动 → 不退出", bool((state(port) or {}).get("visible")))
    check("宿主日志记录「设置面板使用中」的保持状态", host_log_has("设置面板使用中"))

    out = (x + css_w * 0.3 * s, y + css_h * 0.5 * s)   # 移出面板
    bring_cursor(out[0], out[1])
    set_idle(0)                                          # 并且"刚有输入"
    gone3 = wait_until(lambda: (state(port) or {}).get("visible") is False, 12)
    check("鼠标移出面板 → 立即退出屏保", bool(gone3))
    check("宿主日志出现「保持解除」", host_log_has("保持解除"))


def phase_stop(args, port):
    """6. 停止宿主 → 进程与端口清理。"""
    step("6. 停止宿主 → 进程与端口清理")
    time.sleep(1.5)          # 等最后一次淡出的 ack 落盘，再关宿主
    if args.keep:
        print("   --keep：保留宿主运行中")
        return
    subprocess.run([sys.executable, ENTRY, "--stop"],
                   capture_output=True, text=True, errors="replace", timeout=60)
    time.sleep(1.0)
    check("宿主已退出", state(port) is None)
    check("时钟浏览器进程已清理", not fc.profile_browser_pids())
    check("端口 %d 已释放" % port, not fc.port_in_use(port))
    check(".edge-profile 保留（下次启动更快）", os.path.isdir(fc.PROFILE_DIR))


def robustness_limits(port, token):
    """阈值夹取 + 非法输入处理（中等-3/5）。"""
    # ---- 中等-5：命令行 --idle-seconds 3（低于下限）→ 夹到 MIN ----
    got = float((state(port) or {}).get("threshold") or 0)
    check("命令行阈值低于下限 → 夹到 %.0f 秒" % fc.MIN_IDLE_SECONDS,
          abs(got - fc.MIN_IDLE_SECONDS) < 0.5, "threshold=%.1f" % got)

    # ---- 中等-5：设置页写入的阈值同样夹取 ----
    post(port, "/__config", {"idleSeconds": 5})          # 低于下限
    got = float((state(port) or {}).get("threshold") or 0)
    check("设置页阈值低于下限 → 夹到 %.0f 秒" % fc.MIN_IDLE_SECONDS,
          abs(got - fc.MIN_IDLE_SECONDS) < 0.5, "threshold=%.1f" % got)
    post(port, "/__config", {"idleSeconds": 999999})     # 超过上限
    got = float((state(port) or {}).get("threshold") or 0)
    check("设置页阈值超过上限 → 夹到 %.0f 秒" % fc.MAX_IDLE_SECONDS,
          abs(got - fc.MAX_IDLE_SECONDS) < 0.5, "threshold=%.1f" % got)
    check("MIN/MAX 常量成对且合理",
          fc.MIN_IDLE_SECONDS >= 1 and fc.MAX_IDLE_SECONDS > fc.MIN_IDLE_SECONDS,
          "%d..%d" % (fc.MIN_IDLE_SECONDS, fc.MAX_IDLE_SECONDS))

    # ---- 中等-3：非法输入不写坏配置，也不 500 ----
    head, _ = raw_http(port, "/__config", method="POST", token=token,
                       body='{"idleSeconds": "abc", "monitor": "bogus"}')
    check("非法 idleSeconds 被忽略（不报 500）", "200" in head, head)
    head, _ = raw_http(port, "/__config", method="POST", token=token, body="{not json")
    check("坏 JSON → 400", "400" in head, head)
    head, _ = raw_http(port, "/__hold", method="POST", token=token, body="[]")
    check("JSON 类型不对 → 按空对象处理（200）", "200" in head, head)

    # ---- 中等-3：config.json 始终是完整可解析的 JSON（原子写） ----
    ok_json = True
    detail = ""
    try:
        with open(fc.CONFIG_PATH, "r", encoding="utf-8") as fh:
            json.load(fh)
    except Exception as exc:
        ok_json = False
        detail = repr(exc)
    check("config.json 始终是完整 JSON（原子写）", ok_json, detail)
    leftovers = [f for f in os.listdir(APP_DIR) if f.endswith(".json.tmp")]
    check("原子写不留 .tmp 残渣", not leftovers, "残留=%s" % leftovers)


def phase_robustness(args):
    """9. 健壮性回归（中等项）：阈值夹取、配置原子写、非法 JSON、不阻塞监测。

    用 --dry-run 起一个不弹窗的宿主，避免打扰桌面。
    """
    step("9. 健壮性回归：阈值夹取 / 原子写 / 非法输入 / 不阻塞监测")
    logfh = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
    set_idle(0)
    # 用 3 秒启动（低于 MIN_IDLE_SECONDS）：验证命令行阈值也被夹取
    proc = subprocess.Popen([sys.executable, ENTRY,
                             "--no-browser", "--dry-run", "--idle-source", IDLE_FILE,
                             "--idle-seconds", "3"],
                            stdout=logfh, stderr=subprocess.STDOUT, cwd=APP_DIR,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        port = wait_runtime_for(proc)
        check("靶子宿主已就绪", bool(port), "port=%s" % port)
        if not port:
            return
        token = str(fc.read_runtime().get("token") or "")
        robustness_limits(port, token)

        # ---- 中等-1：退出请求不应被清理工作阻塞 ----
        t0 = time.time()
        post(port, "/__exit", {})
        gone = wait_until(lambda: state(port) is None, 15)
        elapsed = time.time() - t0
        check("请求退出后宿主正常结束（未卡在清理上）", bool(gone), "%.1fs" % elapsed)
    finally:
        subprocess.run([sys.executable, ENTRY, "--stop"],
                       capture_output=True, text=True, errors="replace", timeout=60)
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception as exc:
                print("   结束靶子宿主失败:", repr(exc))
        logfh.close()


def summarize():
    passed = sum(1 for _, ok, _ in RESULT if ok)
    failed = [r for r in RESULT if r[1] is False]
    skipped = [r for r in RESULT if r[1] is None]
    print("\n" + "=" * 60)
    print("自测结果：%d 通过 / %d 失败 / %d 跳过（共 %d 项）"
          % (passed, len(failed), len(skipped), len(RESULT)))
    for name, ok, detail in RESULT:
        if ok is False:
            print("  FAIL: %s%s" % (name, ("  — " + detail) if detail else ""))
    for name, ok, detail in RESULT:
        if ok is None:
            print("  SKIP: %s%s" % (name, ("  — " + detail) if detail else ""))
    print("=" * 60)
    if not host_log_has("Traceback"):
        for line in log_text().strip().splitlines()[-6:]:
            print("  日志 | " + line)
    return 0 if not failed else 1


def phase_code_health(args):
    """10. 代码结构回归（轻微项）：模块可导入、文件不过长、页面常量/白名单在位。

    不启宿主，纯静态检查，跑得很快。
    """
    step("10. 结构回归：模块拆分 / 文件长度 / 页面常量与白名单")

    # ---- 轻微-2：入口向后兼容（selftest 与外部脚本都靠 import flipclock_overlay） ----
    for name in ("CONFIG_PATH", "THEME_PATH", "RUNTIME_PATH", "PROFILE_DIR", "MAX_BODY",
                 "MAX_IMAGE_LEN", "MIN_IDLE_SECONDS", "MAX_IDLE_SECONDS", "POINT", "RECT",
                 "WNDENUMPROC", "kill_profile_browser", "load_config", "load_theme",
                 "read_runtime", "port_in_use", "profile_browser_pids", "window_class",
                 "window_pid", "App", "Handler", "main"):
        if not hasattr(fc, name):
            check("入口再导出 %s" % name, False)
            return
    check("入口再导出旧名字（向后兼容）", True, "共 22 个抽查项")

    declared = [n for n in getattr(fc, "__all__", []) if not hasattr(fc, n)]
    check("__all__ 里声明的名字都真实存在", not declared, "缺失=%s" % declared)

    modules = ["flipclock_common.py", "flipclock_win32.py", "flipclock_config.py",
               "flipclock_browser.py", "flipclock_http.py", "flipclock_app.py",
               "flipclock_overlay.py"]
    too_long = []
    for mod in modules:
        path = os.path.join(APP_DIR, mod)
        if not os.path.isfile(path):
            too_long.append("%s 不存在" % mod)
            continue
        with open(path, "r", encoding="utf-8") as fh:
            n = sum(1 for _ in fh)
        if n > 500:
            too_long.append("%s=%d 行" % (mod, n))
    check("拆出的每个模块都 ≤ 500 行（轻微-2）", not too_long, ", ".join(too_long) or "全部达标")

    # ---- 轻微-3：页面侧时间常量集中在文件头 ----
    with open(os.path.join(APP_DIR, "index.html"), "r", encoding="utf-8") as fh:
        page = fh.read()
    page_consts = ["FADE_IN_MS", "FADE_OUT_MS", "ACK_FALLBACK_MS", "POLL_INTERVAL_MS",
                   "POLL_RETRY_MS", "CONFIG_POLL_MS", "CONFIG_RETRY_MS", "HOLD_RENEW_MS",
                   "CURSOR_HIDE_DELAY_MS", "SECOND_SLACK_MS"]
    missing_consts = [c for c in page_consts if ("var %s =" % c) not in page]
    check("页面时间常量已集中在文件头（轻微-3）", not missing_consts, "缺失=%s" % missing_consts)

    # ---- 轻微-4：背景图协议白名单 ----
    check("页面有背景图协议白名单 safeImgUrl（轻微-4）", "function safeImgUrl(" in page)
    check("applyTheme 经由 safeImgUrl 取值", "var img = safeImgUrl(theme.img);" in page)

    # ---- 轻微-5：启动脚本不再硬编码端口 / 都有 python 检查 ----
    for script in ("settings.bat", "stop.bat", "clean.bat", "start.bat",
                   "clock.bat", "test-30s.bat"):
        path = os.path.join(PROJECT_ROOT, script) if script != "test-30s.bat" \
            else os.path.join(HERE, script)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        check("%s 检查 python 是否可用（轻微-5）" % script, "where python" in body)
        check("%s 不硬编码 127.0.0.1 端口（轻微-5）" % script,
              "127.0.0.1:8756" not in body)

    # ---- 整理后的目录结构（文件外只留启动项/结束项/指南） ----
    for d in ("app", "docs", "previews", "tests"):
        check("存在目录 %s/" % d, os.path.isdir(os.path.join(PROJECT_ROOT, d)))
    stray_py = [f for f in os.listdir(PROJECT_ROOT)
                if f.endswith(".py") or (f.endswith(".html") and f != "index.html")]
    check("项目根不再散落 .py / .html（都进了 app/ 或 tests/）", not stray_py,
          "残留=%s" % stray_py)
    check("页面在 app/index.html（服务根就是 app/）",
          os.path.isfile(os.path.join(APP_DIR, "index.html")))
    check("截图已移出服务根（preview 不在 app/）",
          not any(f.startswith("preview-") for f in os.listdir(APP_DIR)))
    # 入口脚本必须指向 app/，否则移动文件后全部启动项都会失效
    for script in ("start.bat", "stop.bat", "clean.bat", "settings.bat", "clock.bat"):
        path = os.path.join(PROJECT_ROOT, script)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        check("%s 指向 app\\flipclock_overlay.py" % script, "app\\flipclock_overlay.py" in body)
    for script in ("start-silent.vbs", "clock-silent.vbs"):
        path = os.path.join(PROJECT_ROOT, script)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            body = fh.read()
        check("%s 指向 app\\flipclock_overlay.py" % script, "\\app\\flipclock_overlay.py" in body)
    with open(os.path.join(HERE, "test-30s.bat"), "r", encoding="utf-8", errors="replace") as fh:
        check("tests\\test-30s.bat 指向 ..\\app\\flipclock_overlay.py",
              "..\\app\\flipclock_overlay.py" in fh.read())

    # ---- 建议 3：窗口标题里不得出现 token（会被同机进程枚举到） ----
    check("页面窗口标题用非敏感 WINDOW_TAG（不是 token）",
          "document.title = 'FlipClockOverlay-' + WINDOW_TAG;" in page
          and "document.title = 'FlipClockOverlay-' + TOKEN;" not in page)


def phase_ios_web(args):
    """12. iOS / 网页版（PWA）回归：静态检查，确保「添加到主屏幕」所需资产齐备且配置正确。

    不启宿主、不开浏览器，纯文件检查，跑得很快。
    """
    step("12. iOS / 网页版回归：PWA 资产、iOS 适配、无空闲跳出")

    with open(os.path.join(APP_DIR, "index.html"), "r", encoding="utf-8") as fh:
        page = fh.read()

    # ---- iOS 全屏与安全区 ----
    check("viewport 含 viewport-fit=cover（否则刘海/横条会压住内容）",
          "viewport-fit=cover" in page)
    check("声明 apple-mobile-web-app-capable（添加到主屏幕后全屏）",
          'name="apple-mobile-web-app-capable" content="yes"' in page)
    check("声明 apple-mobile-web-app-status-bar-style",
          "apple-mobile-web-app-status-bar-style" in page)
    check("声明 apple-mobile-web-app-title（主屏幕显示名）",
          "apple-mobile-web-app-title" in page)
    check("使用 safe-area-inset 变量避开刘海/底部横条",
          "env(safe-area-inset-top" in page and "env(safe-area-inset-bottom" in page)
    check("使用动态视口单位 dvh/dvw（iOS 地址栏收放时尺寸不跳）",
          "100dvh" in page and "100dvw" in page)
    check("禁止页面被拖动/橡皮筋滚动", "overscroll-behavior: none" in page)
    check("禁用长按选择与双击缩放",
          "-webkit-touch-callout: none" in page and "touch-action: manipulation" in page)
    check("尊重系统「减弱动态效果」", "prefers-reduced-motion" in page)

    # ---- 卡片宽度自适应（竖屏不溢出） ----
    check("卡片宽度按可用宽/高取小值（不再用固定 clamp 下限）",
          "--cw: min(" in page)
    check("卡片宽度公式按 6 卡 + 2 冒号 + 7 间距推导", "/ 7.36)" in page)

    # ---- PWA 资产 ----
    manifest_path = os.path.join(APP_DIR, "manifest.webmanifest")
    check("存在 manifest.webmanifest", os.path.isfile(manifest_path))
    manifest = {}
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as fh:
            try:
                manifest = json.load(fh)
            except ValueError as e:
                check("manifest.webmanifest 是合法 JSON", False, str(e))
    check("manifest 含 name/short_name", bool(manifest.get("name")) and bool(manifest.get("short_name")))
    check("manifest display=standalone（独立窗口，像原生 App）",
          manifest.get("display") == "standalone")
    check("manifest start_url 为相对路径（可部署在任意子目录）",
          str(manifest.get("start_url", "")).startswith("./"))
    check("manifest 背景/主题色为纯黑", manifest.get("background_color") == "#000000"
          and manifest.get("theme_color") == "#000000")

    icons = manifest.get("icons") or []
    check("manifest 声明 3 个图标（192/512/maskable）", len(icons) == 3,
          "实际 %d 个" % len(icons))
    missing_icons = [i.get("src") for i in icons
                     if not os.path.isfile(os.path.join(APP_DIR, str(i.get("src"))))]
    check("manifest 里的图标文件都真实存在", not missing_icons, "缺失=%s" % missing_icons)
    check("有 maskable 图标（Android 自适应）",
          any(i.get("purpose") == "maskable" for i in icons))

    for name, size in (("apple-touch-icon.png", 180), ("icon-192.png", 192),
                       ("icon-512.png", 512), ("icon-maskable-512.png", 512)):
        path = os.path.join(APP_DIR, name)
        if not os.path.isfile(path):
            check("图标 %s 存在" % name, False)
            continue
        with open(path, "rb") as fh:
            head = fh.read(24)
        # PNG 头 + IHDR 宽高（大端）
        ok_png = head[:8] == b"\x89PNG\r\n\x1a\n"
        got_w = int.from_bytes(head[16:20], "big") if ok_png and len(head) >= 24 else 0
        got_h = int.from_bytes(head[20:24], "big") if ok_png and len(head) >= 24 else 0
        check("图标 %s 是 %dx%d PNG" % (name, size, size),
              ok_png and got_w == size and got_h == size,
              "实际 %dx%d" % (got_w, got_h))

    check("页面声明 apple-touch-icon", 'rel="apple-touch-icon"' in page)
    check("页面声明 manifest", 'rel="manifest"' in page)
    check("页面声明 theme-color", 'name="theme-color"' in page)

    # ---- Service Worker（离线可用） ----
    sw_path = os.path.join(APP_DIR, "sw.js")
    check("存在 sw.js", os.path.isfile(sw_path))
    if os.path.isfile(sw_path):
        with open(sw_path, "r", encoding="utf-8") as fh:
            sw = fh.read()
        check("sw.js 预缓存核心资源", "PRECACHE" in sw and "index.html" in sw)
        check("sw.js 不缓存本机控制接口（/__state 等）", "indexOf('__')" in sw)
        check("sw.js 导航请求走「网络优先」（在线时能拿到最新页面）",
              "req.mode === 'navigate'" in sw)
    check("页面只在非本机站点注册 Service Worker（本机宿主不被缓存）",
          "!IS_LOCAL_PAGE" in page and "serviceWorker.register" in page)

    # ---- 网页版无「空闲跳出」 ----
    check("页面区分 host / web 两种运行形态", "WEB_MODE" in page and "IS_LOCAL_PAGE" in page)
    check("网页版进入纯钟模式时隐藏空闲设置",
          "function enterStandaloneMode(" in page and "idleSection.hidden = true" in page)
    check("页面标题可作主屏幕名称", "<title>翻页时钟</title>" in page)

    # ---- 局域网服务脚本 ----
    serve = os.path.join(PROJECT_ROOT, "tools", "serve-web.py")
    check("存在 tools/serve-web.py（局域网免公网使用）", os.path.isfile(serve))
    if os.path.isfile(serve):
        with open(serve, "r", encoding="utf-8") as fh:
            body = fh.read()
        check("serve-web.py 以 app/ 为静态根", 'os.path.join(ROOT, "app")' in body)

    # ---- 图标生成器可复现 ----
    maker = os.path.join(PROJECT_ROOT, "tools", "make-icons.py")
    check("存在 tools/make-icons.py（图标可复现生成）", os.path.isfile(maker))

    # ---- GitHub Pages 工作流 ----
    wf = os.path.join(PROJECT_ROOT, ".github", "workflows", "deploy-pages.yml")
    check("存在 Pages 部署工作流", os.path.isfile(wf))
    if os.path.isfile(wf):
        with open(wf, "r", encoding="utf-8") as fh:
            y = fh.read()
        check("工作流只发布网页版所需文件（不发布 Python 源码）",
              "cp app/index.html" in y and "cp app/manifest.webmanifest" in y
              and "cp app/sw.js" in y and "flipclock_overlay.py" not in y)
        check("工作流不含 Tab（YAML 语法）", "\t" not in y)


def phase_api_contract(args):
    """11. 接口协议回归（改进建议 3）：路由表必须与 API_CONTRACT 一致，且逐项可调用。"""
    step("11. 接口协议回归：路由表与协议表一致 + 每项按表可用")

    ok, detail = fc.validate_contract()
    check("路由表与 API_CONTRACT 完全一致（不多不少）", ok,
          "未登记=%s 未实现=%s" % (detail["missing_in_contract"], detail["undocumented_routes"]))
    check("协议表覆盖全部 10 个控制接口", len(fc.API_CONTRACT) == 10,
          "实际 %d 条" % len(fc.API_CONTRACT))
    check("协议表每条都有 method/path/auth/body/desc",
          all({"method", "path", "auth", "body", "desc"} <= set(r) for r in fc.API_CONTRACT))

    logfh = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
    set_idle(0)
    proc = subprocess.Popen([sys.executable, ENTRY,
                             "--no-browser", "--dry-run", "--idle-source", IDLE_FILE],
                            stdout=logfh, stderr=subprocess.STDOUT, cwd=APP_DIR,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        port = wait_runtime_for(proc)
        check("靶子宿主已就绪", bool(port), "port=%s" % port)
        if not port:
            return
        token = str(fc.read_runtime().get("token") or "")
        for route in fc.API_CONTRACT:
            method, path, auth = route["method"], route["path"], route["auth"]
            label = "%s %s" % (method, path)
            if auth == "token":
                # 不带 token：必须 403（协议表说它需要鉴权）
                head, _ = raw_http(port, path, method=method)
                check("协议表：%s 需要 token → 无 token 403" % label, "403" in head, head)
                # 带 token：不应再是 403/404（业务状态码可以不同）
                body = route["body"] if route["body"] != "-" else ""
                head, _ = raw_http(port, path, method=method, token=token, body=body)
                check("协议表：%s 带 token 可用" % label,
                      "403" not in head and "404" not in head, head)
            else:
                head, _ = raw_http(port, path, method=method)
                check("协议表：%s 只读可访问（200）" % label, "200" in head, head)
    finally:
        subprocess.run([sys.executable, ENTRY, "--stop"],
                       capture_output=True, text=True, errors="replace", timeout=60)
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception as exc:
                print("   结束靶子宿主失败:", repr(exc))
        logfh.close()


def restore_env(config_backup, proc, logfh, args):
    """收尾：停掉自测起的宿主、还原 config.json、删掉伪造空闲文件。"""
    if logfh:
        logfh.close()
    if proc and proc.poll() is None and not args.keep:
        try:
            proc.terminate()
        except Exception as exc:
            print("   结束自测宿主失败:", repr(exc))
    if config_backup is not None:
        try:
            with open(fc.CONFIG_PATH, "r", encoding="utf-8") as fh:
                now = fh.read()
            if now != config_backup:
                with open(fc.CONFIG_PATH, "w", encoding="utf-8") as fh:
                    fh.write(config_backup)
                print("   已还原 config.json（自测临时改过阈值）")
        except OSError as exc:
            print("   还原 config.json 失败:", repr(exc))
    try:
        os.remove(IDLE_FILE)
    except OSError:
        pass


def main(argv=None):
    args = parse_args(argv)
    proc = None
    logfh = None
    config_backup = None
    try:
        port, config_backup = phase_reset(args)
        logfh = open(LOG_PATH, "w", encoding="utf-8", errors="replace")
        proc, ok = phase_start_host(args, port, logfh)
        if not ok:
            return 2

        shown = phase_idle_trigger(args, port)
        phase_exit(port)
        shown2 = phase_reconfig(args, port)
        phase_panel(args, port, shown2)
        phase_stop(args, port)

        # ---- 7/8/9. 手动模式、安全回归、健壮性（同一端口，须在宿主停掉之后跑） ----
        if args.keep:
            print("   --keep：跳动手动模式 / 安全回归 / 健壮性回归（同一端口不能起两个宿主）")
        else:
            manual_phase(args)
            security_phase(args)
            phase_robustness(args)
        phase_code_health(args)
        phase_ios_web(args)
        if not args.keep:
            phase_api_contract(args)
        return summarize()
    finally:
        restore_env(config_backup, proc, logfh, args)


if __name__ == "__main__":
    sys.exit(main())
