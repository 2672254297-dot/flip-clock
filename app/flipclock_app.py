#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
翻页时钟 · 状态机（App）

职责：空闲判定、显示/隐藏、保持显示（Alt / 面板）、指针显隐、浏览器生命周期。
"""

import os
import subprocess
import sys
import threading
import time

from flipclock_common import ( ACK_TIMEOUT_S, HOLD_TTL_S, ICON_TITLE_PREFIX,
                               INPUT_IDLE_EPS_S, LAUNCH_RETRY_S, MANUAL_CURSOR_IDLE_S,
                               MAX_IDLE_SECONDS, MIN_IDLE_SECONDS, PID_PATH, PROFILE_DIR,
                               log,)
from flipclock_win32 import ( alt_down, dpi_aware_here, find_windows_by_pids,
                              find_windows_by_title, hide_window, idle_seconds,
                              set_cursor_visible, show_window, strip_frame, target_rect,
                              user32,)
from flipclock_config import ( find_browser, load_config, load_theme, sanitize_theme_patch,
                               save_config, save_theme, seed_profile,)
from flipclock_browser import ( kill_profile_browser, profile_browser_pids,)

if os.name != 'nt':
    sys.exit('本项目依赖 Windows API，仅支持 Windows。')

# ---------------------------------------------------------------- 宿主
class App(object):
    def __init__(self, args):
        self.args = args
        self.config = load_config()
        if args.idle_minutes:
            self.config["idleMinutes"] = args.idle_minutes
            self.config["idleSeconds"] = 0        # 命令行显式给了分钟，就不要再被秒覆盖
        if args.monitor:
            self.config["monitor"] = args.monitor
        if args.window_mode:
            self.config["windowMode"] = args.window_mode
        if args.keep_browser is not None:
            self.config["keepBrowser"] = bool(args.keep_browser)
        if args.browser:
            self.config["browser"] = args.browser

        self.lock = threading.RLock()
        self.visible = False
        self.rev = 0
        self.ack = threading.Event()
        self.stop_event = threading.Event()
        self.hwnd = None
        self.proc = None
        self.launching = False
        self.last_launch = 0.0
        self._last_probe = 0.0
        self._idle_src_log = 0.0
        self._pending_log = 0.0
        self._hold_active = False
        self._hold_source = ""
        self._releasing = False    # 是否已有一轮浏览器清理在跑（避免重复起线程）
        self.hold_until = 0.0      # 页面声明的「保持显示」有效期（设置面板使用中）
        self.manual = bool(getattr(args, "manual", False))
        self.cursor_shown = True
        self.exit_requested = False
        self.idle_source = getattr(args, "idle_source", "") or ""
        self.page_state = {}       # 页面回报：manual / nocursor / panel
        if self.manual:
            self.hold_until = time.time() + HOLD_TTL_S   # 手动模式：一上来就保持显示，不会误退
        self.theme = load_theme()
        self.theme_rev = 1
        self.token = "%08x" % int(time.time() * 1000 % 0xFFFFFFFF)
        # 窗口标识：只用于让宿主认出自己的窗口（会写进窗口标题，所以不能是 token）
        self.window_tag = os.urandom(3).hex()
        self.port = args.port or self.config["port"]

    # ---- 阈值 ----
    def threshold(self):
        """空闲阈值（秒）。统一夹到 [MIN_IDLE_SECONDS, MAX_IDLE_SECONDS]：
        传 0 或负数会让 `idle >= threshold` 恒真 → 显示/隐藏反复抖动。"""
        secs = 0.0
        if self.args.idle_seconds:
            secs = float(self.args.idle_seconds)
        else:
            secs = float(self.config.get("idleSeconds") or 0)
            if secs <= 0:
                secs = float(self.config.get("idleMinutes") or 10) * 60.0
        return max(float(MIN_IDLE_SECONDS), min(float(MAX_IDLE_SECONDS), secs))

    # ---- 「保持显示」（页面声明，带超时，页面卡死也不会永久卡住） ----
    def holding(self):
        return time.time() < self.hold_until

    def set_hold(self, on):
        if on:
            self.hold_until = time.time() + HOLD_TTL_S   # 页面每秒续期一次
        else:
            self.hold_until = 0.0
        return self.holding()

    # ---- 空闲时间 ----
    def idle(self):
        """系统级空闲秒数；--idle-source 时改为读文件（测试专用，保证可复现）。"""
        if self.idle_source:
            try:
                with open(self.idle_source, "r", encoding="utf-8") as fh:
                    return max(0.0, float(fh.read().strip() or 0))
            except (OSError, ValueError) as exc:
                # 测试注入文件读不到/内容坏了：当作「刚有输入」，宁可不出屏保也别乱跳
                if time.time() - self._idle_src_log > 5:
                    self._idle_src_log = time.time()
                    log("读取 --idle-source 失败（按空闲 0 处理）:", repr(exc))
                return 0.0
        return idle_seconds()

    # ---- 鼠标指针：全屏显示时一律藏起来 ----
    def cursor_should_show(self, idle):
        """手动模式：动一下鼠标显示指针、停 3 秒自动隐藏；屏保模式：仅在「保持显示」时显示。"""
        if self.args.no_hide_cursor:
            return True
        if self.manual:
            return idle < MANUAL_CURSOR_IDLE_S
        return self._hold_active

    def set_cursor(self, show):
        if show == self.cursor_shown:
            return
        self.cursor_shown = show
        set_cursor_visible(show)

    # ---- 状态 ----
    def state(self):
        with self.lock:
            return {
                "visible": self.visible,
                "rev": self.rev,
                "enabled": bool(self.config["enabled"]),
                "idleSeconds": int(self.config.get("idleSeconds") or 0),
                "idleMinutes": int(self.config["idleMinutes"]),
                "monitor": self.config["monitor"],
                "idle": round(self.idle(), 1),
                "threshold": self.threshold(),
                "window": bool(self.hwnd),
                "windowMode": self.config.get("windowMode", "kiosk"),
                "keepBrowser": bool(self.config.get("keepBrowser", True)),
                "alt": alt_down(),
                "hold": self.holding(),
                "manual": bool(self.manual),
                "cursor": bool(self.cursor_shown),
                "page": dict(self.page_state),
                "themeRev": self.theme_rev,
                "dryRun": bool(self.args.dry_run),
            }

    def patch_theme(self, patch):
        clean = sanitize_theme_patch(patch)
        with self.lock:
            self.theme.update(clean)
            self.theme_rev += 1
            theme = dict(self.theme)
            rev = self.theme_rev
        save_theme(theme)
        return {"rev": rev, "theme": theme}

    def _drop_cli_idle_override(self, why):
        """设置页改了阈值 → 取消命令行 --idle-seconds 的临时覆盖，否则改了没反应。"""
        if getattr(self.args, "idle_seconds", 0):
            self.args.idle_seconds = 0
            log("设置页修改了空闲阈值（%s）→ 已取消命令行 --idle-seconds 的临时覆盖" % why)

    def patch_config(self, patch):
        with self.lock:
            if "enabled" in patch:
                self.config["enabled"] = bool(patch["enabled"])
            if "idleSeconds" in patch:
                try:
                    secs = int(round(float(patch["idleSeconds"])))
                    # 0 = 停用秒级设置、回退到 idleMinutes；否则夹到 10 秒 ~ 24 小时
                    self.config["idleSeconds"] = 0 if secs <= 0 else max(
                        MIN_IDLE_SECONDS, min(MAX_IDLE_SECONDS, secs))
                    if self.config["idleSeconds"]:
                        # 同步一个近似分钟值，方便直接看 config.json
                        self.config["idleMinutes"] = max(1, min(600, int(round(secs / 60.0)) or 1))
                    self._drop_cli_idle_override("idleSeconds")
                except (TypeError, ValueError) as exc:
                    log("忽略非法的 idleSeconds:", repr(exc))
            if "idleMinutes" in patch:
                try:
                    self.config["idleMinutes"] = max(1, min(600, int(patch["idleMinutes"])))
                    self.config["idleSeconds"] = 0
                    self._drop_cli_idle_override("idleMinutes")
                except (TypeError, ValueError) as exc:
                    log("忽略非法的 idleMinutes:", repr(exc))
            if patch.get("monitor") in ("cursor", "primary", "all"):
                self.config["monitor"] = patch["monitor"]
        save_config(self.config)
        log("配置更新:", self.config)

    # ---- 浏览器窗口 ----
    def browser_command(self, exe, mode):
        """拼装浏览器命令行（kiosk = 真无边框全屏；app = 普通窗口，备用）。

        URL 里同时带 t（鉴权 token）与 w（窗口标识）：后者会出现在窗口标题里，
        所以刻意与 token 分开，避免同机进程通过窗口标题拿到 token。
        """
        url = "http://127.0.0.1:%d/?overlay=1&t=%s&w=%s" % (self.port, self.token, self.window_tag)
        common = [
            "--user-data-dir=" + PROFILE_DIR,
            "--no-first-run",
            "--no-default-browser-check",
            "--no-service-autorun",
            "--disable-sync",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-features=Translate,msEdgeIdentityFeatures,msEdgeSyncPromo,"
            "EdgeSigninPromo,ImplicitSignin,msEdgeFirstRunExperience",
        ]
        if mode == "kiosk":
            # --kiosk：真正的无边框全屏（没有标题栏 / 最小化 / 关闭按钮，也没有白边）
            return [exe, "--kiosk", url] + common
        return [exe, "--app=" + url, "--window-size=1280,800",
                "--window-position=-32000,-32000"] + common

    def wait_for_window(self, mode, timeout=40.0):
        """等时钟窗口出现并接管它。kiosk 窗口没有标题，所以标题匹配失败时按 profile 进程找。"""
        wanted = ICON_TITLE_PREFIX + self.window_tag
        deadline = time.time() + timeout
        pids = set()
        last_pid_query = 0.0
        while time.time() < deadline and not self.stop_event.is_set():
            hwnds = find_windows_by_title(wanted)
            if not hwnds:
                if not pids or time.time() - last_pid_query > 3:
                    last_pid_query = time.time()
                    pids |= profile_browser_pids()
                if pids:
                    hwnds = find_windows_by_pids(pids)
            if hwnds:
                self.attach_windows(hwnds, mode)
                return True
            time.sleep(0.25)
        log("等待时钟窗口超时（%.0f 秒）" % timeout)
        return False

    def launch_browser(self):
        if self.args.no_browser:
            return
        exe = self.config.get("browser") or find_browser()
        if not exe or not os.path.isfile(exe):
            log("未找到 Edge/Chrome，无法显示屏保窗口（可用 --browser 指定路径）")
            return
        mode = self.config.get("windowMode", "kiosk")
        cmd = self.browser_command(exe, mode)
        log("启动时钟窗口:", os.path.basename(exe), "模式:", mode)
        kill_profile_browser(quiet=True)      # 清掉上次异常退出遗留的时钟浏览器
        seed_profile()                        # 跳过首次运行 / 登录同步弹窗
        try:
            self.proc = subprocess.Popen(cmd, close_fds=True,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            log("浏览器启动失败:", exc)
            return
        self.wait_for_window(mode)

    def attach_windows(self, hwnds, mode):
        """首个窗口作为主窗口，其余重复窗口一并隐藏，避免残留。"""
        self.hwnd = hwnds[0]
        for hwnd in hwnds:
            if mode != "kiosk":
                strip_frame(hwnd)     # app 模式下才需要手动去边框
            hide_window(hwnd)
        log("时钟窗口已就绪并隐藏 (hwnd=%s，共 %d 个)" % (self.hwnd, len(hwnds)))

    def ensure_browser(self):
        if self.args.no_browser or self.launching:
            return
        if self.hwnd and user32.IsWindow(self.hwnd):
            return
        keep = bool(self.config.get("keepBrowser", True))
        idle = self.idle()
        if not keep and not self.manual:
            # 按需启动：只在空闲接近阈值时才拉起浏览器，平时不占内存
            start_at = max(0.0, self.threshold() - float(self.config.get("prewarmSeconds", 20)))
            if idle < start_at:
                return
        if time.time() - self.last_launch < LAUNCH_RETRY_S:
            return
        self.last_launch = time.time()
        self.hwnd = None
        log("拉起时钟浏览器（%s）" % ("常驻预热" if keep else "按需启动"))
        threading.Thread(target=self._launch_once, name="browser", daemon=True).start()

    def _launch_once(self):
        self.launching = True
        try:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                except Exception:
                    pass
            self.launch_browser()
        finally:
            self.launching = False

    # ---- 显示 / 隐藏 ----
    def show(self, reason=None):
        if self.visible:
            return
        # 窗口还没就绪（浏览器冷启动中）时不置位，下一轮 tick 会自动重试
        if not self.args.dry_run and not self.hwnd:
            if time.time() - self._pending_log > 5:
                self._pending_log = time.time()
                log("  时钟窗口尚未就绪，稍后自动重试显示")
            return
        with self.lock:
            self.visible = True
            self.rev += 1
        self.hold_until = (time.time() + HOLD_TTL_S) if self.manual else 0.0
        self._hold_active = False
        self._hold_source = ""
        log(reason or ("★ 空闲达到 %.0f 秒 → 显示翻页时钟" % self.threshold()))
        if self.args.dry_run:
            log("  (dry-run：不实际显示窗口)")
            return
        rect = target_rect(self.config["monitor"])
        dpi_aware_here()
        got = show_window(self.hwnd, rect)
        if got and got[2:] != rect[2:]:
            log("  ⚠ 实际窗口尺寸 %dx%d，与请求的 %dx%d 不一致" % (got[2], got[3], rect[2], rect[3]))
        else:
            log("  已全屏显示：%dx%d @ (%d,%d)" % (rect[2], rect[3], rect[0], rect[1]))
        self.set_cursor(self.cursor_should_show(self.idle()))

    def hide(self):
        if not self.visible:
            return
        with self.lock:
            self.visible = False
            self.rev += 1
        self._hold_active = False
        self._hold_source = ""
        self.hold_until = 0.0
        log("☆ 检测到输入 → 页面淡出并隐藏窗口")
        self.ack.clear()
        # 等页面确认淡出（页面在 transitionend 后回 ack）；1.2 秒兜底：
        # 轮询间隔(≤200ms) + 淡出(320ms) 有抖动，给 0.6 秒偶尔会不够，看起来像硬切
        self.ack.wait(ACK_TIMEOUT_S)
        if self.args.dry_run:
            return
        if self.hwnd:
            hide_window(self.hwnd)
        self.set_cursor(True)
        self.maybe_release_browser()

    def maybe_release_browser(self):
        """keepBrowser=False 时，退出一律彻底结束浏览器进程（保留宿主继续监测）。

        进程枚举要起 PowerShell、再逐个 taskkill，最坏能阻塞几十秒；
        这些都在独立线程里做，监测线程只做「立刻归还句柄」的簿记，
        否则动一下鼠标后屏保会卡好几秒才退。
        """
        if self.config.get("keepBrowser", True) or self.args.no_browser:
            return
        with self.lock:
            if self._releasing:
                return                      # 已有一轮清理在跑，别重复起线程
            self._releasing = True
        log("🧹 按需启动模式：结束时钟浏览器进程（后台执行，不阻塞监测）")
        proc = self.proc
        self.proc = None                    # 主循环立刻继续
        self.hwnd = None
        threading.Thread(target=self._release_worker, args=(proc,),
                         name="release", daemon=True).start()

    def _release_worker(self, proc):
        try:
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception as exc:
                    log("结束时钟浏览器进程失败（将改用 taskkill）:", repr(exc))
            kill_profile_browser(quiet=True)
        finally:
            with self.lock:
                self._releasing = False

    # ---- 主循环 ----
    def watch(self):
        dpi_aware_here()
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                log("监测循环异常:", repr(exc))
            self.stop_event.wait(0.2)

    # ---- 主循环：拆成「可见/不可见」两个子步骤，便于单独阅读与修改 ----
    def tick(self):
        self.ensure_browser()
        idle = self.idle()

        # 手动模式：不看空闲、不自动退出；窗口被关掉就整体退出
        if self.manual:
            if not self.visible:
                self.show(reason="★ 手动模式 → 立即全屏显示翻页时钟")
            elif self.hwnd and not user32.IsWindow(self.hwnd):
                log("时钟窗口已关闭 → 手动模式退出")
                self.exit_requested = True
            # 手动模式的指针规则：动一下显示、停 3 秒自动隐藏（必须在这里维护）
            self.set_cursor(self.cursor_should_show(idle))
            return

        if self.visible:
            self._tick_visible(idle)
        else:
            self._tick_hidden(idle)

    def _hold_label(self, alt, hold):
        """当前「保持显示」的来源描述（Alt / 面板 / 两者都有）。"""
        src = "Alt 已按住" if alt else ""
        if hold:
            src = (src + " + " if src else "") + "设置面板使用中"
        return src

    def _tick_visible(self, idle):
        """时钟正在显示：维护保持状态、指针显隐，并按空闲决定是否退出。"""
        alt = alt_down()
        hold = self.holding()
        held = alt or hold
        src = self._hold_label(alt, hold)

        if held and not self._hold_active:
            self._hold_active = True
            self._hold_source = src
            log("⏸ 保持显示（%s）→ 暂停自动退出" % src)
        elif held and src != self._hold_source:
            # 例如：松开 Alt 后由「设置面板保持」接管，屏保不会退出
            self._hold_source = src
            log("⏸ 保持显示来源切换 → %s" % src)
        elif not held and self._hold_active:
            self._hold_active = False
            self._hold_source = ""
            log("▶ 保持解除 → 移动鼠标即退出")

        # 全屏显示期间的鼠标指针：屏保=仅保持时显示；手动=动一下显示、停 3 秒隐藏
        self.set_cursor(self.cursor_should_show(idle))

        if not held and idle < INPUT_IDLE_EPS_S:     # 任意输入都会让空闲时间归零
            self.hide()

    def _tick_hidden(self, idle):
        """时钟没显示：恢复指针、按需回收预热浏览器，到点则跳出。"""
        # 桌面上的指针必须恢复正常（也兜住上次异常退出遗留的隐藏）
        self.set_cursor(True)

        if not bool(self.config.get("keepBrowser", True)) and self.hwnd and not self.launching:
            # 用户回来了：把预热的浏览器也收掉，避免常驻内存
            start_at = max(0.0, self.threshold() - float(self.config.get("prewarmSeconds", 20)))
            if idle < max(0.0, start_at - 5):
                self.maybe_release_browser()

        if self.config["enabled"] and idle >= self.threshold():
            self.show()
        elif self.threshold() < 120 and time.time() - self._last_probe > 3:
            # 阈值被设成秒级（测试模式）时，滚动报告空闲进度
            self._last_probe = time.time()
            log("  空闲 %.1fs / 阈值 %.0fs" % (idle, self.threshold()))

    def shutdown(self):
        self.stop_event.set()
        set_cursor_visible(True)
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception as exc:
                log("退出时结束时钟浏览器进程失败（改用 taskkill）:", repr(exc))
        kill_profile_browser(quiet=True)
        try:
            if os.path.isfile(PID_PATH):
                os.remove(PID_PATH)
        except Exception:
            pass
        drop_runtime()
