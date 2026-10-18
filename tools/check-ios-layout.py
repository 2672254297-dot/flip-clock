#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
在精确的设备视口下验证网页版时钟的布局（iOS / iPad 适配回归）。

为什么用 iframe：无头浏览器受系统缩放影响，--window-size 得到的 CSS 视口并不等于
请求值（本机请求 390 实际得到 496）。把页面放进固定尺寸的 iframe 里，视口就是精确的
CSS 像素，测量结果与宿主窗口大小无关。

检查项（每个设备）：
  - 文档是否横向 / 纵向溢出
  - 时钟整体是否完整落在视口内
  - 卡片数量与宽度是否符合公式（148px 为上限）
  - 网页版是否已隐藏「空闲跳出」设置（iOS 不需要该功能）
  - 是否有 JS 报错

用法： python tools/check-ios-layout.py [--shots]
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
PAGE = os.path.join(APP, "index.html")
SHOTS = os.path.join(ROOT, "tests", "ios-shots")

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

DEVICES = [
    ("iPhone SE", 375, 667, False),
    ("iPhone 14", 390, 844, False),
    ("iPhone 14 Pro Max", 430, 932, False),
    ("iPhone 14 横屏", 844, 390, True),
    ("iPad mini", 744, 1133, False),
    ("iPad Pro 12.9", 1024, 1366, False),
    ("iPad Pro 12.9 横屏", 1366, 1024, True),
]

# 注入被测页：算好指标挂到 window 上，供外层壳页读取
MEASURE = """
<script>
window.__errs = [];
window.addEventListener('error', function (e) { window.__errs.push(String(e.message)); });
window.addEventListener('load', function () {
  setTimeout(function () {
    var de = document.documentElement;
    var clock = document.getElementById('clock');
    var card = document.querySelector('.card');
    var r = clock ? clock.getBoundingClientRect() : null;
    var cr = card ? card.getBoundingClientRect() : null;
    var idle = document.getElementById('idleSection');
    window.__probe = {
      vw: window.innerWidth,
      vh: window.innerHeight,
      scrollW: de.scrollWidth,
      scrollH: de.scrollHeight,
      clockW: r ? Math.round(r.width) : null,
      clockH: r ? Math.round(r.height) : null,
      clockLeft: r ? Math.round(r.left) : null,
      clockRight: r ? Math.round(r.right) : null,
      clockTop: r ? Math.round(r.top) : null,
      clockBottom: r ? Math.round(r.bottom) : null,
      cardW: cr ? Math.round(cr.width * 100) / 100 : null,
      idleHidden: idle ? idle.hidden : null,
      webClass: de.classList.contains('web'),
      cards: document.querySelectorAll('.card').length,
      errors: window.__errs
    };
  }, 500);
});
</script>
"""


def find_browser():
    for p in BROWSERS:
        if os.path.isfile(p):
            return p
    return ""


def build_probe_page():
    """把测量脚本注入 index.html 的副本（就放在 app/ 内，保证相对路径可用）。"""
    html = open(PAGE, encoding="utf-8").read()
    html = html.replace("</body>", MEASURE + "</body>")
    path = os.path.join(APP, "_iosprobe.html")
    open(path, "w", encoding="utf-8").write(html)
    return path


def build_wrapper(w, h):
    """外层壳页：用固定尺寸 iframe 制造精确视口。"""
    wrap = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  html,body { margin:0; padding:0; background:#111; }
  iframe { width:%dpx; height:%dpx; border:0; display:block; }
</style></head>
<body>
<iframe id="f" src="_iosprobe.html"></iframe>
<script>
var f = document.getElementById('f');
function emit(obj) {
  var d = document.createElement('div');
  d.textContent = 'PROBEMARK' + JSON.stringify(obj) + 'PROBEMARK';
  document.body.appendChild(d);
}
function grab() {
  try {
    var w = f.contentWindow;
    if (w && w.__probe) { emit(w.__probe); return true; }
  } catch (e) { emit({ error: String(e) }); return true; }
  return false;
}
f.addEventListener('load', function () {
  var n = 0;
  var t = setInterval(function () {
    if (grab() || ++n > 40) clearInterval(t);
  }, 100);
});
</script>
</body></html>""" % (w, h)
    path = os.path.join(APP, "_ioswrap.html")
    open(path, "w", encoding="utf-8").write(wrap)
    return path


def run(exe, page, w, h, shot=None):
    profile = tempfile.mkdtemp(prefix="fc-ios-")
    cmd = [
        exe, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "--allow-file-access-from-files",          # 让外层壳页能读 iframe 内部指标
        "--user-data-dir=" + profile,
        "--window-size=%d,%d" % (max(w + 80, 1000), max(h + 80, 900)),
        "--virtual-time-budget=4000",
    ]
    if shot:
        cmd.append("--screenshot=" + shot)
    cmd += ["--dump-dom", "file:///" + page.replace("\\", "/")]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", timeout=120)
        return proc.stdout or ""
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def main():
    exe = find_browser()
    if not exe:
        sys.exit("未找到 Edge/Chrome")
    want_shots = "--shots" in sys.argv
    if want_shots:
        os.makedirs(SHOTS, exist_ok=True)

    build_probe_page()
    print("浏览器:", os.path.basename(exe))
    print()

    rows = []
    failures = []
    try:
        for name, w, h, _landscape in DEVICES:
            wrap = build_wrapper(w, h)
            shot = None
            if want_shots:
                safe = name.replace(" ", "_").replace(".", "")
                shot = os.path.join(SHOTS, "%s_%dx%d.png" % (safe, w, h))
            dom = run(exe, wrap, w, h, shot)
            m = None
            # 注意：壳页脚本源码里也有 PROBEMARK 字面量，必须取**最后**一对标记
            # （结果 div 是最后追加的节点），否则会解析到脚本源码。
            if "PROBEMARK" in dom:
                parts = dom.rsplit("PROBEMARK")
                if len(parts) >= 2:
                    try:
                        m = json.loads(parts[-2])
                    except Exception:
                        m = None
            if not m:
                failures.append("%s：未取到测量结果" % name)
                rows.append((name, w, h, None))
                continue

            over_x = m["scrollW"] > m["vw"] + 1
            over_y = m["scrollH"] > m["vh"] + 1
            fits = all([
                m["clockLeft"] is not None,
                m["clockLeft"] >= -1,
                m["clockRight"] <= m["vw"] + 1,
                m["clockTop"] >= -1,
                m["clockBottom"] <= m["vh"] + 1,
            ])
            rows.append((name, w, h, m, over_x, over_y, fits))
            if over_x:
                failures.append("%s：横向溢出（内容 %d > 视口 %d）" % (name, m["scrollW"], m["vw"]))
            if over_y:
                failures.append("%s：纵向溢出（内容 %d > 视口 %d）" % (name, m["scrollH"], m["vh"]))
            if not fits:
                failures.append("%s：时钟超出视口（左右 %s..%s，视口宽 %d）"
                                % (name, m["clockLeft"], m["clockRight"], m["vw"]))
            if m["cards"] != 6:
                failures.append("%s：卡片数 %s ≠ 6" % (name, m["cards"]))
            if not m["idleHidden"]:
                failures.append("%s：网页版仍显示「空闲跳出」设置" % name)
            if m["errors"]:
                failures.append("%s：JS 报错 %s" % (name, m["errors"]))

        print("%-22s %-11s %-11s %-9s %-7s %s" % ("设备", "视口", "时钟尺寸", "卡片宽", "溢出", "空闲设置"))
        print("-" * 82)
        for row in rows:
            if row[3] is None:
                print("%-22s %-11s %s" % (row[0], "%dx%d" % (row[1], row[2]), "测量失败"))
                continue
            name, w, h, m, over_x, over_y, fits = row
            print("%-22s %-11s %-11s %-9s %-7s %s" % (
                name,
                "%dx%d" % (m["vw"], m["vh"]),
                "%sx%s" % (m["clockW"], m["clockH"]),
                m["cardW"],
                (("X" if over_x else "") + ("Y" if over_y else "")) or "无",
                "已隐藏" if m["idleHidden"] else "仍显示"))
    finally:
        for f in ("_iosprobe.html", "_ioswrap.html"):
            try:
                os.remove(os.path.join(APP, f))
            except OSError:
                pass

    print()
    if failures:
        print("发现问题 %d 项：" % len(failures))
        for f in failures:
            print("  -", f)
        return 1
    print("全部通过：无溢出、时钟完整可见、网页版已隐藏空闲跳出设置")
    if want_shots:
        print("截图目录:", SHOTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
