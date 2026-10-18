#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
在局域网内把网页版时钟跑起来，供 iPhone / iPad 直接访问（不需要公网、不需要 GitHub Pages）。

用法：
    python tools/serve-web.py            # 默认 8080 端口
    python tools/serve-web.py 9000       # 指定端口

启动后会打印形如 http://192.168.1.5:8080 的地址；
在手机 Safari 里打开该地址 → 分享 → 添加到主屏幕，即可当作全屏时钟使用。

注意：
  - 手机与电脑必须在同一个 Wi-Fi / 局域网内；
  - 局域网 http 不是「安全上下文」，因此离线缓存（Service Worker）不会启用，
    但「添加到主屏幕」与全屏独立显示依然可用；
  - 挂断脚本（Ctrl+C）后手机就打不开了；想随时可用建议用 GitHub Pages（见 README）。
"""

import http.server
import os
import socket
import socketserver
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "app")


def lan_ip():
    """拿到本机在局域网里的 IPv4（连一下外网地址但不真的发包）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def end_headers(self):
        # 开发时别让浏览器缓存住页面
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    if not os.path.isfile(os.path.join(WEB_DIR, "index.html")):
        sys.exit("找不到 %s" % os.path.join(WEB_DIR, "index.html"))

    ip = lan_ip()
    socketserver.TCPServer.allow_reuse_address = True
    try:
        httpd = socketserver.TCPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        sys.exit("端口 %d 无法监听：%s" % (port, e))

    print("=" * 56)
    print(" 翻页时钟 · 局域网服务已启动")
    print("=" * 56)
    print(" 目录   :", WEB_DIR)
    print(" 手机打开: http://%s:%d" % (ip, port))
    print(" 本机打开: http://127.0.0.1:%d" % port)
    print()
    print(" 手机上：Safari 打开上面地址 → 分享按钮 → 添加到主屏幕")
    print(" 按 Ctrl+C 结束")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
