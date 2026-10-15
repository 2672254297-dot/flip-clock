#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
翻页时钟 · 配置层：config / theme / runtime 的读写与取值校验。
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
import re

from flipclock_common import (BROWSER_CANDIDATES, CONFIG_PATH, DEFAULT_CONFIG,
                              DEFAULT_THEME, HEX_COLOR_RE, MAX_BODY, MAX_IMAGE_LEN,
                              PROFILE_DIR, RUNTIME_PATH, THEME_PATH, log)

# ---------------------------------------------------------------- 配置
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
            if "idleSeconds" not in data:
                # 旧版 config.json 只有 idleMinutes：关掉秒级设置，按分钟回退
                cfg["idleSeconds"] = 0
    except Exception:
        pass
    return cfg


def write_json_atomic(path, obj):
    """先写临时文件再 os.replace 原子替换。

    直接以 "w" 截断写有风险：stop.bat 用的是 taskkill /F，
    若恰好在写入中途被杀，会留下半截 JSON，下次启动静默回退默认值（看起来像"设置丢了"）。
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save_config(cfg):
    try:
        write_json_atomic(CONFIG_PATH, cfg)
    except Exception as exc:
        log("配置保存失败:", exc)


def save_runtime(port, token):
    """把 {pid, port, token} 写到 runtime.json：settings.bat / selftest 靠它拿到本次运行的 token。"""
    try:
        write_json_atomic(RUNTIME_PATH, {"pid": os.getpid(), "port": int(port), "token": token})
    except Exception as exc:
        log("运行信息写入失败:", exc)


def read_runtime():
    try:
        with open(RUNTIME_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def drop_runtime():
    try:
        os.remove(RUNTIME_PATH)
    except OSError:
        pass


def find_browser():
    for path in BROWSER_CANDIDATES:
        if os.path.isfile(path):
            return path
    return ""


def seed_profile():
    """预置独立 profile：跳过「首次运行」与 Edge 登录/同步引导（否则会弹白色对话框）。"""
    try:
        os.makedirs(PROFILE_DIR, exist_ok=True)
        state_path = os.path.join(PROFILE_DIR, "Local State")
        data = {}
        if os.path.isfile(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}

        def put(key, value):
            if data.get(key) != value:
                data[key] = value
                return True
            return False

        changed = False
        changed |= put("first_run_complete", True)
        browser = dict(data.get("browser") or {})
        if browser.get("has_seen_welcome_page") is not True:
            browser["has_seen_welcome_page"] = True
            changed = True
        if browser:
            changed |= put("browser", browser)
        changed |= put("signin", {"allowed": False, "allowed_on_next_startup": False})
        changed |= put("sync", {"requested": False})

        if changed:
            tmp = state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, state_path)

        marker = os.path.join(PROFILE_DIR, "First Run")
        if not os.path.exists(marker):
            with open(marker, "w", encoding="utf-8"):
                pass
        return True
    except Exception as exc:
        log("预置浏览器配置失败:", exc)
        return False


def sanitize_theme_patch(patch):
    """只接受合法取值；非法项直接忽略（返回清洗后的 dict）。"""
    clean = {}
    for key, default in DEFAULT_THEME.items():
        if key not in patch:
            continue
        value = patch[key]
        if key == "img":
            if isinstance(value, str) and len(value) <= MAX_IMAGE_LEN:
                clean[key] = value
            else:
                log("忽略非法的主题项 img（类型不符或超过 %d 字节）" % MAX_IMAGE_LEN)
        elif key == "mask":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                log("忽略非法的主题项 mask（需要数字）")
            else:
                clean[key] = max(0, min(100, int(value)))
        else:                                   # bg / card / digit：必须是 #RRGGBB
            if isinstance(value, str) and HEX_COLOR_RE.match(value):
                clean[key] = value.lower()
            else:
                log("忽略非法的主题项 %s（需要 #RRGGBB 颜色）" % key)
    return clean


def check_limits_consistent():
    """启动自检：请求体上限必须能装下合法图片，否则「换背景图」会被 413 挡掉。"""
    if MAX_BODY <= MAX_IMAGE_LEN:
        log("⚠ 配置异常：MAX_BODY(%d) 应大于 MAX_IMAGE_LEN(%d)，否则换背景图会被 413 挡掉"
            % (MAX_BODY, MAX_IMAGE_LEN))
        return False
    return True


def load_theme():
    theme = dict(DEFAULT_THEME)
    try:
        with open(THEME_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            theme.update({k: v for k, v in data.items() if k in DEFAULT_THEME})
    except Exception:
        pass
    return theme


def save_theme(theme):
    try:
        write_json_atomic(THEME_PATH, theme)
    except Exception as exc:
        log("配色保存失败:", exc)
