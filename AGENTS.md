# AGENTS.md

## 项目概述

ATV Remote — 跨平台本地遥控器，支持 Apple TV 和 Android TV。
Python 后端提供 HTTP API，Web 前端 + Mac 原生 App + Android 客户端多端接入。

## 启动命令

```bash
python3 server.py        # 主入口，启动 Web 服务
./start.sh               # 脚本封装
```

## 源码结构

| 路径 | 说明 |
|------|------|
| `server.py` | Web 服务器入口 |
| `atv_backend.py` | 设备控制后端（adb / pyatv 协议适配） |
| `static/` | Web 前端（index.html, app.js, style.css） |
| `android/` | Android WebView 客户端 |
| `android-native/` | Android 原生客户端（Chaquopy） |
| `mac/` | Mac 原生 App（Swift + WebKit） |

## 构建路径

- **Android APK**: `./android/build.sh`
- **Mac App**: `mac/main.swift`，使用 `swiftc` 编译

## 安全约束

- `debug.keystore` — Android 调试签名密钥，已在 .gitignore 中排除，禁止提交
- `state.json` — 包含 Apple TV 配对凭据，已在 .gitignore 中排除，禁止提交

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

## 依赖

```bash
.venv/bin/pip install -r requirements.txt   # pyatv（Apple TV）+ qrcode，均为可选
```

`server.py` / `atv_backend.py` 对这两个依赖都是**导入失败即优雅降级**，
用系统 `python3` 启动（无 pyatv）也能正常遥控 Android TV。

## 关键约定

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

### 设备休眠时 `input` 会阻塞

屏幕熄灭时 `input text` / `input keyevent` **会一直挂住**（实测），而 `settings` /
`dumpsys` 不受影响。因此 `run_shell()` 超时后会查一次 `dumpsys power`：
- 确认休眠 → 报「先点☀ 唤醒」（原来报「未授权/离线」，是误导，用户会去查授权）；
- 但**唤醒键（26 / 223 / 224）豁免**，直接返回成功 —— 否则提示用户点唤醒、
  用户点了又弹一次「休眠」，自相矛盾。

`ime` 状态查询要 3 条 shell，前端按需查询（设备切换时），**不能挂进 8s 轮询**。
