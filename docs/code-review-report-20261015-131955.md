# 代码审查报告 - flip-clock（翻页时钟 + 空闲屏保）

> 本文件由 `7d-code-reviewer` 生成的 HTML 报告转换而来，供 agent / 工具直接读取。
> 原始报告：`code-review-report-20261015-131955.html`

## 元信息

| 项 | 值 |
| --- | --- |
| 审查对象 | `flip-clock`（翻页时钟 + 空闲屏保；Python 宿主 + 单文件 Web 页面） |
| 审查时间 | 2026-10-15T13:19:55+08:00 |
| 审查方式 | 逐行审查 + 本机实测验证（raw socket 直连本地服务） |
| 总体评分 | **5 / 10** |
| 维度 · 代码质量 | 7/10 |
| 维度 · 安全性 | 3/10 |
| 维度 · 性能 | 6/10 |
| 维度 · 可维护性 | 7/10 |
| 计数 · 严重问题 | 3 |
| 计数 · 中等问题 | 7 |
| 计数 · 轻微问题 | 5 |
| 计数 · 优点 | 6 |

## 审查摘要

整体工程质量不错：状态机把「保持显示/淡出时序/进程清理」这些容易出错的点都考虑到了，自测能真跑端到端并容忍环境干扰，注释与文档解释充分。但本地 HTTP 控制服务的安全性存在三个已实测确认的严重问题（任意文件读取、无鉴权+通配 CORS 的 CSRF、浏览器 profile 目录暴露），修复前不建议在这台机器上长期常驻运行。此外还有若干中等项：监测线程同步杀进程会阻塞、请求体无上限、配置写入非原子、超长函数与静默吞异常。

## 问题总览

| # | 级别 | 位置 | 问题 |
| --- | --- | --- | --- |
| 1 | 🔴 严重 | `flipclock_overlay.py:972-976（do_GET 静态文件分支）` | 任意本机文件读取：绝对路径可绕过根目录限制 |
| 2 | 🔴 严重 | `flipclock_overlay.py:910-913（_cors）、978-1050（do_POST 全部控制接口）` | 控制接口无鉴权 + 通配 CORS：任意网页可遥控屏保（CSRF） |
| 3 | 🔴 严重 | `flipclock_overlay.py:969-976（服务根目录 = 项目目录 ROOT）` | 浏览器 profile 目录被明文暴露 |
| 4 | 🟡 中等 | `flipclock_overlay.py:766-777（maybe_release_browser）、876-898（kill_profile_browser）` | 监测线程里同步执行 PowerShell + taskkill，最长阻塞 30 秒 |
| 5 | 🟡 中等 | `flipclock_overlay.py:978-981（do_POST 读请求体）` | 请求体大小无上限，可被超大 Content-Length 打爆内存 |
| 6 | 🟡 中等 | `flipclock_overlay.py:577-586（patch_theme）、381-390 / 456-462（save_config / save_theme）` | theme.json 无取值/长度校验，且配置文件写入不是原子的 |
| 7 | 🟡 中等 | `flipclock_overlay.py:336-349（force_hide_cursor）` | 隐藏光标的『压计数』只在第一次生效，之后无法纠正被抬高的计数 |
| 8 | 🟡 中等 | `flipclock_overlay.py:1065-1066（--idle-seconds）、508-514（threshold）` | 命令行阈值不做上下限校验，极小/负值会造成显示抖动 |
| 9 | 🟡 中等 | `selftest.py:371（main，249 行）、flipclock_overlay.py:1120+（main，101 行）、tick()、launch_browser()` | 超长函数：远超「函数 ≤50 行」的规范 |
| 10 | 🟡 中等 | `flipclock_overlay.py（共 25 处 except Exception）` | 大量静默吞异常，排障时缺少线索 |
| 11 | 🟢 轻微 | `selftest.py:73、79` | Python 函数命名不符合 snake_case 规范 |
| 12 | 🟢 轻微 | `flipclock_overlay.py（1187 行）、index.html（1332 行）、selftest.py（620 行）` | 单文件远超 500 行建议值 |
| 13 | 🟢 轻微 | `flipclock_overlay.py（tick/show/hide/HTTP 各处）、index.html:689 等` | 魔法数字散落各处，缺少常量与单位说明 |
| 14 | 🟢 轻微 | `index.html:789（applyTheme）` | 背景图片值未做协议白名单 |
| 15 | 🟢 轻微 | `settings.bat:9、stop.bat、clean.bat` | 启动脚本容错不一致 |

## 🔴 严重问题（3）

### 严重-1 任意本机文件读取：绝对路径可绕过根目录限制

- **位置**：`flipclock_overlay.py:972-976（do_GET 静态文件分支）`
- **描述**：静态文件分支用 `rel = os.path.normpath(path.lstrip("/"))` 后直接 `os.path.join(ROOT, rel)`，只用 `rel.startswith("..")` 挡住了**相对**穿越。当 `rel` 是盘符/绝对路径时，NT 路径语义下 `join` 会**丢弃 ROOT**，于是请求变成读取本机任意路径。
  **实测证据（本机 raw socket）：**
  `GET //C:/Windows/win.ini` → **200 OK**，返回文件内容；
  `GET /C:\Windows\win.ini` → **200 OK**；而 `GET /../flipclock_overlay.py` → 404（说明只挡住了相对形式）。
  响应同时带 `Access-Control-Allow-Origin: *`，因此**用户访问的任意网页**都能跨域读取其可读的任意文件（文档、配置、其它软件的凭据文件等）。
- **修复建议**：用白名单或 `Path.resolve()` + `relative_to()` 强制目标必须落在允许目录内；更稳妥的是静态服务只放行明确允许的文件，其余一律 404。

修复示例：

```python
ALLOWED = {"index.html"}
def _serve(self, url_path):
    base = Path(ROOT).resolve()
    rel = unquote(url_path).lstrip("/")          # 先解码再做路径判断
    target = (base / rel).resolve()
    try:
        target.relative_to(base)                 # 绝对路径/盘符路径会在此抛错
    except ValueError:
        return self._text(403, "forbidden")
    if target.name not in ALLOWED and target.suffix != ".png":
        return self._text(404, "not found")
    return self._file(str(target))
```

### 严重-2 控制接口无鉴权 + 通配 CORS：任意网页可遥控屏保（CSRF）

- **位置**：`flipclock_overlay.py:910-913（_cors）、978-1050（do_POST 全部控制接口）`
- **描述**：`_cors()` 对所有响应发送 `Access-Control-Allow-Origin: *`，而 `/__exit`、`/__config`、`/__theme`、`/__hold`、`/__ack`、`/__page` 均不校验来源、不校验 token、不校验 Host。页面 URL 里虽带 `?t=<token>`，但仅用于设置窗口标题，未参与任何鉴权。
  **实测证据：**`POST /__exit`（带 `Origin: http://evil.example`）→ **200 OK**；随后宿主日志出现「页面请求退出」并开始清理进程。
  **影响：**恶意/被挂马的网页可以：关闭屏保（`{"enabled": false}`）、退出手动时钟（`/__exit`）、篡改配色与 `theme.json`、反复声明 `hold` 让屏保永不退出；DNS rebinding 还能绕过部分浏览器的同源限制。
- **修复建议**：1) 校验 `Origin`/`Referer`/`Host` 只允许 `127.0.0.1:<port>` 与 `localhost:<port>`；2) 把启动 URL 里的 token 真正用起来：POST 必须带 `X-FlipClock-Token` 且与宿主一致；3) 去掉通配 CORS，改为精确来源。

修复示例：

```python
def _allowed_origin(self):
    origin = self.headers.get("Origin")
    if origin is None:
        return True                      # 同源/无 Origin（本机页面）放行
    return origin in ("http://127.0.0.1:%d" % self.app.port,
                      "http://localhost:%d" % self.app.port)

def do_POST(self):
    if not self._allowed_origin():
        return self._text(403, "forbidden")
    if self.headers.get("X-FlipClock-Token") != self.app.token:
        return self._text(403, "forbidden")
    ...
```

### 严重-3 浏览器 profile 目录被明文暴露

- **位置**：`flipclock_overlay.py:969-976（服务根目录 = 项目目录 ROOT）`
- **描述**：HTTP 服务根目录就是项目目录，因此 `.edge-profile/` 下的浏览器数据文件也在可服务范围内。
  **实测证据：**`GET /.edge-profile/Default/Preferences` → **200 OK**，返回 14695 字节（同目录下还存在 `Local State`、Cookies/History 等标准文件）。
  虽然是项目专用的独立 profile、不含用户日常浏览记录，但仍属不应对外暴露的本机数据；叠加通配 CORS 后可被任意网页抓取。
- **修复建议**：静态服务改为白名单（`index.html`、需要的 `*.png`），并把 `.edge-profile` 放到服务根之外（例如 `%LOCALAPPDATA%` 下的独立目录）。

修复示例：

```
ALLOW_SUFFIX = {".png", ".css", ".js"}
if target.name != "index.html" and target.suffix not in ALLOW_SUFFIX:
    return self._text(404, "not found")   # config.json / .edge-profile 不再对外
```

## 🟡 中等问题（7）

### 中等-1 监测线程里同步执行 PowerShell + taskkill，最长阻塞 30 秒

- **位置**：`flipclock_overlay.py:766-777（maybe_release_browser）、876-898（kill_profile_browser）`
- **描述**：`tick()` 在按需启动模式会调用 `maybe_release_browser()`，后者同步执行 `kill_profile_browser()`：内部先跑一次 PowerShell（`subprocess.run(..., timeout=30)`）枚举进程，再对每个 PID 调 `taskkill`（每个 `timeout=20`）。这段时间整个监测循环停摆，表现为「动了鼠标要等几秒才退出屏保」，在进程多/杀进程慢时更明显。
- **修复建议**：把清理工作丢到独立线程（清理期间先复位 hwnd/visible 状态，让主循环继续响应输入）；或改用 Win32 API 直接结束进程，避免起 PowerShell。

修复示例：

```python
def maybe_release_browser(self):
    if self.config.get("keepBrowser", True) or self.args.no_browser:
        return
    log("🧹 按需启动模式：结束时钟浏览器进程")
    self.proc, self.hwnd = None, None      # 主循环立刻继续
    threading.Thread(target=kill_profile_browser, kwargs={"quiet": True},
                     daemon=True).start()
```

### 中等-2 请求体大小无上限，可被超大 Content-Length 打爆内存

- **位置**：`flipclock_overlay.py:978-981（do_POST 读请求体）`
- **描述**：直接 `raw = self.rfile.read(length)`，`length` 完全来自请求头且没有上限校验。配合「无鉴权 + 通配 CORS」，任意网页都能用一个大 POST 让宿主一次性分配巨量内存。
- **修复建议**：加 `MAX_BODY` 上限，超限返回 413 并关闭连接。

修复示例：

```python
MAX_BODY = 256 * 1024
length = int(self.headers.get("Content-Length") or 0)
if length > MAX_BODY:
    return self._text(413, "payload too large")
raw = self.rfile.read(length) if length else b""
```

### 中等-3 theme.json 无取值/长度校验，且配置文件写入不是原子的

- **位置**：`flipclock_overlay.py:577-586（patch_theme）、381-390 / 456-462（save_config / save_theme）`
- **描述**：`patch_theme()` 只做 `isinstance` 类型检查：`img` 可以是任意长字符串（写入 theme.json 造成磁盘膨胀，页面也会把它拼进 CSS 值），`mask` 不校验范围。
  另外 `save_config` / `save_theme` 都是直接 `open(..., "w")` 截断写，而 `stop.bat` 用的是 `taskkill /F`；若恰好在写入中途被杀，会留下半截 JSON，下次启动 `load_config()` 静默回退默认值，用户会感觉「设置莫名丢失」。项目内 `seed_profile()` 已经用了 tmp + `os.replace`，标准不统一。
- **修复建议**：1) 校验：颜色走 `^#[0-9a-fA-F]{6}$`、`mask∈[0,100]`、`len(img) ≤ 2MB`；2) 写盘统一改成 temp + `os.replace`。

修复示例：

```python
def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, CONFIG_PATH)      # 原子替换，不会被截断
```

### 中等-4 隐藏光标的『压计数』只在第一次生效，之后无法纠正被抬高的计数

- **位置**：`flipclock_overlay.py:336-349（force_hide_cursor）`
- **描述**：函数注释明确说「别的程序调用过 ShowCursor 会把计数抬上去，所以循环压到 <0」，但循环被 `if not _cursor_hidden:` 包住：一旦 `_cursor_hidden` 已为 True，后续调用只执行 `SetCursor(None)`，不再压计数。于是「我们隐藏 → 别的程序把计数抬起来 → 箭头重新出现」时不会再被纠正，与注释声称的设计意图不一致。
- **修复建议**：把压计数改成幂等操作（每次想隐藏都压到 <0），并在每次进入隐藏态时调用。

修复示例：

```python
def force_hide_cursor():
    global _cursor_hidden
    for _ in range(64):                 # 幂等：每次都压，纠正被抬高的计数
        if user32.ShowCursor(False) < 0:
            break
    _cursor_hidden = True
    user32.SetCursor(None)
```

### 中等-5 命令行阈值不做上下限校验，极小/负值会造成显示抖动

- **位置**：`flipclock_overlay.py:1065-1066（--idle-seconds）、508-514（threshold）`
- **描述**：`--idle-seconds` 是 `float` 且无校验，`threshold()` 直接返回该值。传 `--idle-seconds 0` 或负数时，`tick()` 里 `idle >= threshold` 恒为真 → 立即显示，紧接着又因 `idle < 0.7` 触发隐藏，出现反复显示/隐藏的抖动与日志刷屏。
- **修复建议**：在 `threshold()` 或参数解析处夹到合理区间（例如 ≥1 秒），与 `patch_config` 里已有的 10~86400 秒夹取保持一致。

修复示例：

```python
def threshold(self):
    secs = float(self.args.idle_seconds or self.config.get("idleSeconds") or 0)
    if secs > 0:
        return max(1.0, min(86400.0, secs))     # 与设置页夹取范围一致
    return float(self.config.get("idleMinutes") or 10) * 60.0
```

### 中等-6 超长函数：远超「函数 ≤50 行」的规范

- **位置**：`selftest.py:371（main，249 行）、flipclock_overlay.py:1120+（main，101 行）、tick()、launch_browser()`
- **描述**：按规范统计：`selftest.py main()` 约 249 行、`flipclock_overlay.py main()` 约 101 行、`tick()` 约 58 行、`launch_browser()` 约 53 行、`manual_phase()` 约 60 行、`do_POST()` 约 58 行。大函数里嵌套了「启动→等待→断言→交互」多个阶段，修改其中一步时容易影响其它阶段，阅读与回归成本都高。
- **修复建议**：按阶段拆函数：selftest 把 7 个阶段各自拆成 `phase_idle()/phase_exit()/phase_config()/phase_panel()/phase_cleanup()/phase_manual()`，`main()` 只做编排与汇总；宿主把 `tick()` 拆成 `_tick_visible()/_tick_hidden()`，`show()` 的日志与窗口摆放拆出。

修复示例：

```python
def main(argv=None):
    args = parse_args(argv)
    port = start_host(args)
    try:
        phase_idle(port, args)
        phase_exit(port, args)
        phase_config(port, args)
        phase_panel(port, args)
    finally:
        phase_cleanup(port, args)
        phase_manual(args)
    return summarize()
```

### 中等-7 大量静默吞异常，排障时缺少线索

- **位置**：`flipclock_overlay.py（共 25 处 except Exception）`
- **描述**：全文有 25 处 `except Exception`，其中相当一部分是空实现或只做兜底返回（如 `read_pid()` 返回 0、`profile_browser_pids()` 返回空集、若干 `pass`）。当出现「端口占用/进程枚举失败/profile 被锁」这类环境问题时，日志里没有任何痕迹，只能靠猜。
- **修复建议**：把所有「非预期」的异常路径改成至少 `log()` 一行（带 `repr(exc)`），并区分「预期失败」（如文件不存在，可静默）与「意外失败」（要记录）。

修复示例：

```
try:
    out = subprocess.run([...], capture_output=True, text=True, timeout=30)
    return {int(x) for x in (out.stdout or "").split() if x.strip().isdigit()}
except subprocess.TimeoutExpired:
    log("枚举时钟浏览器进程超时（30s），本次视为无残留")
    return set()
except Exception as exc:
    log("枚举时钟浏览器进程失败:", repr(exc))
    return set()
```

## 🟢 轻微问题（5）

### 轻微-1 Python 函数命名不符合 snake_case 规范

- **位置**：`selftest.py:73、79`
- **描述**：`setIdle()`、`bumpIdle()` 使用了 camelCase，而项目内其它 Python 函数（`wait_until`、`host_log_has`、`manual_phase`…）都是 snake_case。规范要求 Python 方法/函数为 snake_case。
- **修复建议**：重命名为 `set_idle()` / `bump_idle()`（调用点一并改）。

修复示例：

```python
def set_idle(sec):
    with open(IDLE_FILE, "w", encoding="utf-8") as fh:
        fh.write("%.1f" % sec)

def bump_idle(sec, ms=60):
    set_idle(sec)
    time.sleep(ms / 1000.0)
```

### 轻微-2 单文件远超 500 行建议值

- **位置**：`flipclock_overlay.py（1187 行）、index.html（1332 行）、selftest.py（620 行）`
- **描述**：三个主要文件都超过规范建议的 500 行/文件。宿主把 Win32 封装、配置、HTTP、App、自测辅助都放在一个文件里，JS 也把时钟渲染、主题、面板、屏保协议写在一个 `<script>` 里。目前靠注释分区维持可读性，但继续加功能会越来越难定位。
- **修复建议**：宿主按职责拆分（`win32.py` / `http_api.py` / `app.py`），页面脚本拆成 `clock.js` / `theme.js` / `overlay.js`（仍由本地服务提供）。

修复示例：

```python
# flipclock_overlay.py  (入口：CLI + 装配)
#   win32.py    Win32 封装（窗口/DPI/光标/空闲）
#   config.py   配置与主题读写（原子写）
#   httpapi.py  本地 HTTP 控制接口
#   app.py      状态机（显示/隐藏/保持/模式）
```

### 轻微-3 魔法数字散落各处，缺少常量与单位说明

- **位置**：`flipclock_overlay.py（tick/show/hide/HTTP 各处）、index.html:689 等`
- **描述**：如 `0.7`（判定「刚有输入」）、`1.2`（ack 等待）、`3.0`（hold 超时）、`200`（轮询间隔）、`64`（ShowCursor 循环上限）、页面里的 `1000 - Date.now() % 1000 + 4`。同类阈值在宿主与页面各写了一份，改一处容易漏另一处。
- **修复建议**：提为模块常量并注明单位，例如 `INPUT_IDLE_EPS_S = 0.7`、`ACK_TIMEOUT_S = 1.2`、`HOLD_TTL_S = 3.0`、`POLL_INTERVAL_MS = 200`，页面侧用同名常量。

修复示例：

```python
INPUT_IDLE_EPS_S = 0.7      # 空闲归零到「确认有输入」的判定窗口
ACK_TIMEOUT_S = 1.2         # 等页面淡出 ack 的最长时间
HOLD_TTL_S = 3.0            # 页面保持显示的续期超时
POLL_INTERVAL_MS = 200      # 页面轮询 /__state 的间隔
```

### 轻微-4 背景图片值未做协议白名单

- **位置**：`index.html:789（applyTheme）`
- **描述**：`r.setProperty('--bg-img', 'url("' + theme.img + '")')` 直接拼接。虽然 `setProperty` 会校验整条声明值、难以注入新声明（风险低），但 `img` 既可来自本地 `localStorage`，也可来自宿主的 `theme.json`（而 `/__theme` 目前无鉴权），缺少显式的协议白名单。
- **修复建议**：只允许 `data:image/`、`http(s)://` 或相对路径，其余忽略并回退到 `none`。

修复示例：

```javascript
function safeImg(url) {
  if (!url) return '';
  if (/^data:image\//i.test(url) || /^https?:\/\//i.test(url)) return url;
  return '';
}
```

### 轻微-5 启动脚本容错不一致

- **位置**：`settings.bat:9、stop.bat、clean.bat`
- **描述**：`start.bat` 检查了 `python` 是否在 PATH，但 `stop.bat`/`clean.bat` 没有；`settings.bat` 在拿不到地址时硬编码回退 `http://127.0.0.1:8756/`，若 `config.json` 里端口被改成 8757 且宿主未运行，会打开一个错误地址（用户会以为「服务挂了」）。
- **修复建议**：统一在三个脚本里加 python 存在性检查；回退地址改为读取 `config.json` 的 port，或直接提示「宿主未运行，请先 start.bat」。

修复示例：

```text
@echo off
where python >nul 2>nul || (echo [ERROR] python not found in PATH. & pause & exit /b 1)
cd /d "%~dp0"
python "%~dp0flipclock_overlay.py" --stop
```

## ✅ 代码优点（6）

1. 状态机设计克制且考虑了失效场景：`hold`（页面保持显示）带 3 秒服务端超时、淡出用 `transitionend` + 500ms 兜底再回 `ack`、宿主等 ack 有上限——页面崩溃或卡死都不会把屏保钉在屏幕上，也不会「硬切」。
2. 单实例与端口处理很细致：先查 PID 存活、再用 `connect_ex` 探测端口（正确规避了 Windows `SO_REUSEADDR` 导致 bind 探测恒成功的坑），端口被占自动顺延；进程清理按 `--user-data-dir` 精确匹配，不误杀用户自己的 Edge。
3. 把「空闲时间」抽象成可注入来源（`--idle-source`），使端到端自测可以确定性复现，不受「测试者本人在用电脑」影响——这是一个很实用的可测试性设计。
4. 自测覆盖真实链路而非 mock：真启动 kiosk 窗口、真截图做像素断言（顶部是否全黑、数字是否渲染）、真合成键鼠、真检查进程与端口清理，并对环境干扰（用户按住 Alt、高权限窗口丢弃合成输入）明确记为 SKIP 而不是误报失败。
5. 注释与文档解释「为什么」而不只是「做什么」：SO_REUSEADDR 误判、ack 与淡出动画的竞态、预置 profile 抑制 Edge 首启同步弹窗、ShowCursor 计数会被其它程序抬高……后来人不需要重新踩一遍。
6. 页面侧没有任何远程脚本、没有 `eval`，`innerHTML` 只用于自身的静态模板，数字与提示文本一律走 `textContent`，无用户内容注入面（XSS 面很小）。

## 💡 改进建议（3）

### 建议 1（优先级 1）：先堵住本地服务的攻击面（本次 3 个严重项）

这三项是同一个根因（把本地控制服务当成了「只有自己人访问」）：无鉴权、通配 CORS、静态服务根目录过宽。建议一次性重构 `do_GET`/`do_POST`：来源校验 + token 校验 + 文件白名单，三处都在 20 行内可完成。

```python
# do_GET / do_POST 统一入口
def _guard(self):
    if not self._allowed_origin():
        self._text(403, "forbidden"); return False
    if self.command == "POST" and self.headers.get("X-FlipClock-Token") != self.app.token:
        self._text(403, "forbidden"); return False
    return True
```

### 建议 2（优先级 2）：把「写盘」和「杀进程」从关键路径上挪开

配置/主题改原子写；进程清理改异步线程。这两处一个会导致「设置莫名丢失」，一个会导致「动鼠标后屏保要等几秒才退」，都是用户能直接感知的体验问题。

```python
def save_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, path)
```

### 建议 3（优先级 3）：补一份「协议文档」并让自测读它

现在 /__state、/__hold、/__ack、/__exit、/__config、/__theme、/__page 的语义散落在代码与 README 里。建议在 README 里补一张接口表（方法/路径/请求体/语义/鉴权），并让 `selftest.py` 按该表逐项断言，接口变更时测试会立刻发现。

```text
| 方法 | 路径 | 请求体 | 语义 | 鉴权 |
|------|------|--------|------|------|
| GET  | /__state | - | 全量状态（visible/alt/hold/cursor/manual/page） | 同源 |
| POST | /__hold  | {hold:bool} | 页面声明保持显示（3s TTL 续期） | token |
| POST | /__ack   | - | 淡出动画结束，宿主可隐藏窗口 | token |
| POST | /__exit  | - | 请求退出宿主（Esc） | token |
```

> ✅ **已实施（第 3 轮补充）**：接口协议做成**代码里的单一数据源** `flipclock_http.API_CONTRACT`
> （10 条，含 method / path / auth / body / desc），并新增：
> - `GET_ROUTES` / `POST_ROUTES` 路由表——`do_GET` / `do_POST` 只做分发；
> - `validate_contract()`——路由表与协议表必须**完全一致（不多不少）**；
> - `selftest.py` 第 11 阶段——逐项按表断言：`auth=token` 的接口**无 token 必须 403、带 token 必须可用**，
>   `auth=none` 的只读接口必须 200。
>
> README 里的接口表就是这张表的文档副本；新增接口忘了登记、或登记了没实现，自测立即报错。

## 附：验证方法与复现

```text
1) 启动一个不弹窗的宿主做靶子：
   python flipclock_overlay.py --no-browser --dry-run
2) 用 raw socket（避免 HTTP 客户端归一化 URL）逐条验证：
   GET  //C:/Windows/win.ini                  -> 200 OK（任意文件读取）
   GET  /C:\Windows\win.ini                  -> 200 OK
   GET  /../flipclock_overlay.py              -> 404（只挡住了相对穿越）
   GET  /.edge-profile/Default/Preferences    -> 200 OK（profile 暴露）
   POST /__exit   Origin: http://evil.example -> 200 OK（跨站可遥控）
   响应头：Access-Control-Allow-Origin: *
3) 复现完执行 python flipclock_overlay.py --stop 清理。
```

## 附：修复优先级（给 agent 的执行顺序）

1. `do_GET`/`do_POST` 入口加 **Origin/Host 白名单 + token 校验** → 关闭 CSRF（严重项 2）。
2. 静态文件服务改 **白名单**（仅 `index.html` 与需要的 `*.png`）→ 关闭任意文件读取与 profile 暴露（严重项 1、3）。
3. `save_config`/`save_theme` 改 **原子写**；`kill_profile_browser` 改 **异步**。
4. 补校验：请求体上限、`theme` 取值范围、`--idle-seconds` 上下限、`force_hide_cursor` 幂等。
5. 结构调整：拆分超长函数与超长文件，消灭 `except Exception: pass`。

---

# 修复状态（追加记录，不属于原始 HTML 报告）

> 按「一个等级一轮」推进：先修 🔴 严重，回归通过后再动 🟡 中等、🟢 轻微。

## 第 1 轮 · 🔴 严重（3/3 已修复）— 2026-10-15

| # | 问题 | 状态 | 修法 |
| --- | --- | --- | --- |
| 严重-1 | 任意本机文件读取（绝对路径绕过） | ✅ 已修复 | 删除「`normpath` + `join(ROOT, rel)`」拼接逻辑，改为**根目录单文件名白名单**（不递归、不接受子目录/相对路径/盘符路径） |
| 严重-2 | 控制接口无鉴权 + 通配 CORS（CSRF） | ✅ 已修复 | `Host` 白名单 + `Origin` 白名单；改状态接口要求 `X-FlipClock-Token`；`_cors()` 由 `*` 改为**只回显自身来源** |
| 严重-3 | 浏览器 profile 目录被明文暴露 | ✅ 已修复 | 同严重-1：任何子目录（含 `.edge-profile/`）都不再进入服务范围；`config.json` / `theme.json` 也不再对外 |

### 改动文件

- `flipclock_overlay.py`
  - 新增常量：`STATIC_FILES`、`STATIC_PNG_PREFIXES`、`ALLOWED_HOSTS`、`RUNTIME_PATH`
  - 新增函数：`save_runtime()` / `read_runtime()` / `drop_runtime()`
  - `Handler` 新增：`_host_allowed()`、`_origin_allowed()`、`_token_ok()`、`_guard()`、`_static_allowed()`、`_redirect_with_token()`；`_cors()` 改为回显；`do_GET`/`do_POST`/`do_OPTIONS` 接入 `_guard`；`do_POST` 要求 token
  - `--print-url` 输出带 token 的地址（宿主未运行时自动退化为无 token）；启动写 `runtime.json`，`--stop` 与 `shutdown()` 删除
- `index.html`：`api()` 统一附加 `X-FlipClock-Token`
- `selftest.py`：`post()` 附加 token；新增**第 8 阶段「安全回归」14 项**断言（攻击必拒 + 正常链路必通）
- `README.md` / `启动指南.md`：补「安全边界」章节与 403 排错条目

### 验证结果

```
自测结果：68 通过 / 0 失败 / 0 跳过（共 68 项）      # 原 47 项 + 安全回归 14 项 + 计数调整
```

攻击面（全部被拒）：

| 请求 | 修复前 | 修复后 |
| --- | --- | --- |
| `GET //C:/Windows/win.ini` | 200（泄露文件内容） | 404 |
| `GET /C:\Windows\win.ini` | 200 | 404 |
| `GET /../flipclock_overlay.py` | 404 | 404 |
| `GET /.edge-profile/Default/Preferences` | 200（14695 字节） | 404 |
| `GET /config.json` | 200 | 404 |
| `POST /__exit` + `Origin: http://evil.example` | 200（屏保被关） | 403，且不回 CORS 头 |
| 伪造 `Host: evil.example` | 200 | 403 |
| 无 token / 错 token 的 POST | 200 | 403 |

正常链路（未受影响）：`GET /__state` 200、带 token `POST /__hold` 200、页面（带 token）200、无 token 打开 `/` → 302 自动补 token、白名单截图 200、同源请求 CORS 只回显 `http://127.0.0.1:<port>`。

### 行为变化（用户可见）

1. 新增 `runtime.json`（`{pid, port, token}`）：写盘原子替换，停止/退出时删除。
2. `settings.bat` 打开的地址变为 `http://127.0.0.1:8756/?t=<token>`（脚本用 `--print-url` 自动取，无需手工处理）。
3. 手动访问 `http://127.0.0.1:8756/` 会 302 补 token，落点仍能正常用设置页。

### 残余风险

- ✅ **已修复（第 3 轮补充）**：token 曾随 kiosk 窗口标题（`FlipClockOverlay-<token>`）暴露，同机进程可枚举窗口标题读到。
  现在窗口标题改用独立的非敏感 `w=<tag>`（`os.urandom(3).hex()`，见 `App.window_tag`），
  token 只出现在 `?t=`、`runtime.json`、启动日志与设置页地址里；自测断言「窗口标题不含 token」。
- 页面与宿主之间是本机回环上的明文 HTTP（无中间人面，token 仅用于本机鉴权）。

## 第 2 轮 · 🟡 中等（7/7 已修复）— 2026-10-15

| # | 问题 | 状态 | 修法 |
| --- | --- | --- | --- |
| 中等-1 | 监测线程同步跑 PowerShell/taskkill（阻塞最长 30s） | ✅ 已修复 | `maybe_release_browser()` 改为**簿记即返回**：主循环立刻复位 `proc/hwnd`，真正的 terminate + taskkill 丢到 `_release_worker` 线程；用 `_releasing` 标志防止重复起线程 |
| 中等-2 | 请求体大小无上限 | ✅ 已修复 | `do_POST` 统一走 `_read_body()`：超过 `MAX_BODY` 直接 **413** 并丢弃，不再 `read(length)` |
| 中等-3 | `theme` 无取值校验 + 配置写入非原子 | ✅ 已修复 | 新增 `sanitize_theme_patch()`（颜色必须 `#RRGGBB`、`mask` 夹到 0–100、`img` ≤ `MAX_IMAGE_LEN`）；`save_config`/`save_theme`/`save_runtime` 统一走 `write_json_atomic()`（tmp + fsync + `os.replace`） |
| 中等-4 | `force_hide_cursor()` 压计数不幂等 | ✅ 已修复 | 去掉 `if not _cursor_hidden` 前置判断，**每次调用都压到 <0**，可纠正被别的程序抬高的计数 |
| 中等-5 | `--idle-seconds` 无上下限校验 | ✅ 已修复 | `threshold()` 统一夹到 `[MIN_IDLE_SECONDS, MAX_IDLE_SECONDS]` = [10, 86400]，命令行与设置页走同一套 |
| 中等-6 | 超长函数 | ✅ 已修复 | 宿主：`tick()` → `_tick_visible()`/`_tick_hidden()`/`_hold_label()`，`launch_browser()` → `browser_command()`/`wait_for_window()`，`do_POST()` → 6 个 `_post_*` 路由表，`main()` → `cmd_print_url()`/`cmd_stop()`/`prepare_app()`/`serve_until_exit()`/`start_workers()`/`log_startup_banner()`/`write_pid_file()`。自测：`main()` → 9 个 `phase_*()` + `summarize()`/`restore_env()`，`security_phase()` → `security_attacks()`/`security_allowed()`，`manual_phase()` → `manual_cursor_checks()`，`phase_panel()` → `open_panel_with_alt()`。**现在两个文件都没有超过 50 行的函数** |
| 中等-7 | 25 处 `except Exception` 静默吞异常 | ✅ 已修复 | 逐处区分「预期失败」与「意外失败」：`read_pid`/`alt_down`/`profile_browser_pids`/`idle`(注入文件)/`taskkill`/`shutdown`/`patch_config` 等补上 `log(...repr(exc))` 或收窄异常类型（`FileNotFoundError`、`(OSError, ValueError)`、`(TypeError, ValueError)`）；纯清理路径保留静默但收窄类型 |

### 改动文件（第 2 轮）

- `flipclock_overlay.py`：新增 `MAX_BODY`/`MAX_IMAGE_LEN`/`MIN_IDLE_SECONDS`/`MAX_IDLE_SECONDS`/`HEX_COLOR_RE`/`INPUT_IDLE_EPS_S`/`HOLD_TTL_S`/`ACK_TIMEOUT_S`/`MANUAL_CURSOR_IDLE_S`/`LAUNCH_RETRY_S`/`CURSOR_SHOWCURSOR_MAX` 常量；新增 `write_json_atomic()`、`sanitize_theme_patch()`、`check_limits_consistent()`、`_read_body()`、`_json_body()`、`_post_*()`、`browser_command()`、`wait_for_window()`、`_tick_visible()`、`_tick_hidden()`、`_hold_label()`、`_release_worker()`、`cmd_print_url()`、`cmd_stop()`、`prepare_app()`、`serve_until_exit()`、`start_workers()`、`log_startup_banner()`、`write_pid_file()`
- `selftest.py`：`main()` 拆成阶段函数；函数名 `setIdle`/`bumpIdle` → `set_idle`/`bump_idle`（顺带清掉轻微-1）；新增**第 9 阶段「健壮性回归」**
- 文档：`README.md` / `启动指南.md` 同步

### 验证结果

```
自测结果：88 通过 / 0 失败 / 0 跳过（共 88 项）      # 第 1 轮 68 项 + 第 2 轮新增 20 项
```

新增断言（第 8/9 阶段）：

| 检查 | 结果 |
| --- | --- |
| 命令行 `--idle-seconds 3`（低于下限）→ 夹到 10 秒 | PASS |
| 设置页 `idleSeconds=5` → 夹到 10 秒；`999999` → 夹到 86400 秒 | PASS |
| 非法 `idleSeconds:"abc"` 被忽略且不 500 | PASS |
| 坏 JSON → 400；JSON 类型不对 → 按空对象 200 | PASS |
| `config.json` 始终是完整 JSON、无 `.json.tmp` 残渣 | PASS |
| 超过 `MAX_BODY` 的请求体 → 413 | PASS |
| 超长 `img`（> `MAX_IMAGE_LEN`）被清洗忽略、不落盘 | PASS |
| 非法颜色 `not-a-color` / `rgb(1,2,3)` 不被写入；`mask:999` 夹到 100 | PASS |
| `MAX_BODY > MAX_IMAGE_LEN`（保证换背景图不被 413） | PASS |
| 256KB 背景图 dataURL 正常提交（换图可用） | PASS |
| 请求退出后宿主正常结束（未卡在清理上） | PASS |

按需启动（`keepBrowser=false`）实测异步清理，退出时状态机未被阻塞：

```
退出耗时: 0.5s | 状态响应采样 5 次 | 最长一次间隔 0.13s
宿主日志: 🧹 按需启动模式：结束时钟浏览器进程（后台执行，不阻塞监测）
残留时钟浏览器进程: 0
```

### 过程中发现并修掉的 2 个自引入缺陷

1. **手动模式指针不再自动隐藏**：把 `tick()` 拆成 `_tick_visible()`/`_tick_hidden()` 后，手动模式分支提前 `return`，导致指针维护代码不再执行（自测第 7 阶段立刻报 3 项 FAIL）。已改为在手动分支内显式调用 `set_cursor(...)`。
2. **`MAX_BODY` 卡太紧会让「换背景图」直接 413**：最初取 256KB，而设置页把本地图片以 dataURL 整张 POST（`MAX_IMAGE_LEN` = 2MB），192KB 以上的图就会被拒。已把 `MAX_BODY` 提到 4MB，并加 `check_limits_consistent()` 启动自检 + 断言防回归。

## 第 3 轮 · 🟢 轻微（5/5 已修复）— 2026-10-15

| # | 问题 | 状态 | 修法 |
| --- | --- | --- | --- |
| 轻微-1 | `setIdle`/`bumpIdle` 命名不合 snake_case | ✅ 已修复 | 改名 `set_idle`/`bump_idle`（第 2 轮顺带完成） |
| 轻微-2 | 单文件超长（宿主 1469 行 > 500） | ✅ 已修复 | 按职责拆成 6 个模块，**每个文件都 ≤ 500 行**；入口 `flipclock_overlay.py` 保留 `__all__` 再导出，`import flipclock_overlay as fc` 完全向后兼容 |
| 轻微-3 | 魔法数字散落（页面侧） | ✅ 已修复 | 页面文件头集中 10 个时间常量（`FADE_IN_MS`/`FADE_OUT_MS`/`ACK_FALLBACK_MS`/`POLL_INTERVAL_MS`/`POLL_RETRY_MS`/`CONFIG_POLL_MS`/`CONFIG_RETRY_MS`/`HOLD_RENEW_MS`/`CURSOR_HIDE_DELAY_MS`/`SECOND_SLACK_MS`），替换掉散落的 `500/200/1000/3000/1500/360/600/320/4` |
| 轻微-4 | 背景图值缺协议白名单 | ✅ 已修复 | 新增 `safeImgUrl()`：只放行 `data:image/`、`http(s)://`、相对路径；`javascript:`、`data:text/html`、`file:`、带引号括号的注入串一律回退成 `none` |
| 轻微-5 | `settings.bat` 硬编码端口、`stop.bat`/`clean.bat` 未检查 python | ✅ 已修复 | 四个脚本统一 `where python` 检查；`settings.bat` 改为「拿不到地址就报错并提示先 `start.bat`」，不再硬编码 `127.0.0.1:8756`；`clean.bat` 顺带清理 `runtime.json`/`overlay.pid` |

### 模块拆分结果（轻微-2）

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `flipclock_common.py` | 90 | 路径 / 常量 / 日志 |
| `flipclock_win32.py` | 311 | Win32 封装（窗口、DPI、光标、空闲、进程） |
| `flipclock_config.py` | 193 | config / theme / runtime 读写与取值校验 |
| `flipclock_browser.py` | 63 | 时钟浏览器进程的查找与清理 |
| `flipclock_http.py` | 296 | 本地 HTTP 控制接口 + 端口工具 |
| `flipclock_app.py` | 477 | App 状态机 |
| `flipclock_overlay.py` | 246 | 入口：CLI + `__all__` 再导出（向后兼容） |

### 验证结果

```
自测结果：102 通过 / 0 失败 / 0 跳过（共 102 项）   # 第 2 轮 88 项 + 第 3 轮新增 14 项
```

新增断言（第 10 阶段「结构回归」，纯静态、跑得快）：

| 检查 | 结果 |
| --- | --- |
| 入口再导出 22 个旧名字（`import flipclock_overlay as fc` 兼容） | PASS |
| `__all__` 里声明的名字都真实存在 | PASS |
| 拆出的每个模块都 ≤ 500 行 | PASS |
| 页面 10 个时间常量已集中在文件头 | PASS |
| 页面有 `safeImgUrl` 且 `applyTheme` 经由它取值 | PASS |
| 4 个启动脚本都有 `where python` 检查、都不硬编码端口 | PASS |

页面白名单另做了 11 组单元验证（`data:image/`、`http(s)://`、相对路径通过；`javascript:`、`data:text/html`、`file:`、CSS 注入串、空值、`null` 全部拦下）。

### 过程中发现并修掉的 3 个问题

1. **拆分后入口缺 `ThreadingHTTPServer` 导入**：`serve_until_exit()` 用到它但没导入，`py_compile` 发现不了（运行时才 NameError）。已补 `from http.server import ThreadingHTTPServer`。
2. **入口漏再导出 `MAX_IDLE_SECONDS`**：`__all__` 里声明了却因 import 行漏写而拿不到，第 9 阶段直接抛 `AttributeError`。已修复，并加断言「`__all__` 声明的名字必须真实存在」防复发。
3. **超大体量请求会触发 TCP RST**：原来「回 413 后立刻关连接」，客户端还在发数据就收到 `ConnectionResetError`。已改为先分块丢弃请求体（上限 `MAX_DRAIN_BYTES`）再回 413。
   另外修了自测自身的一个竞态：上一实例的 `runtime.json` 未及时删除时会读到**旧 token**（导致后续带 token 请求全 403），新增 `wait_runtime_for(proc)` 按 pid 比对。

---

# 第 4 轮 · 建议 3 + 残余风险（2/2 已闭环）— 2026-10-15

15 个问题项（3 严重 / 7 中等 / 5 轻微）在第 1–3 轮全部修完后，本轮清掉了报告里**两处明确留白**：

| 项 | 状态 | 修法 |
| --- | --- | --- |
| 第 1 轮「残余风险」：token 随窗口标题暴露 | ✅ 已闭环 | 窗口标题改用独立的非敏感 `w=<tag>`（`App.window_tag = os.urandom(3).hex()`），页面 `document.title` 只在 `WINDOW_TAG` 存在时设置；token 仍只走 `?t=` / `runtime.json` / 日志 / 设置页地址。新增断言「窗口标题不含 token」 |
| 建议 3：补协议文档并让自测读它 | ✅ 已闭环 | 见上方「已实施」——`API_CONTRACT` 单一数据源 + 路由表 + `validate_contract()` + 自测第 11 阶段；README 增补接口协议表 |

### 验证结果（第 4 轮）

```
自测结果：124 通过 / 0 失败 / 0 跳过（共 124 项）   # 第 3 轮 102 项 + 本轮新增 22 项
```

| 新增检查 | 结果 |
| --- | --- |
| 窗口标题不含 token | PASS |
| 页面 `document.title` 用 `WINDOW_TAG` 而非 `TOKEN` | PASS |
| 路由表与 `API_CONTRACT` 完全一致（不多不少） | PASS |
| 协议表覆盖全部 10 个控制接口、每条字段齐全 | PASS |
| 4 个 `GET` 只读接口均 200 | PASS |
| 6 个 `POST` 接口：无 token → 403 | PASS |
| 6 个 `POST` 接口：带 token → 可用（非 403/404） | PASS |

### 最终状态

三轮修复 + 本轮闭环后，报告中的 **3 严重 / 7 中等 / 5 轻微全部已修复**，
2 处留白（残余风险、建议 3）也已闭环，未勾选待办为 **0**。

代码结构：宿主拆成 7 个模块（每个 ≤ 500 行，入口保留 `__all__` 向后兼容），
自测 11 个阶段 124 项全绿。



