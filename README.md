# Flip Clock · 翻页时钟屏保

一个 Windows 上的**全屏翻页时钟**，带**系统级空闲屏保**功能：电脑空闲到设定时间，
自动全屏跳出翻页时钟；动一下鼠标或键盘就淡出回桌面。

纯 Python 标准库 + 单文件网页，**零第三方依赖**，不需要 `pip install` 任何东西。

> ⚠️ 仅支持 **Windows**（依赖 `GetLastInputInfo` / `SetWindowPos` 等 Win32 API）。

---

## 效果预览

| 默认效果 | 全屏跳出 |
| --- | --- |
| ![默认](previews/preview-default.png) | ![全屏](previews/preview-fullscreen.png) |

| 设置面板（改配色 / 空闲时长） | 背景图片 + 蒙版 |
| --- | --- |
| ![设置](previews/preview-settings.png) | ![背景图](previews/preview-bgimage.png) |

| 屏保内按住 Alt 出现齿轮与提示条 | 屏保内直接改配色（松开 Alt 也不退） |
| --- | --- |
| ![Alt 菜单](previews/preview-alt-menu.png) | ![屏保内设置](previews/preview-overlay-panel.png) |

---

## 特性

**时钟本身**

- 真 3D 翻页动画（`perspective` + `preserve-3d`，四层卡片结构：上下静片 + 上下翻页片）
- `HH:MM:SS` 六位数字 + 两个常亮冒号，细字重等宽数字
- 配色可调：背景（纯色 / 本地图片 / 图片 URL + 蒙版）、卡片、数字
- 背景图与卡片明暗、冒号、中缝全部由一个基色自动推导

**空闲屏保**

- 用 `GetLastInputInfo` 读**系统级**空闲时间，任意鼠标/键盘输入都会重置
- 到点自动全屏跳出；有输入立刻淡出（先淡出、再隐藏窗口，不会硬切）
- 全屏采用 `msedge --kiosk`，**真·无边框**：没有标题栏、最小化/关闭按钮，也没有白边
- 跳出时自动隐藏鼠标指针，动一下鼠标临时显示
- 支持多屏选择：鼠标所在屏幕 / 主屏幕 / 全部屏幕

**两种模式**

| 模式 | 说明 |
| --- | --- |
| **屏保模式** | 空闲到点自动跳出（默认 10 分钟），有输入自动退出 |
| **手动模式** | 立即全屏显示，不监控空闲、不因鼠标移动退出，按 `Esc` 退出 |

**屏保界面里还能改设置**：屏保全屏时按住 `Alt`，右上角出现齿轮 →
点开设置面板后**可以松开 `Alt`**，只要鼠标还在面板内屏保就不退出，改配色能实时预览；
鼠标移出面板即刻退出。

**安全**：本地 HTTP 控制通道加了 Host / Origin 白名单、运行期 token 鉴权、
静态文件白名单、请求体上限（详见 [docs/README.md](docs/README.md#安全边界本机服务只服务于本机页面)）。

---

## 📱 在 iPhone / iPad 上使用（网页版，不装 App）

同一个 `app/index.html` 就是网页版：**没有任何依赖，打开就是一只全屏翻页时钟**，
支持「添加到主屏幕」后像原生 App 一样独立全屏运行，**并且没有「几分钟不操作自动跳出」**。

三种用法，任选其一：

| 方式 | 适合 | 需要 |
| --- | --- | --- |
| **A. GitHub Pages（推荐）** | 手机随时可用，离线也能开 | 一次部署，之后一劳永逸 |
| **B. 局域网** | 不想发布到公网 | 电脑和手机连同一个 Wi-Fi |
| **C. 直接把 HTML 传到手机** | 完全不联网 | 用「文件」App 打开即可 |

**方式 A：发布到 GitHub Pages（推荐）**

仓库已自带工作流 `.github/workflows/deploy-pages.yml`，只要在仓库
`Settings → Pages → Source` 选 **GitHub Actions**，之后往 `main` 推代码就会自动发布。
发布后地址形如：

```
https://2672254297-dot.github.io/flip-clock/
```

手机上：**Safari 打开该地址 → 点「分享」→「添加到主屏幕」**。
之后从主屏幕图标启动即为全屏、无地址栏；Service Worker 会把页面缓存下来，
**飞行模式下也能打开**。

**方式 B：局域网（不用公网）**

```bat
python tools\serve-web.py
```

脚本会打印形如 `http://192.168.1.5:8080` 的地址，在手机 Safari 里打开即可，
同样可以「添加到主屏幕」。注意：局域网 http 不是安全上下文，离线缓存不会启用，
且关掉脚本后手机就打不开了。

**方式 C：把 HTML 传到手机**

把 `app/index.html` 单独传到手机（AirDrop / iCloud / 微信文件传输），用 Safari 打开就是时钟。
此方式图标与离线缓存不可用（`manifest` 与 `sw.js` 是独立文件），但时钟功能完整。

**iOS 上专门做的适配**

- `viewport-fit=cover` + `env(safe-area-inset-*)`：内容避开刘海 / 灵动岛 / 底部横条
- 卡片尺寸由**可用宽与可用高共同决定**，iPhone 竖屏、横屏、iPad 各尺寸都不溢出
- 用 `dvh` / `dvw` 动态视口单位，Safari 地址栏收放时尺寸不跳
- 禁用橡皮筋滚动、长按选择、双击缩放，手感接近原生 App
- 触屏设备上齿轮按钮常驻（半透明），点击区域 ≥44px
- **屏幕常亮**：用 Screen Wake Lock 让手机/平板当桌面时钟时不自动熄屏
  （iOS 16.4+ 支持；不支持时静默跳过）
- 尊重系统「减弱动态效果」设置

---

## 环境要求

- Windows 10 / 11（**屏保功能**需要；只当网页时钟用的话任何平台都行）
- Python 3（[python.org](https://www.python.org/downloads/) 安装时勾选 *Add python.exe to PATH*）
- Microsoft Edge 或 Google Chrome（自动探测常见安装路径）

---

## 快速开始

1. 下载或克隆本仓库
   ```bat
   git clone https://github.com/2672254297-dot/flip-clock.git
   ```
2. 双击 **`start.bat`** 启动屏保宿主（想后台无窗口运行就用 `start-silent.vbs`）
   - 控制台会打印设置页地址、空闲阈值、窗口模式；
   - 首次启动会在后台预热一个全屏窗口（藏在屏幕外），所以第一次会花十几秒。
3. 双击 **`settings.bat`** 打开设置页，调整配色、空闲时长、显示屏幕。
4. 之后就不用管了：空闲到点自动跳出，一动鼠标/键盘立刻淡出。

**只想现在就看看效果**：双击 **`clock.bat`** —— 立即全屏显示，按 `Esc` 退出。

**想开机自启**：按 `Win+R` 输入 `shell:startup`，把 `start-silent.vbs` 的**快捷方式**放进去。

**停止**：双击 **`stop.bat`**；想顺带清掉浏览器缓存就用 **`clean.bat`**。

---

## 命令行参数

```
python app\flipclock_overlay.py [选项]

--manual             手动模式：立即全屏显示，不监听空闲、不自动退出（Esc 退出）
--idle-seconds S     按秒设置空闲阈值（临时覆盖，不写入 config.json）
--idle-minutes N     按分钟设置空闲阈值（覆盖 config.json）
--monitor MODE       cursor（鼠标所在屏幕，默认）| primary | all
--window-mode MODE   kiosk（默认，真·无边框全屏）| app（普通应用窗口，备用）
--keep-browser       常驻预热浏览器（默认，跳出 0 延迟）
--on-demand          按需启动：平时不占内存，退出即彻底结束浏览器
--port N             本地服务端口（默认 8756，被占用自动顺延）
--browser PATH       指定浏览器可执行文件路径
--no-browser         只跑本地服务，不启动时钟窗口（调试用）
--dry-run            只跑状态机，不真的显示/隐藏窗口（调试用）
--no-hide-cursor     全屏时不隐藏鼠标指针
--print-url          打印（带本次 token 的）设置页地址后退出
--stop               停止正在运行的实例并清理时钟浏览器
```

`app/config.json` 字段与之一一对应，首次启动自动生成：

```json
{
  "enabled": true,          // 总开关
  "idleSeconds": 600,       // 空闲阈值（秒）；>0 时以它为准
  "idleMinutes": 10,        // 旧字段，仅当 idleSeconds<=0 时回退
  "monitor": "cursor",      // cursor | primary | all
  "port": 8756,
  "browser": "",            // 留空 = 自动找 Edge / Chrome
  "windowMode": "kiosk",    // kiosk | app
  "keepBrowser": true,      // true = 常驻预热；false = 按需启动
  "prewarmSeconds": 20      // 按需启动时提前多少秒拉起浏览器
}
```

---

## 目录结构

```
flip-clock\
├── README.md            本文件（GitHub 落地页）
├── LICENSE              MIT
├── 启动指南.md          上手速查（启动 / 测试 / 排错）
├── start.bat            启动项：屏保模式（带日志窗口）
├── start-silent.vbs     启动项：屏保模式（后台无窗口）
├── clock.bat            启动项：手动模式（立即全屏）
├── clock-silent.vbs     启动项：手动模式（后台无窗口）
├── settings.bat         启动项：打开设置页
├── stop.bat             结束项：停止服务
├── clean.bat            结束项：停止服务并清缓存
├── app\                 程序本体 + 网页版（同一份 index.html）
│   ├── flipclock_overlay.py   入口：CLI + 再导出
│   ├── flipclock_app.py       状态机 App（空闲判定 / 显示隐藏 / 保持 / 指针）
│   ├── flipclock_http.py      本地 HTTP 控制接口 + 端口工具
│   ├── flipclock_config.py    config / theme / runtime 读写（原子写）
│   ├── flipclock_win32.py     Win32 封装（窗口 / DPI / 光标 / 空闲 / 进程）
│   ├── flipclock_browser.py   浏览器进程查找与清理
│   ├── flipclock_common.py    路径、常量、日志
│   ├── index.html             时钟本体（HTML/CSS/JS 单文件，桌面 + iOS 通用）
│   ├── manifest.webmanifest   PWA 清单（添加到主屏幕）
│   ├── sw.js                  Service Worker（离线可用）
│   ├── apple-touch-icon.png   iOS 主屏幕图标
│   ├── icon-192.png / icon-512.png / icon-maskable-512.png
│   ├── config.json            运行参数（自动生成）
│   └── theme.json             配色（自动生成）
├── docs\                详细文档与代码审查报告
├── previews\            效果截图
├── tools\               辅助脚本（图标生成 / 局域网服务 / iOS 布局检查）
├── tests\               端到端自测（selftest.py）+ 30 秒测试脚本
└── .github\workflows\   GitHub Pages 自动发布
```

**不要单独移动 `app\` 里的文件**——移动后要同步修改根目录启动脚本里的 `app\flipclock_overlay.py` 路径。

---

## 自测

`tests\selftest.py` 是一套端到端自测，会**真的把屏保全屏跳出来**并逐项断言
（全屏铺满 / 置顶 / 无白边 / 指针隐藏 / 有输入就退出 / 面板保持 / 安全回归 / 接口协议……），
共 **11 个阶段 144 项**：

```bat
python tests\selftest.py            :: 冷启动 + 完整回归
python tests\selftest.py --warm     :: 保留浏览器 profile，启动更快
python tests\selftest.py --no-shot  :: 不截图
```

它会自动还原 `config.json`，并把截图与宿主日志留在 `tests\` 下。
只想快速验证「空闲 30 秒跳出」可以用 `tests\test-30s.bat`。

---

## 已知限制

- **「空闲跳出」是 Windows 专属**：它靠 `GetLastInputInfo` + 窗口置顶实现。
  网页版 / iOS 上没有宿主进程，因此**不存在自动跳出**，就是一只纯时钟（这也是在手机上想要的形态）。
- **不是真正的 `.scr` 屏保程序**：本质是一个 kiosk 浏览器窗口 + Python 宿主，
  任务管理器里能看到 `msedge.exe`（用项目专属 profile，与你自己的浏览器完全隔离）。
- 屏保**不锁定**电脑，也不做密码保护，只负责显示。
- 空闲判定是系统级的，所以在别的程序里动鼠标也会退出——这是刻意设计。
- 淡入淡出靠页面透明度实现（Windows 不支持窗口级逐像素透明），
  所以时序是「页面先淡出 → 宿主再隐藏窗口」。
- `monitor: all` 是把窗口铺满整个虚拟桌面（跨屏一张大画面），不是每块屏各一份时钟。
- **iOS 上没有原生 App**：iPhone / iPad 只支持 PWA 形式（添加到主屏幕）。
  真机 App 需要 macOS + Xcode 打包签名，本仓库不提供 `.ipa`。
- iOS 的「屏幕常亮」依赖 Screen Wake Lock（iOS 16.4+）；低电量模式下系统会强制熄屏。
- iPhone 竖屏是**单行** `HH:MM:SS`，卡片按屏宽自适应（约 50px 宽）；
  想要更大的数字请用横屏或 iPad。

---

## 开源协议

[MIT](LICENSE) © 2026 Liu
