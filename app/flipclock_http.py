#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
翻页时钟 · HTTP 层：本地控制接口（只服务本机页面）与端口工具。
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

import json
import os
import socket
import urllib.parse
from http.server import BaseHTTPRequestHandler

from flipclock_common import (ALLOWED_HOSTS, MAX_BODY, MAX_DRAIN_BYTES, ROOT,
                              STATIC_FILES, log)

# ---------------------------------------------------------------- 接口协议
# 本地控制接口的**单一数据源**：README 里的接口表照抄这里，
# selftest.py 也按这张表逐项断言（多一个未登记的路由就会被测出来）。
#   method : HTTP 方法
#   path   : 路径
#   auth   : "token" = 改状态，必须带 X-FlipClock-Token；"none" = 只读，仅校验 Host/Origin
#   body   : 请求体形态（仅说明用途，不参与校验）
#   desc   : 语义
API_CONTRACT = [
    {"method": "GET", "path": "/__state", "auth": "none", "body": "-",
     "desc": "全量状态：visible / alt / hold / cursor / manual / page / themeRev / idle / threshold"},
    {"method": "GET", "path": "/__config", "auth": "none", "body": "-",
     "desc": "当前配置（config.json 内容）"},
    {"method": "GET", "path": "/__theme", "auth": "none", "body": "-",
     "desc": "当前配色与版本号（themeRev 用于让页面判断要不要重新拉取）"},
    {"method": "GET", "path": "/__ping", "auth": "none", "body": "-",
     "desc": "存活探测（宿主用它识别端口上是否已有实例）"},
    {"method": "POST", "path": "/__hold", "auth": "token", "body": '{"hold": true}',
     "desc": "页面声明「保持显示」（设置面板使用中），宿主 3 秒超时"},
    {"method": "POST", "path": "/__ack", "auth": "token", "body": "-",
     "desc": "页面确认淡出动画已结束，宿主可以隐藏窗口了"},
    {"method": "POST", "path": "/__page", "auth": "token", "body": '{"manual":bool,"nocursor":bool,"panel":bool}',
     "desc": "页面回报自身 UI 状态（供日志与自测核对）"},
    {"method": "POST", "path": "/__exit", "auth": "token", "body": "-",
     "desc": "请求退出宿主（手动模式按 Esc / 关闭窗口）"},
    {"method": "POST", "path": "/__config", "auth": "token", "body": '{"enabled":bool,"idleSeconds":int,"monitor":str}',
     "desc": "修改配置；阈值会被夹到 [MIN_IDLE_SECONDS, MAX_IDLE_SECONDS]"},
    {"method": "POST", "path": "/__theme", "auth": "token", "body": '{"bg":"#RRGGBB","img":str,"mask":int,...}',
     "desc": "修改配色；非法取值被清洗忽略，img 超过 MAX_IMAGE_LEN 不落盘"},
]

# 只读的静态资源（不在上面协议表里，因为它们是页面本身）
STATIC_ROUTES = {"/", "/index.html"}

# 路由表：do_GET / do_POST 只做分发，新增接口必须同时登记进 API_CONTRACT
GET_ROUTES = {
    "/__state": "_get_state",
    "/__config": "_get_config",
    "/__theme": "_get_theme",
    "/__ping": "_get_ping",
}
POST_ROUTES = {
    "/__ack": "_post_ack",
    "/__page": "_post_page",
    "/__exit": "_post_exit",
    "/__hold": "_post_hold",
    "/__config": "_post_config",
    "/__theme": "_post_theme",
}


def contract_pairs():
    """协议表里声明的 (method, path) 集合。"""
    return {(r["method"], r["path"]) for r in API_CONTRACT}


def route_pairs():
    """代码里真实实现的 (method, path) 集合。"""
    return ({("GET", p) for p in GET_ROUTES} | {("POST", p) for p in POST_ROUTES})


def validate_contract():
    """接口表与真实路由必须完全一致（不多不少）。

    返回 (ok, detail)：新增接口忘了登记、或登记了没实现，都会在这里暴露。
    """
    declared, actual = contract_pairs(), route_pairs()
    detail = {
        "missing_in_contract": sorted("%s %s" % (m, p) for m, p in (actual - declared)),
        "undocumented_routes": sorted("%s %s" % (m, p) for m, p in (declared - actual)),
    }
    return (not detail["missing_in_contract"] and not detail["undocumented_routes"]), detail


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "FlipClockOverlay/1.0"
    app = None      # 由外部注入

    def log_message(self, *args):
        pass

    # ---- 安全校验：只服务本机页面，且只认自己的来源；改状态的 POST 还要 token ----
    def _request_host(self):
        host = self.headers.get("Host") or ""
        return host.rsplit(":", 1)[0].strip("[]").lower() if host else ""

    def _request_origin(self):
        return (self.headers.get("Origin") or "").rstrip("/").lower()

    def _own_origins(self):
        return {"http://127.0.0.1:%d" % self.app.port,
                "http://localhost:%d" % self.app.port}

    def _origin_allowed(self, origin=None):
        origin = self._request_origin() if origin is None else origin
        return (not origin) or (origin in self._own_origins())

    def _host_allowed(self):
        return self._request_host() in ALLOWED_HOSTS

    def _token_ok(self):
        return (self.headers.get("X-FlipClock-Token") or "") == self.app.token

    def _guard(self, need_token=False):
        """返回 True 表示放行；否则已经回过 403。"""
        if not self._host_allowed() or not self._origin_allowed():
            log("⛔ 拒绝非本机/跨站请求: Host=%r Origin=%r %s"
                % (self.headers.get("Host"), self.headers.get("Origin"), self.command))
            self._text(403, "forbidden")
            return False
        if need_token and not self._token_ok():
            log("⛔ 拒绝缺少/错误 token 的 %s" % self.command)
            self._text(403, "forbidden")
            return False
        return True

    # ---- 工具 ----
    def _cors(self):
        """只回显自己的来源；跨站请求一枚 CORS 头都不给（浏览器因此读不到内容）。"""
        origin = self._request_origin()
        if origin and origin in self._own_origins():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-FlipClock-Token")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code, msg):
        body = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        with open(path, "rb") as fh:
            body = fh.read()
        ctype = "text/html; charset=utf-8"
        if path.endswith(".png"):
            ctype = "image/png"
        elif path.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif path.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    # ---- 路由 ----
    def do_OPTIONS(self):
        if not self._guard():
            return
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    @staticmethod
    def _static_allowed(rel):
        """只允许 app/ 下的单个白名单文件名：不递归、不接受子目录/相对路径/隐藏文件。

        截图（previews/）与文档（docs/）都已移出 app/，所以不在可服务范围内。
        """
        if not rel or rel.startswith(".") or "/" in rel or "\\" in rel or ":" in rel:
            return False
        return rel in STATIC_FILES

    def _redirect_with_token(self, parsed):
        """页面没带 token 时补一个（浏览器地址栏直接打开也能用设置页）。"""
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                 if k != "t"]
        query.append(("t", self.app.token))
        location = (parsed.path or "/") + "?" + urllib.parse.urlencode(query)
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- 只读接口 ----
    def _get_state(self, parsed):
        return self._json(self.app.state())

    def _get_config(self, parsed):
        return self._json(self.app.config)

    def _get_theme(self, parsed):
        return self._json({"rev": self.app.theme_rev, "theme": self.app.theme})

    def _get_ping(self, parsed):
        return self._json({"ok": True})

    def do_GET(self):
        if not self._guard():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        handler = GET_ROUTES.get(path)
        if handler is not None:
            return getattr(self, handler)(parsed)
        if path in STATIC_ROUTES:
            token = urllib.parse.parse_qs(parsed.query).get("t", [""])[0]
            if token != self.app.token:
                return self._redirect_with_token(parsed)
            return self._file(os.path.join(ROOT, "index.html"))

        rel = urllib.parse.unquote(path).lstrip("/")
        if not self._static_allowed(rel):
            return self._text(404, "not found")
        return self._file(os.path.join(ROOT, rel))

    def _drain_body(self, length):
        """把超大的请求体读掉再回错误。

        直接「回 413 + 关连接」会让客户端在还在发数据时收到 RST
        （表现为 ConnectionResetError），所以这里分块丢弃、最多丢 MAX_DRAIN_BYTES。
        """
        remaining = min(length, MAX_DRAIN_BYTES)
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def _read_body(self):
        """读请求体；超过 MAX_BODY 就丢弃并回 413，避免被超大 Content-Length 打爆内存。

        返回 (body_bytes, ok)：ok=False 表示已经回过 413，调用方应立刻 return。
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            self._text(400, "bad content-length")
            return b"", False
        if length < 0:
            self._text(400, "bad content-length")
            return b"", False
        if length > MAX_BODY:
            log("⛔ 拒绝超大请求体: %d 字节（上限 %d）" % (length, MAX_BODY))
            self._drain_body(length)          # 先读完再回，避免客户端收到 RST
            self.close_connection = True
            self._text(413, "payload too large")
            return b"", False
        return (self.rfile.read(length) if length else b""), True

    @staticmethod
    def _json_body(raw):
        """尽力解析 JSON 对象；失败返回 None。"""
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _post_ack(self, raw):
        if not self.app.ack.is_set():
            log("  页面已确认淡出完成")
        self.app.ack.set()
        return self._json({"ok": True})

    def _post_page(self, raw):
        # 页面回报自身状态（手动模式 / 指针是否隐藏 / 面板是否打开 / 指针是否已到齿轮区），
        # 供日志与自测核对。gear 用来判断合成鼠标输入是否真的送达页面。
        info = self._json_body(raw) or {}
        self.app.page_state = {
            "manual": bool(info.get("manual")),
            "nocursor": bool(info.get("nocursor")),
            "panel": bool(info.get("panel")),
            "gear": bool(info.get("gear")),
        }
        return self._json({"ok": True})

    def _post_exit(self, raw):
        log("页面请求退出（Esc / 关闭）")
        self.app.exit_requested = True
        return self._json({"ok": True})

    def _post_hold(self, raw):
        # 页面（设置面板使用中）声明保持显示；带 3 秒超时，页面异常不会卡住屏保
        patch = self._json_body(raw) or {}
        return self._json({"hold": self.app.set_hold(bool(patch.get("hold")))})

    def _post_config(self, raw):
        patch = self._json_body(raw)
        if patch is None:
            return self._text(400, "bad json")
        self.app.patch_config(patch)
        return self._json(self.app.config)

    def _post_theme(self, raw):
        patch = self._json_body(raw)
        if patch is None:
            return self._text(400, "bad json")
        return self._json(self.app.patch_theme(patch))

    def do_POST(self):
        if not self._guard(need_token=True):
            return
        path = urllib.parse.urlparse(self.path).path
        handler = POST_ROUTES.get(path)
        if handler is None:
            return self._text(404, "not found")
        raw, ok = self._read_body()
        if not ok:
            return
        return getattr(self, handler)(raw)


def port_in_use(port):
    """Windows 上 SO_REUSEADDR 会让 bind 探测误判，所以用 connect 探测。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def server_alive(port):
    """端口上跑的是不是本程序的宿主。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4) as sock:
            sock.sendall(b"GET /__ping HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            return b'"ok"' in sock.recv(256)
    except Exception:
        return False


def pick_port(preferred):
    for port in [preferred] + list(range(preferred + 1, preferred + 12)):
        if not port_in_use(port):
            return port
    return 0
