#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
翻页时钟 · 公共层：路径、常量、日志。
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

import re
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))          # app/：程序本体与运行时文件都在这
PROJECT_ROOT = os.path.dirname(ROOT)                       # 项目根：启动脚本、文档、截图在这
CONFIG_PATH = os.path.join(ROOT, "config.json")
THEME_PATH = os.path.join(ROOT, "theme.json")
PID_PATH = os.path.join(ROOT, "overlay.pid")
RUNTIME_PATH = os.path.join(ROOT, "runtime.json")     # {pid, port, token}，供 settings.bat / 自测读取
PROFILE_DIR = os.path.join(ROOT, ".edge-profile")
ICON_TITLE_PREFIX = "FlipClockOverlay-"

# ---- 安全：本地 HTTP 服务只做这几件事，其余一律拒绝 ----
# 静态文件白名单：只允许「app/ 下的单个文件名」，不递归、不允许子目录
# （这样 config.json / theme.json / .edge-profile/ 下的浏览器数据都不会被服务出去；
#   截图与文档已移出 app/，因此也不在可服务范围内）
STATIC_FILES = frozenset(["index.html", "favicon.ico"])
ALLOWED_HOSTS = frozenset(["127.0.0.1", "localhost", "::1"])

DEFAULT_CONFIG = {
    "enabled": True,
    "idleSeconds": 600,        # 空闲阈值（秒）。>0 时以它为准，方便把测试阈值调成 30 秒
    "idleMinutes": 10,         # 旧字段，仅当 idleSeconds<=0 时作为回退（分钟）
    "monitor": "cursor",       # cursor | primary | all
    "port": 8756,
    "browser": "",
    "windowMode": "kiosk",     # kiosk（真·无边框全屏）| app（带标题栏的普通应用窗口）
    "keepBrowser": True,       # True = 常驻预热（跳出 0 延迟）；False = 按需启动
    "prewarmSeconds": 20,      # keepBrowser=False 时，提前多少秒启动浏览器
}

DEFAULT_THEME = {
    "bg": "#000000",
    "img": "",
    "mask": 30,
    "card": "#232a33",
    "digit": "#ffffff",
}

# ---- 取值范围与上限（配置/主题来自 HTTP 接口，必须校验后再落盘） ----
MAX_IMAGE_LEN = 2 * 1024 * 1024  # 背景图片 dataURL/URL 长度上限
# 请求体上限必须**大于**图片上限：设置页选本地图片时会把整张图以 dataURL POST 过来，
# 卡太紧会让"换背景图"直接 413。这里留 4MB 给 base64 膨胀与 JSON 包装的余量。
MAX_BODY = 4 * 1024 * 1024
# 超过 MAX_BODY 时最多再丢弃这么多字节（读完再回 413，避免客户端收到 TCP RST）
MAX_DRAIN_BYTES = 64 * 1024 * 1024
MIN_IDLE_SECONDS = 10            # 与设置页滑杆下限一致
MAX_IDLE_SECONDS = 86400         # 24 小时
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
INPUT_IDLE_EPS_S = 0.7           # 空闲归零到「确认有输入」的判定窗口（秒）
HOLD_TTL_S = 3.0                 # 页面「保持显示」的续期超时（秒）
ACK_TIMEOUT_S = 1.2              # 等页面淡出 ack 的最长时间（秒）
MANUAL_CURSOR_IDLE_S = 3.0       # 手动模式：鼠标停多久后重新隐藏指针（秒）
LAUNCH_RETRY_S = 10.0            # 两次拉起浏览器的间隔（秒）

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# ---------------------------------------------------------------- 日志
_log_lock = threading.Lock()


def log(*parts):
    stamp = time.strftime("%H:%M:%S")
    with _log_lock:
        print("[%s] %s" % (stamp, " ".join(str(p) for p in parts)), flush=True)
