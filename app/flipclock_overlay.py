#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
翻页时钟 · 空闲屏保宿主（Windows）—— 入口 / 状态机

工作方式：
  1. 用 user32.GetLastInputInfo 读取【系统级】空闲时间（鼠标或键盘任意输入都会重置）；
  2. 空闲达到阈值后，把一个无边框、全屏、置顶的 Edge/Chrome 窗口显示出来（里面就是 index.html）；
  3. 一旦检测到任何输入，通知页面淡出，然后隐藏窗口；
  4. 通过本地 HTTP 服务（默认 127.0.0.1:8756）给页面提供状态与控制接口。

模块划分（本文件只放状态机与 CLI）：
  flipclock_common   路径 / 常量 / 日志
  flipclock_win32    Win32 封装（窗口、DPI、光标、空闲、进程）
  flipclock_config   配置 / 主题 / runtime 读写与校验
  flipclock_browser  时钟浏览器进程的查找与清理
  flipclock_http     本地 HTTP 控制接口 + 端口工具

只用 Python 标准库，不需要 pip 安装任何东西。
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

from flipclock_common import (ALLOWED_HOSTS, BROWSER_CANDIDATES,
                              CONFIG_PATH, DEFAULT_CONFIG, DEFAULT_THEME,
                              ICON_TITLE_PREFIX, MAX_BODY, MAX_IDLE_SECONDS,
                              MAX_IMAGE_LEN, MIN_IDLE_SECONDS, PID_PATH, PROFILE_DIR,
                              PROJECT_ROOT, ROOT, RUNTIME_PATH, STATIC_FILES,
                              THEME_PATH, log)
from flipclock_win32 import (POINT, RECT, WNDENUMPROC, alt_down, dpi_aware_here,
                             find_windows_by_pids, find_windows_by_title,
                             hide_window, idle_seconds, pid_alive, read_pid,
                             set_cursor_visible, show_window, strip_frame, target_rect,
                             user32, window_class, window_pid, window_title)
from flipclock_config import (check_limits_consistent, drop_runtime, find_browser,
                              load_config, load_theme, read_runtime,
                              sanitize_theme_patch, save_config, save_runtime,
                              save_theme, seed_profile, write_json_atomic)
from flipclock_browser import kill_profile_browser, profile_browser_pids
from flipclock_http import (API_CONTRACT, GET_ROUTES, POST_ROUTES, Handler,
                            contract_pairs, pick_port, port_in_use, route_pairs,
                            server_alive, validate_contract)

if os.name != 'nt':
    sys.exit('本项目依赖 Windows API，仅支持 Windows。')

# 控制台里 Python 自带 Unicode 输出；被重定向到管道/文件时改成 UTF-8，避免中文乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        if not _stream.isatty():
            _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 供 selftest.py 等外部使用者通过 `import flipclock_overlay as fc` 继续访问的旧名字
__all__ = [
    "POINT", "RECT", "WNDENUMPROC", "alt_down", "dpi_aware_here", "find_windows_by_pids",
    "find_windows_by_title", "hide_window", "idle_seconds", "pid_alive", "read_pid",
    "set_cursor_visible", "show_window", "strip_frame", "target_rect", "user32",
    "window_class", "window_pid", "window_title", "load_config", "load_theme", "save_config",
    "save_theme", "save_runtime", "read_runtime", "drop_runtime", "sanitize_theme_patch",
    "check_limits_consistent", "find_browser", "seed_profile", "write_json_atomic",
    "kill_profile_browser", "profile_browser_pids", "Handler", "pick_port",
    "port_in_use", "server_alive", "App", "main",
    "API_CONTRACT", "GET_ROUTES", "POST_ROUTES", "contract_pairs", "route_pairs",
    "validate_contract",
    "CONFIG_PATH", "THEME_PATH", "PID_PATH", "RUNTIME_PATH", "PROFILE_DIR",
    "ROOT", "PROJECT_ROOT",
    "DEFAULT_CONFIG", "DEFAULT_THEME", "STATIC_FILES",
    "ALLOWED_HOSTS", "BROWSER_CANDIDATES", "ICON_TITLE_PREFIX", "MAX_BODY",
    "MAX_IMAGE_LEN", "MIN_IDLE_SECONDS", "MAX_IDLE_SECONDS", "log",
]


from flipclock_app import App


# ---------------------------------------------------------------- 入口
def parse_args(argv):
    ap = argparse.ArgumentParser(description="翻页时钟空闲屏保宿主")
    ap.add_argument("--idle-minutes", type=int, default=0, help="空闲多少分钟后显示（覆盖 config.json）")
    ap.add_argument("--idle-seconds", type=float, default=0,
                    help="按秒设置空闲阈值（临时覆盖，不写入 config.json）。测试常用：--idle-seconds 30")
    ap.add_argument("--manual", action="store_true",
                    help="手动模式：立即全屏显示时钟，不监听空闲、不自动退出（Esc 退出，鼠标动一下显示指针）")
    ap.add_argument("--monitor", choices=["cursor", "primary", "all"], default="", help="显示在哪块屏幕")
    ap.add_argument("--window-mode", choices=["kiosk", "app"], default="", help="kiosk=真无边框全屏（默认）；app=普通应用窗口")
    ap.add_argument("--keep-browser", dest="keep_browser", action="store_true", default=None, help="常驻预热浏览器（默认）")
    ap.add_argument("--on-demand", dest="keep_browser", action="store_false", help="按需启动：只在接近空闲阈值时拉起浏览器，退出即彻底结束")
    ap.add_argument("--port", type=int, default=0, help="本地服务端口（默认取 config.json）")
    ap.add_argument("--browser", default="", help="浏览器可执行文件路径")
    ap.add_argument("--no-browser", action="store_true", help="只跑服务，不启动时钟窗口")
    ap.add_argument("--dry-run", action="store_true", help="只跑状态机，不真正显示/隐藏窗口")
    ap.add_argument("--idle-source", default="",
                    help="测试专用：从文件读取空闲秒数（替代系统 GetLastInputInfo），"
                         "让自测不依赖「真的没人碰电脑」")
    ap.add_argument("--no-hide-cursor", action="store_true", help="显示时钟时不隐藏鼠标指针")
    ap.add_argument("--print-url", action="store_true", help="打印设置页地址后退出")
    ap.add_argument("--stop", action="store_true", help="停止正在运行的实例并清理时钟浏览器")
    return ap.parse_args(argv)


def cmd_print_url():
    """--print-url：优先用运行中的实例（含真实端口与本次运行的 token）。"""
    rt = read_runtime()
    cfg = load_config()
    alive = bool(rt.get("pid")) and pid_alive(int(rt["pid"]))
    port = int((rt.get("port") if alive else 0) or cfg.get("port", DEFAULT_CONFIG["port"]))
    token = str(rt.get("token") or "") if alive else ""
    print("http://127.0.0.1:%d/%s" % (port, ("?t=" + token) if token else ""))
    return 0


def cmd_stop():
    """--stop：结束宿主与时钟浏览器，并清理 pid / runtime 文件。"""
    stopped = False
    pid = read_pid()
    if pid and pid_alive(pid):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True, text=True, errors="replace", timeout=20)
        log("已结束宿主进程:", pid)
        stopped = True
    else:
        log("未发现正在运行的宿主进程")
    kill_profile_browser()
    for path in (PID_PATH, RUNTIME_PATH):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            log("删除 %s 失败:" % os.path.basename(path), repr(exc))
    return 0 if stopped else 1


def prepare_app(args):
    """校验单实例、挑端口、落盘 pid/runtime。返回 (app, 退出码)；退出码非 None 表示直接返回。"""
    running = read_pid()
    if running and pid_alive(running):
        log("已有实例正在运行（PID %d），如需重启请先运行 stop.bat" % running)
        return None, 3

    app = App(args)
    if server_alive(app.port):
        log("端口 %d 上已有翻页时钟宿主在运行，如需重启用 stop.bat" % app.port)
        return None, 3
    app.port = pick_port(app.port)
    if not app.port:
        log("找不到可用端口，退出")
        return None, 2
    if app.port != int(app.config.get("port", 0)):
        app.config["port"] = app.port
    save_config(app.config)          # 首次运行即生成 config.json，方便手动编辑
    check_limits_consistent()        # 上限配置自检（MAX_BODY 必须能装下合法图片）
    write_pid_file()
    save_runtime(app.port, app.token)     # 端口与本次运行的 token（settings.bat / 自测读它）
    log_startup_banner(app, args)
    return app, None


def serve_until_exit(app):
    """起 HTTP 服务与工作线程，直到页面请求退出 / Ctrl+C。"""
    Handler.app = app
    ThreadingHTTPServer.allow_reuse_address = False   # Windows 上避免端口被“共享”
    httpd = ThreadingHTTPServer(("127.0.0.1", app.port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    start_workers(app)
    try:
        while not app.exit_requested:
            time.sleep(0.2)
        log("页面请求退出 → 正在清理…")
    except KeyboardInterrupt:
        log("收到 Ctrl+C，正在退出…")
    finally:
        app.shutdown()
        httpd.shutdown()


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.print_url:
        return cmd_print_url()
    if args.stop:
        return cmd_stop()

    app, code = prepare_app(args)
    if code is not None:
        return code
    serve_until_exit(app)
    return 0


def write_pid_file():
    try:
        with open(PID_PATH, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError as exc:
        log("写入 overlay.pid 失败（不影响运行）:", repr(exc))


def log_startup_banner(app, args):
    log("=" * 56)
    log("翻页时钟" + ("（手动模式：立即全屏显示）" if app.manual else "屏保宿主已启动"))
    log("  设置页：http://127.0.0.1:%d/?t=%s" % (app.port, app.token))
    log("  安全：仅接受 127.0.0.1/localhost 的请求；改配置类接口需带本次运行的 token（设置页地址里已含）")
    if app.manual:
        log("  不监听空闲时间、不会自动跳出/自动退出：Esc 退出，鼠标动一下显示指针")
        log("  全屏期间自动隐藏鼠标指针（动一下鼠标临时显示）")
    else:
        log("  空闲阈值：%.0f 秒（%.1f 分钟）%s"
            % (app.threshold(), app.threshold() / 60.0,
               "  ← 命令行 --idle-seconds 临时覆盖（测试模式）" if args.idle_seconds else ""))
        log("  任意鼠标/键盘输入都会退出屏保；全屏期间自动隐藏鼠标指针")
    log("  显示屏幕：%s    窗口模式：%s    浏览器：%s    dry-run：%s"
        % (app.config["monitor"], app.config.get("windowMode", "kiosk"),
           "常驻预热" if app.config.get("keepBrowser", True) else "按需启动", args.dry_run))
    log("  退出：Ctrl+C，或运行 stop.bat")
    log("=" * 56)


def start_workers(app):
    """常驻预热模式：启动时先把浏览器拉起来；随后由监测线程接管。"""
    # 先占位再起线程，避免 watcher 与首次启动重复拉起浏览器
    if app.config.get("keepBrowser", True):
        app.launching = True
        app.last_launch = time.time()
        threading.Thread(target=app._launch_once, name="browser", daemon=True).start()
    else:
        app.launching = False
        app.last_launch = 0.0
    threading.Thread(target=app.watch, name="watch", daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
