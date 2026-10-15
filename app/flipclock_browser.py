#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
翻页时钟 · 浏览器进程层：按专用 profile 精确查找与清理时钟浏览器。
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

import os
import subprocess

from flipclock_common import PROFILE_DIR, log

# ---------------------------------------------------------------- 清理浏览器
def profile_browser_pids():
    """本项目专用 profile 下的浏览器进程 PID 集合（不会命中用户自己的 Edge）。"""
    needle = PROFILE_DIR.replace("'", "''")
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' or Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -and $_.CommandLine.Contains('%s') } | "
        "ForEach-Object { $_.ProcessId }"
    ) % needle
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, errors="replace", timeout=30)
        return {int(x) for x in (out.stdout or "").split() if x.strip().isdigit()}
    except subprocess.TimeoutExpired:
        log("枚举时钟浏览器进程超时（30s），本次视为无残留")
        return set()
    except Exception as exc:
        log("枚举时钟浏览器进程失败（视为无残留）:", repr(exc))
        return set()


def kill_profile_browser(quiet=False):
    """按 user-data-dir 精确结束本项目的时钟浏览器进程（不会影响用户自己的 Edge）。"""
    if not quiet:
        pass
    pids = profile_browser_pids()
    killed = []
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, text=True, errors="replace", timeout=20)
            killed.append(str(pid))
        except Exception as exc:
            log("taskkill 失败 (pid=%s):" % pid, repr(exc))
    if killed and not quiet:
        log("已结束时钟浏览器进程:", ", ".join(killed))
    return len(killed)
