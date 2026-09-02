# 📺 ATV Remote — Android TV / Apple TV 遥控器（支持键盘输入）

仿 atvremote 的本地遥控器：网页 UI（Mac/手机浏览器都能用），同时支持 **Android TV（adb）** 和 **Apple TV（pyatv / MediaRemote 协议，与 iOS「遥控器」App 同款）**。

## 功能总览

| 功能 | 🤖 Android TV | 🍎 Apple TV |
|------|:---:|:---:|
| 方向键 D-pad | ✅ | ✅ |
| 键盘打字发送到电视 | ✅（中文需一键启用 ADBKeyboard） | ✅ **支持中文** |
| 全局键盘遥控（方向键/回车/Esc…） | ✅ | ✅ |
| 触摸板（点击/滑动） | ✅（映射屏幕坐标） | ✅（触控手势） |
| 返回/主页/菜单/播放控制 | ✅ | ✅ |
| 电源/唤醒 | ✅ | ✅（开关机） |
| 应用启动 | ✅（预设+自定义包名） | ✅（在线应用列表） |
| 画面获取 | ✅ 真实截屏 | ⚠️ 正在播放内容画面 |
| 音量 | ✅ | ⚠️ 需额外 AirPlay 配对（见 FAQ） |
| 设备发现 | 手动输 IP | ✅ 局域网自动扫描 |

## 使用

### Mac 端（一次安装，之后零操作）

**遥控器 App（推荐，不用浏览器）**：已安装到 `/Applications/ATVRemote.app`——点开就是遥控器窗口（原生 App，加载本地服务）。可拖进 Dock 常驻；想开机自动弹出：系统设置 → 通用 → 登录项 → 添加 ATVRemote。

首次部署（换新机器时）：

```bash
cd ~/atv-remote
python3 -m venv .venv && .venv/bin/pip install pyatv   # 首次（Apple TV 支持）
bash mac-install.sh                                    # 后台服务装成开机自启（崩溃自动重启）
cd mac && swiftc -O -target arm64-apple-macos26.0 -o ATVRemote main.swift -framework Cocoa -framework WebKit
# 再把 mac/ATVRemote.app 拷到 /Applications（注意：本机 CLT 的 SDK 默认 minos 28.0 高于系统版本，
# 会报 open -10825，必须显式 -target arm64-apple-macos26.0）
```

后台服务（`http://127.0.0.1:8300`）由 LaunchAgent 常驻，浏览器仍可访问。取消自启：`launchctl unload ~/Library/LaunchAgents/com.atv.remote.plist`。

### 图标

Mac 与 Android 共用一套设计（深色底 + 蓝色电视 + D-pad 徽章）。重新生成：

```bash
cd ~/atv-remote
swift make_icon.swift        # 生成 mac/AppIcon_1024.png 和 mac/ic_launcher_fg_432.png
# Mac: 见上文 mac/ 下的 iconset→iconutil 流程（产物已在 mac/ 下）
# Android: 重新跑 android/build.sh 即可
```

### 手机（3 步变独立遥控器，之后不需要 Mac）

打开 Mac 上的遥控器网页，底部有「📱 把遥控器装到手机」卡片：

1. 手机浏览器打开同一页面 → 下载 `ATVRemote.apk` → 安装
2. 安装 [Termux（F-Droid 版）](https://f-droid.org/packages/com.termux/) 并打开一次
3. Termux 里粘贴卡片中那行 `curl ... | bash` 命令回车，自动装完（3-8 分钟，会同步 Mac 上的配对记录）

之后打开 ATVRemote App 点「🚀 独立模式」即可。**微信/QQ 传文件给手机无法直接装 APK？** 用手机浏览器直接访问 Mac 页面下载即可。

### 🤖 Android TV 连接（一次性）

1. 电视：设置 → 设备偏好设置 → 关于 → 连点「版本号」7 次开启开发者模式
2. 开发者选项 → 打开「网络调试 / ADB 调试（网络）」
3. 网页输入电视 IP → 连接 → 首次在电视上点「允许 USB 调试」

### 🍎 Apple TV 连接（一次性）

1. 网页切到「Apple TV」页签 → 点「🔍 扫描局域网 Apple TV」
2. 点扫描结果中的「配对」→ 电视屏幕会显示 4 位 PIN 码
3. 在网页输入 PIN → 「完成配对」→ 自动连接
4. 之后重启会自动恢复连接（凭据保存在 `state.json`）

支持两种协议（自动选择）：**tvOS 16+ 的 Apple TV 走 Companion 协议**（新版 tvOS 不再广播 MRP），旧款走 MRP；Mac/HomePod/第三方电视会被自动过滤。要求与本机同一网段，Apple TV 3 及更早不支持。

### ⌨ 键盘输入（核心）

**打字发送**：在「键盘输入」框打字 → 回车或「发送」→ 文字直接上电视。
（Apple TV 需要电视端有聚焦的输入框，状态栏会显示「输入框已聚焦」徽标；支持中文）

**Android TV 输中文**：`adb shell input text` 只认 ASCII，中文会被静默丢弃，
必须借道第三方输入法 ADBKeyboard —— 页面里点一下「中文键盘 → 启用」即自动完成，只需一次：

| 状态显示 | 含义 | 怎么办 |
|---------|------|-------|
| 电视上未安装 | 还没装 ADBKeyboard | 卡片里给出了 APK 直链（Android 16 必须用 v2.5-dev），在电视浏览器打开装一次 |
| 已安装 · 未切换 | 装了但当前不是它 | 点「启用」 |
| 已启用 · 可输中文 | 就绪 | 直接打中文 / Emoji |

切换后电视可能弹一次「选择输入法」确认框，用遥控器点「确定」。不用了点「切回系统」即可还原。
启用后输入框右下角会多两个按钮：**清空电视输入框**、**电视搜索键**（比发回车更能命中搜索框）。

**全局键盘遥控**（点一下页面空白处后生效）：

| 键盘 | 电视动作 |
|------|---------|
| 方向键 ↑↓←→ | D-pad 移动 |
| 回车 | OK / 确定 |
| Esc | 返回 |
| 退格 | 删除（仅 Android） |
| Home | 主页 |
| PageUp / PageDown | 翻页（仅 Android） |
| 媒体键 | 播放/暂停/上一曲/下一曲 |
| 音量键 | 音量 +/−/静音 |

### 手机 App（Android APK）

`android/ATVRemote.apk` — WebView 壳 App，装到手机上直接当遥控器用：

1. Mac 上启动服务（`python3 server.py`，默认已开放局域网；当前已后台运行）
2. 把 `android/ATVRemote.apk` 传到手机（微信/AirDroid/USB 均可），点击安装（允许"未知来源"）
3. 首次打开填 Mac 地址（App 已预填构建时的默认值，改成你自己的）→ 连接，之后自动记住
4. 点「键盘输入」框会直接调起手机输入法，打字（含中文发 Apple TV）回车即上电视

重新构建 APK：`./android/build.sh`（无需 Gradle，用 aapt2+d8+apksigner 手工链；注意 resources.arsc 必须未压缩存储，脚本已处理）

### 📱 原生安卓 App（v1.1.0 新增，推荐）

`ATVRemote-native.apk`（约 33MB）——**Python 引擎直接内嵌**（Chaquopy），安装即用、零配置，无需 Mac 也无需 Termux：

- 打开 App → 自动启动内置引擎 → 直接进入遥控器
- Apple TV 的扫描/配对/键盘输入（含中文）/应用启动全部内置；Android TV 因手机沙箱无 adb 二进制不可用（原生版主打 Apple TV）
- 从 [Releases](https://github.com/cpufreestyle/atv-remote/releases) 下载安装即可

重新构建：

```bash
cd android-native
gradle assembleRelease   # 需要 JDK 17 + Android SDK + Python 3.10（Chaquopy 构建要求）
# 产物: app/build/outputs/apk/release/app-release.apk
```

> 构建踩坑记录：Gradle 需 8.x（9.x 与 AGP 8.x 不兼容）；Chaquopy 17 的 pip 自动回溯到 pyatv 0.13.2（cryptography 42 有官方 Android 预编译 wheel）；`chacha20poly1305_reuseable` 继承 Rust 类在 Chaquopy 下不可继承，已由 `app/src/main/python/boot.py` 注入组合式 shim 解决；旧版 pyatv 无 `touch` 接口，触摸板操作自动降级提示；Android 模拟器 NAT 不转发 mDNS，Apple TV 扫描需真机验证。

### 手机独立运行（不需要 Mac）

App 有「🚀 独立模式」：手机内的 Termux 引擎直连电视（adb 和 Apple TV 协议都是纯 TCP）。安装方式见上文「手机（3 步…）」——网页底部的手机安装卡片会给出全部链接和一键命令。

引擎排错：Termux 里跑 `~/atv-remote/start.sh` 看输出，日志在 `~/atv-remote/server.log`。

> 为什么不把 Python 引擎直接打包进 APK？pyatv 依赖 cryptography/pydantic-core 等 Rust 原生库，无法在 Android 上现成交叉编译（Chaquopy 无预编译），重写协议工程量大。Termux 方案零重写、依赖齐全。

## 命令行参数

```
python3 server.py [--host 127.0.0.1] [--port 8300] [--adb adb路径] [--no-open] [--token 令牌]
```

用 `.venv/bin/python server.py` 启动会加载 Apple TV 支持（pyatv）；直接 `python3 server.py` 时 Apple TV 功能自动禁用、Android 照常可用。

### 🔒 局域网访问令牌（可选）

默认**不鉴权**（与历史版本一致）。服务默认监听 `0.0.0.0`，意味着同一网段的任何人都能对你的电视发 `input text` / `monkey` 命令，还能拖走 `/bundle.tgz` 里的 Apple TV 配对凭据。在共享网络、公司网络或租房宽带下建议开启：

```bash
python3 server.py --token 你的令牌       # 也可用环境变量 ATV_TOKEN
```

开启后：

- **本机（`127.0.0.1` / `::1`）免令牌**，本机浏览器和 Mac App 用法不变；
- 局域网设备访问 `/` 会看到登录页，输入令牌即可（成功后种 cookie，后续请求自动带上）；
- 脚本 / App 调用用 `X-ATV-Token` 头，或在 URL 后加 `?token=<令牌>`：
  ```bash
  curl -H 'X-ATV-Token: 你的令牌' -X POST -d '{"type":"key","code":19}' http://192.168.1.5:8300/api/cmd
  ```
- 页面上的 Termux 安装命令、APK 直链、二维码会自动带上令牌，手机装引擎的流程不受影响。

## HTTP API（curl 可直接用）

```
GET  /api/status                          # 连接状态（两种设备）
POST /api/connect   {"target":"192.168.1.50:5555"}        # Android 连接
POST /api/cmd       {"type":"key","code":19}               # 按键（两种设备通用）
POST /api/cmd       {"type":"text","text":"hello","enter":true}
POST /api/cmd       {"type":"tap"...} / {"type":"swipe"...}
POST /api/cmd       {"type":"app","pkg":"com.netflix.ninja"}   # Android 包名 / Apple TV bundle id
POST /api/cmd       {"type":"clear"}                  # 清空电视输入框（需 ADBKeyboard）
POST /api/cmd       {"type":"editor","code":3}        # 触发 IME 动作：3=搜索 2=前往 6=完成
POST /api/ime       {"action":"status"}               # ADBKeyboard 三态：installed/enabled/current
POST /api/ime       {"action":"enable"}               # 启用并切到 ADBKeyboard
POST /api/ime       {"action":"reset"}                # 切回电视系统输入法
GET  /api/screenshot                      # Android 截屏 / Apple TV 播放画面
POST /api/atv/scan   {}                   # 扫描 Apple TV
POST /api/atv/pair   {"action":"begin","id","ip","name"}   # 开始配对（电视显示 PIN）
POST /api/atv/pair   {"action":"finish","pin":"1234",...}  # 完成配对
POST /api/atv/connect {"id","ip","name"}
POST /api/atv/apps   {}                   # Apple TV 应用列表
```

## 常见问题

**Android TV**
- **连接不上**：确认同一网段、电视端「网络调试」已开启；终端 `adb connect <ip>:5555` 验证
- **设备未授权**：首次连接需在电视上点「允许」；一直未授权试 `adb kill-server` 后重连
- **中文打不进**：Android `input text` 仅支持 ASCII，中文会被静默丢弃。点键盘区的
  「中文键盘 → 启用」（内置 ADBKeyboard 方案，见上文）。手动装 APK 见
  [ADBKeyBoard](https://github.com/senzhk/ADBKeyBoard)——**Android 16 必须用 v2.5-dev**。
  （Apple TV 侧无此限制）
- **启用后中文还是打不进**：确认状态显示的是「已启用 · 可输中文」（只是「已安装」不够，
  必须是电视的**当前**输入法）。部分电视会弹「选择输入法」确认框，需要在电视上点「确定」。
- **按键没反应、报「休眠」**：电视屏幕熄灭时 `input` 命令会阻塞，先点「☀ 唤醒」
- **电源键无反应**：部分电视限制 `keyevent 26`，用「唤醒」+系统菜单代替

**Apple TV**
- **扫描不到**：电视已唤醒、同网段；路由器开了 AP 隔离则 mDNS 不可用，可重启路由器
- **配对 PIN 不显示**：配对时电视屏幕会自动弹 PIN；若无反应，电视重启后重试
- **音量**：tvOS 16+（Companion）音量键直接可用；旧款（MRP）音量走 AirPlay 需单独配对，当前未实现
- **文字发送无效**：Apple TV 必须有聚焦的输入框（看到「输入框已聚焦」徽标再发送）
- **电视休眠连不上**：Apple TV 深度休眠时需先「唤醒」（tvOS 16+ 支持网络唤醒）

### 原生独立 APK（android-native/）

`android-native/` 是**完全离线独立**的原生 Android 方案：通过 [Chaquopy](https://chaquo.com/chaquopy/) 将 Python 引擎（pyatv + server.py）直接打包进 APK，无需 Termux、无需 Mac 后台服务。

**用途**：给不想折腾 Termux 的用户，装好即用——打开 App 自动启动内置引擎，WebView 加载遥控器界面，直接控制 Apple TV / Android TV。

**与 `android/` 的区别**：
- `android/`：轻量 WebView 壳，需连接 Mac/手机 Termux 上的后台服务
- `android-native/`：内置完整 Python 引擎，完全离线独立（APK 体积更大）

**构建方式**（需 Android Studio + Chaquopy 插件）：

```bash
cd android-native
./gradlew assembleDebug   # 产物：app/build/outputs/apk/debug/app-debug.apk
```

> 首次构建 Chaquopy 会下载 Python 解释器和 pip 依赖（pyatv、qrcode），耗时较长。

## 目录结构

```
atv-remote/
├── server.py          # Web 服务 + adb（Android）+ 设备路由
├── atv_backend.py     # pyatv 封装（Apple TV：扫描/配对/按键/键盘/触摸）
├── static/            # 遥控器界面（html/css/js）
├── android/           # Android WebView 壳 App（需外部引擎）
├── android-native/    # Android 原生独立 App（Chaquopy 内嵌 Python 引擎，离线可用）
├── mac/               # macOS 原生 App（Swift + WebKit）
├── start.command      # macOS 双击启动（优先用 .venv）
├── .venv/             # 虚拟环境（pyatv）
├── state.json         # 设备与配对凭据（自动生成）
└── README.md
```
