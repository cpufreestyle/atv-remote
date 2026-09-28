# AGENTS.md

## 项目概述

ATV Remote — 跨平台本地遥控器，支持 Apple TV 和 Android TV。
Python 后端提供 HTTP API，Web 前端 + Mac 原生 App + Android 客户端多端接入。

## 启动命令

```bash
python3 server.py        # 主入口，启动 Web 服务（默认 0.0.0.0:8300）
./start.sh               # ⚠️ 这是 Termux 脚本（shebang 指向 /data/data/com.termux/…），在 macOS 上跑不了
```

## 源码结构

| 路径 | 说明 |
|------|------|
| `server.py` | Web 服务器入口 |
| `atv_backend.py` | 设备控制后端（adb / pyatv 协议适配） |
| `static/` | Web 前端（index.html, app.js, style.css） |
| `android/` | Android WebView 客户端 |
| `android-native/` | Android 原生客户端（Chaquopy） |
| `android-native/app/src/main/python/` | ⚠️ `server.py` + `atv_backend.py` + `static/` 的**手工副本**，见下方「内嵌副本」 |
| `mac/` | Mac 原生 App（Swift + WebKit） |

## 构建路径

- **Android APK**: `./android/build.sh`
- **Android 原生 APK**: `android-native/gradlew assembleRelease`（Chaquopy 打包 Python 引擎）
  - `syncPythonSrc` 把根目录的 `server.py` / `atv_backend.py` / `static/` 拷进
    `app/src/main/python/`；除 `preBuild.dependsOn` 外，还必须给 `merge*PythonSources`
    补 `dependsOn syncPythonSrc`，否则 Gradle 并行/增量构建会用空目录或旧副本 merge
    （「新后端 + 旧前端」幽灵就是这么回来的）
  - chaquo.com 不可达时的降级构建：`-PnoAppletv`（跳过 pyatv，Android TV 全功能保留）+ 
    `-PpypiMirror=<可达镜像>`（纯 Python 的 qrcode 走镜像装），验收口径：`app.imy` 里
    `static/*` 与根目录逐字节一致、`*.pyc` 头部记录的源码尺寸与当前文件相符（SDK/JDK 路径均可由 `ANDROID_HOME` / `JAVA_HOME` 覆盖，
  默认自动探测；版本号从根目录 `VERSION` 注入 manifest）
- **Android 原生 APK**: `cd android-native && ./gradlew assembleRelease`
  （**必须用 wrapper**：已钉 Gradle 8.14.3，AGP 8.11 与系统里的 Gradle 9.x 不兼容）
- **Mac App**: `mac/main.swift`，使用 `swiftc` 编译

## 版本号与内嵌副本

- 版本号单一来源是根目录 `VERSION`（`versionName` / `versionCode`）；`build.gradle` 与
  `android/build.sh` 都读它，别在别处硬编版本。
- `android-native/app/src/main/python/` 下的副本由构建时的 `syncPythonSrc`（Copy 任务，
  挂在 `preBuild` 前）从根目录重新生成，**已不在 git 里跟踪**（.gitignore）。手工刷新
  （不跑构建时）仍用 `./sync-native.sh`，`check.sh` 会逐字节比对副本与根目录。
- CI：`.github/workflows/check.yml` 在 push / PR 上跑 `./check.sh`（import + node --check
  + 行为回归 + 副本比对），失败即阻断。

## 安全约束

- `debug.keystore` — Android 调试签名密钥，已在 .gitignore 中排除，禁止提交
- `state.json` — 包含 Apple TV 配对凭据，已在 .gitignore 中排除，禁止提交
- **`state.json` 同样不得进 `/bundle.tgz`**（除非启用了 `--token`）：这个包是局域网里任何设备
  都能下载的，凭据漏出去就等于遥控器被人拿走。清单由 `bundle_files()` 统一决定，
  别再往 `build_bundle()` / `bundle_signature()` 里硬编文件名。
- `/install` 返回的脚本会被用户 `curl … | bash` 执行，其中的地址来自 `Host` 头（可伪造），
  必须过 `HOST_RE` 才允许进脚本。

## 验证基线

修改代码后，必须运行以下验证命令并确认全部通过（退出码 0）：

### Python 后端语法检查

```bash
python3 -c 'import server; import atv_backend'
```

- 覆盖文件：`server.py`、`atv_backend.py`
- 验证内容：语法正确性 + 模块级导入可用

### JavaScript 前端语法检查

```bash
node --check static/app.js
```

- 覆盖文件：`static/app.js`
- 验证内容：ES 语法正确性（Node.js 内置解析器，无需额外依赖）

### 一键运行全部验证

```bash
./check.sh          # 等价下面的命令；有 .venv 时额外校验 pyatv 分支
python3 -c 'import server; import atv_backend' && node --check static/app.js && echo "✅ ALL CHECKS PASSED"
```

### 内嵌副本一致性检查

```bash
./sync-native.sh --check    # 只检查；./sync-native.sh 则同步并顺带跑一遍 check.sh
```

- 覆盖 `android-native/app/src/main/python/` 下的 6 个副本（含 `manifest.webmanifest`——
  它的 `start_url` 若写成相对路径，装到桌面后启动地址会解析到 `/static/` 而 404）
- 验证内容：内嵌进 APK 的前后端与根目录**逐字节一致**。不一致就 `./sync-native.sh` 同步，
  不要手工 `cp`（上一次手工同步把 `static/` 拷成了 `static/static/`，APK 里于是装了一份旧前端）。

### HTTP 行为回归测试

```bash
python3 -m unittest discover -s tests
```

- 覆盖文件：`tests/test_http_hardening.py`
- 验证内容：`/bundle.tgz` 不含配对凭据（未启用令牌时）、`Host` 头经校验后才进 `/install` 脚本、
  异常 body 不会让 keep-alive 帧错位、鉴权 cookie 带 `HttpOnly`
- 只用标准库，不起真端口、不碰 adb/pyatv：把 `LOOPBACK_HOSTS` 临时清空来走真实的
  「非回环（局域网）」鉴权分支，测试结束在 `tearDown` 里还原。
- 改动 HTTP 层（`_send` / `_body` / `_check_auth` / 路由分发）后必须跑这个，光 import 检查不出行为回归。

## 依赖

```bash
.venv/bin/pip install -r requirements.txt   # pyatv（Apple TV）+ qrcode，均为可选
```

`server.py` / `atv_backend.py` 对这两个依赖都是**导入失败即优雅降级**，
用系统 `python3` 启动（无 pyatv）也能正常遥控 Android TV。

## 关键约定

- **改 `server.py` / `atv_backend.py` / `static/*` 后跑 `./sync-native.sh`**（不跑构建时；
  跑 `./gradlew` 构建会自动同步）：
  `android-native/app/src/main/python/` 下是这些文件的独立副本（Chaquopy 只编该目录里现成的东西，
  build.gradle 没有 copy 任务）。历史上漂移过一次，结果是「新后端 + 旧前端」的 APK 安静地发出去，
  而且旧版还多拷了一层 `static/static/`。`./check.sh` 会用 `diff -q` 挡住这种漂移，别绕过它。
- **adb 调用很贵**：`adb.devices()` / `adb.version()` 走缓存（TTL 1.5s / 只查一次）。
  连接、断开、命令超时、设备掉线时必须调用 `adb.invalidate_devices()` 主动失效缓存，
  否则会读到陈旧的在线状态。新增任何改变设备在线状态的操作都要记得失效缓存。
- **不要直接改 `adb._shell`**：一律用 `adb.reset_shell()`（内部持锁）。
- **`state` 操作要持 `state_lock`**：它是 `RLock`，`save_state()` 会在已持锁的分支里被调用。
  `save_state()` 走临时文件 + `os.replace` 原子写，不要改回直接覆盖写。
- **前端不要用 `innerHTML` 渲染设备名 / IP**：这些来自局域网广播可被伪造，一律 `textContent`。
- **HTTP 层是 keep-alive（HTTP/1.1）**：所有响应必须带准确 `Content-Length`，走 `_send()` 即可。
- **可选令牌鉴权**：全局 `AUTH_TOKEN` 为空 = 不鉴权（默认，行为与历史版本一致）。
  开启后仅**非回环**来源需要令牌，取值顺序 `X-ATV-Token` 头 → `?token=` → `atv_token` cookie，
  比较一律用 `hmac.compare_digest`。新增路由不要自己判权限，`do_GET` / `do_POST`
  开头的 `_check_auth()` 已统一处理（注意它同时负责种 cookie）。
  `/` 未授权时返回 `LOGIN_PAGE`（表单 GET 提交即变成 `/?token=xxx`）。

### ADBKeyboard 中文输入（Android TV）

`adb shell input text` **只吃 ASCII**，中文 / Emoji 会静默丢失。中文走第三方输入法
ADBKeyboard（`com.android.adbkeyboard/.AdbIME`），由 `/api/ime` 管理、`/api/cmd`
的 `text` / `clear` / `editor` 三种命令消费。

- **广播是静默失效的**：`am broadcast -a ADB_CLEAR_TEXT` 在没有接收器时同样返回
  `Broadcast completed: result=0`、**退出码 0**。所以每个中文操作前必须用
  `require_adbkb()` 确认当前输入法就是 ADBKeyboard（`settings get secure
  default_input_method`），否则用户点了没反应还查不出原因。
- 输入法三态：`installed`（`ime list -a` 能查到）/ `enabled`（`enabled_input_methods`
  里）/ `current`（`default_input_method` 等于它）。**只有 `current` 为真才能输入**。
- `ime enable` 对未安装的输入法返回 **exit 255**，`adb.shell()` 会抛 `AdbError`，
  所以 `enable` 分支先查 `installed`，用结构化返回（带 APK 链接）而不是抛异常 ——
  长 URL 进 toast 会糊成一团，交给前端渲染成可点链接。
- 用 **base64**（`ADB_INPUT_B64`）而不是 `ADB_INPUT_TEXT`：后者在 Oreo+ 传 UTF-8 会坏。

### 一键宏（macro）

`/api/cmd` 的 `type=macro` 是唯一在**线程里异步执行**的命令：20 步 × 每步最多 10s 延时会
超过前端 fetch 的耐心，所以请求线程只做同步校验（拼错立刻 400），执行放 `_macro_worker`
线程，进度/失败写 stderr。单步失败**不**中断整条宏（App 没装时后续步骤照跑），取消用
`_macro_stop` 事件（延时中用 `Event.wait` 实现可打断 sleep）。自定义宏放浏览器
localStorage，不要写进 `state.json`（敏感文件，不放可编辑内容）。

### 局域网访问令牌

- 绑 `0.0.0.0`/`::` 且没指定令牌时**首启自动生成**，存 `state.json` 的 `token` 键（重启不变，
  否则手机每次重启都要重新配）。单一决策点是 `resolve_token()`，main() 与测试都走它。
- `--no-token` 或 `--host 127.0.0.1` 都不鉴权；`--token ""` 是显式不鉴权（历史行为）。
- `/api/setup` 会把令牌与局域网地址发给已授权客户端，只用于渲染接入二维码；这是已知取舍
  （持令牌者本来每次请求都带着它）。
- 服务发现：macOS 上用 `dns-sd` 广播 `_atv-remote._tcp`（`start_mdns()`），没有 dns-sd 的
  平台静默跳过——只是可发现性增强，不影响 IP/二维码访问。

### 设备休眠时 `input` 会阻塞

屏幕熄灭时 `input text` / `input keyevent` **会一直挂住**（实测），而 `settings` /
`dumpsys` 不受影响。因此 `run_shell()` 超时后会查一次 `dumpsys power`：
- 确认休眠 → 报「先点☀ 唤醒」（原来报「未授权/离线」，是误导，用户会去查授权）；
- 但**唤醒键（26 / 223 / 224）豁免**，直接返回成功 —— 否则提示用户点唤醒、
  用户点了又弹一次「休眠」，自相矛盾。

`ime` 状态查询要 3 条 shell，前端按需查询（设备切换时），**不能挂进 8s 轮询**。
