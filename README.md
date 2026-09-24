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
| 设备发现 | 手动输 IP + 无线调试扫描 | ✅ 局域网自动扫描 |
| 一键宏（场景） | ✅ | ✅（包名依次尝试 Android/tvOS） |
| Mac 菜单栏迷你遥控 | ✅（原生 App 附带） | — |
| 服务自发现（mDNS） | ✅ `_atv-remote._tcp` | ✅（macOS `dns-sd` 广播） |

## 使用

### Mac 端（一次安装，之后零操作）

**遥控器 App（推荐，不用浏览器）**：已安装到 `/Applications/ATVRemote.app`——点开就是遥控器窗口（原生 App，加载本地服务）。可拖进 Dock 常驻；想开机自动弹出：系统设置 → 通用 → 登录项 → 添加 ATVRemote。

**菜单栏遥控器**：同一个 App 还在系统菜单栏放了一个 📺 图标，点开是迷你遥控面板：
D-pad / 播放控制 / 音量 / 静音 / 主页 / 返回 / Esc / 电源，面板顶部显示当前连接的电视。
日常调音量、暂停不用先打开大窗口（全部走 `127.0.0.1:8300` 的 HTTP API，天然免令牌）。

首次部署（换新机器时）：

```bash
cd ~/atv-remote
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 首次（Apple TV 支持）
bash mac-install.sh                                    # 后台服务装成开机自启（崩溃自动重启）
cd mac && swiftc -O -target arm64-apple-macos26.0 -o ATVRemote main.swift -framework Cocoa -framework WebKit
# 再把 mac/ATVRemote.app 拷到 /Applications（注意：本机 CLT 的 SDK 默认 minos 28.0 高于系统版本，
# 会报 open -10825，必须显式 -target arm64-apple-macos26.0；mac/Info.plist 的
# LSMinimumSystemVersion 与之对齐为 26.0）
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

### ⚡ 一键宏

页面「⚡ 一键宏」区有预置场景：观影模式（打开 Netflix + 音量降 3 格）、看 YouTube、静音、
回主页、音量降 3 格。宏 = 一串命令 + 步间延时，由服务端串行执行（单步失败不中断，
比如 App 没装时后续音量调整照跑）。应用步骤可给多个包名，会依次尝试 Android/tvOS。

自定义宏用 JSON 写在文本框里，「存为本机宏」后常驻该浏览器（localStorage）：

```json
{"name":"我的宏","steps":[
  {"type":"app","pkgs":["com.google.android.youtube.tv","com.google.ios.youtube"]},
  {"delay":2500},
  {"type":"key","codes":[25,25]},
  {"type":"text","text":"搜索词","enter":true}
]}
```

可用步骤类型：`key`（`code`/`codes`）、`text`（`text`，可加 `enter`）、`app`（`pkg`/`pkgs`），
外加纯延时步骤 `{"delay":毫秒}`。上限 20 步、单步延时 ≤10s。

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

### 📱 原生安卓 App（推荐）

`ATVRemote-native.apk`（约 33MB）——**Python 引擎直接内嵌**（Chaquopy），安装即用、零配置，无需 Mac 也无需 Termux：

- 打开 App → 自动启动内置引擎 → 直接进入遥控器
- Apple TV 的扫描/配对/键盘输入（含中文）/应用启动全部内置；Android TV 因手机沙箱无 adb 二进制不可用（原生版主打 Apple TV）
- 从 [Releases](https://github.com/cpufreestyle/atv-remote/releases) 下载安装即可

重新构建：

```bash
cd android-native
./gradlew assembleRelease   # 产物: app/build/outputs/apk/release/app-release.apk
```

> 必须用仓库里的 `./gradlew`（已钉 Gradle 8.14.3）：AGP 8.11 与 Gradle 9.x 不兼容，
> 系统 `gradle`（homebrew 现为 9.x）直接跑会配置失败。首次运行 wrapper 会按
> `gradle-wrapper.properties` 里的官方地址下载 Gradle；国内网络下可先把对应
> `gradle-8.14.3-bin.zip` 放到 `~/.gradle/wrapper/dists/gradle-8.14.3-bin/<hash>/` 下，
> 或换用镜像源改 properties 的 `distributionUrl`。

> 构建踩坑记录：Chaquopy 17 的 pip 需要访问自己的 Android wheel 源（chaquo.com）与 PyPI，
> 网络不通时 `install*PythonRequirements` 会失败（pyatv 本体是纯 Python，但 cryptography /
> chacha20poly1305 / pydantic-core 的 Android 预编译 wheel 只有 Chaquo 提供）；`chacha20poly1305_reuseable` 继承 Rust 类在 Chaquopy 下不可继承，已由 `app/src/main/python/boot.py` 注入组合式 shim 解决；旧版 pyatv 无 `touch` 接口，触摸板操作自动降级提示；Android 模拟器 NAT 不转发 mDNS，Apple TV 扫描需真机验证。

### 手机独立运行（不需要 Mac）

App 有「🚀 独立模式」：手机内的 Termux 引擎直连电视（adb 和 Apple TV 协议都是纯 TCP）。安装方式见上文「手机（3 步…）」——网页底部的手机安装卡片会给出全部链接和一键命令。

引擎排错：Termux 里跑 `~/atv-remote/start.sh` 看输出，日志在 `~/atv-remote/server.log`。

> 为什么不把 Python 引擎直接打包进 APK？pyatv 依赖 cryptography/pydantic-core 等 Rust 原生库，无法在 Android 上现成交叉编译（Chaquopy 无预编译），重写协议工程量大。Termux 方案零重写、依赖齐全。

## 版本号与默认端口

- 版本号单一来源：根目录 **`VERSION`**（`versionName` / `versionCode` 两行）。
  `android-native/app/build.gradle` 与 `android/build.sh`（aapt2 link 前注入 manifest）都读它，
  不再各写一份。当前：见文件内容。
- 默认端口 `8300` 的唯一定义在 `server.py` 的 `--port` 默认值；`mac/main.swift`、
  `termux-setup.sh`、`mac-install.sh` 与文档里的是对该默认值的镜像，改端口请一并同步。

## 命令行参数

```
python3 server.py [--host 127.0.0.1] [--port 8300] [--adb adb路径] [--no-open] [--token 令牌]
```

用 `.venv/bin/python server.py` 启动会加载 Apple TV 支持（pyatv）；直接 `python3 server.py` 时 Apple TV 功能自动禁用、Android 照常可用。

### 🔒 局域网访问令牌（默认自动生成）

服务默认监听 `0.0.0.0`。为了不让「同网段任何人都能对你的电视发 `input text` / `monkey`」成立，
**首次启动会自动生成一个访问令牌**，打印在终端横幅里并存入 `state.json`（重启不变）。
之后的行为：

- **本机（`127.0.0.1` / `::1`）免令牌**，本机浏览器和 Mac App 用法不变；
- 局域网设备首次访问会看到登录页；打开本页面「📱 装到手机」里有**带令牌的二维码**，扫一次即完成
  接入（服务端种 cookie，之后不再需要令牌）；
- 想指定自己的令牌：`--token 你的令牌`（或环境变量 `ATV_TOKEN`）；
- 想退回「完全无鉴权」的自用内网：`--no-token`，或用 `--host 127.0.0.1` 只允许本机访问。

开启后：

- **本机（`127.0.0.1` / `::1`）免令牌**，本机浏览器和 Mac App 用法不变；
- 局域网设备访问 `/` 会看到登录页，输入令牌即可（成功后种 cookie，后续请求自动带上）；
- 脚本 / App 调用用 `X-ATV-Token` 头，或在 URL 后加 `?token=<令牌>`：
  ```bash
  curl -H 'X-ATV-Token: 你的令牌' -X POST -d '{"type":"key","code":19}' http://192.168.1.5:8300/api/cmd
  ```
- 页面上的 Termux 安装命令、APK 直链、二维码会自动带上令牌，手机装引擎的流程不受影响。

#### 配对凭据只在有令牌时才随包下发

`/bundle.tgz` 是 Termux 一键安装要拉的代码包。**未启用令牌时它不含 `state.json`**（Apple TV 配对凭据），
因为此时局域网里任何设备都能把它拖走，拿到凭据就等于拿到了遥控器；手机装完引擎后自行配对即可。
启用令牌后，取包必须先通过鉴权，这时才会把配对记录一并同步过去（省去二次配对）。

### 🎙 iOS/Mac「快捷指令」对 Siri 说话遥控

4 个高频动作各 30 秒配好，之后「嘿 Siri，电视暂停」直接生效；加到 Apple Watch 表盘也能按。

在 iPhone「快捷指令」App 里新建：

1. 添加操作 **获取 URL 内容**；
2. URL 填 `http://<Mac的局域网IP>:8300/api/cmd`（启动横幅里的「手机访问」地址去掉 `?token=…` 部分）；
3. 方法选 **POST**，请求体选 **JSON**，粘贴下面对应的 JSON；
4. 开了令牌时再加一步「获取 URL 内容」的**头部**：`X-ATV-Token` = 你的令牌（令牌见启动横幅或本页「📱 装到手机」二维码）；
5. 把指令命名为「电视暂停」之类——名字就是 Siri 唤起动词。

| 指令名 | 请求体 JSON |
|---|---|
| 电视暂停 / 播放 | `{"type":"key","code":85}` |
| 电视静音 | `{"type":"key","code":164}` |
| 电视音量加 | `{"type":"key","code":24}` |
| 半小时后关电视 | `{"type":"timer","action":"set","minutes":30}` |

> 快捷指令的 JSON 请求体类型只有「文本」也能跑，但选 JSON 最稳。Watch 上用同一批指令，
> 无需改任何内容。想跑宏：把请求体换成 `{"type":"macro","steps":[…]}`（步骤见「⚡ 一键宏」）。

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
POST /api/cmd       {"type":"macro","name":"观影模式","steps":[...]}   # 一键宏（服务端线程串行执行）
POST /api/cmd       {"type":"macro","action":"cancel"}                 # 取消执行中的宏
GET  /api/macros                          # 预置宏列表 + 执行状态
GET  /api/setup                           # 令牌与手机接入地址（渲染二维码用）
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
./gradlew assembleDebug    # 产物：app/build/outputs/apk/debug/app-debug.apk
```

> 首次构建 Chaquopy 会下载 Python 解释器和 pip 依赖（pyatv、qrcode，版本跟 `requirements.txt` 对齐），耗时较长。

**chaquo.com 不可达时的降级构建**（只为刷新内嵌的前后端，不附带 Apple TV 功能）：

```bash
./gradlew assembleRelease -PnoAppletv -PpypiMirror=https://mirrors.cloud.tencent.com/pypi/simple
```

- `-PnoAppletv`：跳过 pyatv（Android 侧的 Apple TV 遥控不可用；Android TV 全功能不受影响，
  `server.py` / `atv_backend.py` 对 pyatv 导入失败即优雅降级）；
- `-PpypiMirror`：纯 Python 包（qrcode）改走可达镜像——chaquo.com 挂着时整个 pip 索引都取不到，
  没有 Android 原生 wheel 的包任何镜像都能装；
- 产物 `app/build/outputs/apk/release/app-release.apk` 拷贝为 `android-native/ATVRemote-native.apk`
  即替代旧包（该文件本地产物，不进 git）；
- 网络恢复后去掉这两个参数即可构建带 pyatv 的完整版。
> `src/main/python/` 下的副本**由构建自动生成**：`syncPythonSrc`（Copy 任务，挂在 `preBuild` 前）
> 每次构建从根目录重新拷贝 `server.py` / `atv_backend.py` / `static/`，这几份已不在 git 里跟踪
> （单一事实来源是根目录）。不跑构建的手工刷新仍走 `./sync-native.sh`；`./check.sh` 逐字节
> 比对副本与根目录，漂移即失败。

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
