# ATV Remote — 项目规格（spec.md）

> 本文件是「实现的事实契约」：描述当前代码**应当**具备的行为与可复现的验证方式。
> 事实优先级：server.py / atv_backend.py / static/ 源码 > tests/ > 本文件 > README.md（仅作介绍）。
> 任何行为变更必须同步更新本文件与 docs/harness.md；docs/harness.md 记录最近一次核验结论。
>
> 最近核验：2026-10-07（HEAD 69dca04）

## 1. 目标与范围

ATV Remote 是跨平台本地遥控器：Python 后端提供 HTTP API，控制两类电视 ——
Android TV（adb）与 Apple TV（pyatv / MediaRemote 协议，与 iOS「遥控器」App 同款）。
前端是纯静态 Web UI，另有 Mac App 与 Android 原生客户端两个壳。

- 运行形态：python3 server.py，默认绑 0.0.0.0:8300，LaunchAgent 默认 --no-token（不鉴权）。
- 代码边界：server.py（HTTP 层 + adb + 设备路由）、atv_backend.py（pyatv 封装）、
  static/（index.html / app.js / style.css / sw.js）、mac/、android/、android-native/。
- 非目标：多用户、公网服务、中心化配置。设备管理是「当前设备」单槽切换。

### 1.1 关键约束（摘自 AGENTS.md，改动前必读）

- state.json 含 Apple TV 配对凭据，永不入库；未启用令牌时也不得进 /bundle.tgz。
- debug.keystore 不入库。
- 前端渲染设备名 / IP 一律 textContent（局域网广播可伪造），不得用 innerHTML。
- HTTP 层 keep-alive：所有响应必须带准确 Content-Length，走 _send()。
- adb 调用有缓存（devices TTL 1.5s / version 只查一次）；任何改变设备在线状态的操作
  必须调用 adb.invalidate_devices()。
- state 操作持 state_lock（RLock）；save_state() 走临时文件 + os.replace 原子写。
- 改 server.py / atv_backend.py / static/* 后跑 ./sync-native.sh（内嵌副本一致性）。

## 2. 外部依赖契约

### 2.1 仓库外可执行与库（缺失时优雅降级，不得崩溃）

| 用途 | 探测方式 | 缺失时行为 |
|------|----------|------------|
| adb | shutil.which 后依次试 /opt/homebrew/bin、/usr/local/bin、~/Library/Android/sdk/platform-tools | Android TV 功能不可用，Apple TV 与 Web UI 正常 |
| pyatv | import 成功与否 | Apple TV 功能不可用，Android TV 正常 |
| qrcode | import 成功与否 | /api/qr.svg 回 503 加安装提示，页面其余正常 |
| dns-sd（macOS） | shutil.which | 静默跳过 mDNS 广播，仅影响可发现性 |

### 2.2 运行时文件

| 路径 | 性质 | 说明 |
|------|------|------|
| state.json | 状态加凭据 | 原子写；gitignore；测试必须指向临时文件 |
| server.log | 日志 | LaunchAgent 重定向 stderr；gitignore |
| ~/Library/LaunchAgents/com.atv.remote.plist | 开机自启 | 由 mac-install.sh 生成，默认带 --no-token |

## 3. HTTP 接口契约

鉴权入口是 do_GET / do_HEAD / do_POST 开头的 _check_auth()，新增路由不自行判权。

### 3.1 只读端点（GET，无副作用）

| 路径 | 成功 | 失败 | 说明 |
|------|------|------|------|
| / 与 /index.html | 200 HTML | 无 | 注入令牌占位符 __ATV_TOKEN__ |
| /static/* | 200 | 404 | 路径必须落在 static 根内（防目录穿越） |
| /api/status | 200 JSON | 无 | 两台设备连接状态 |
| /api/perf | 200 JSON | 无 | 调用时间线快照（30s 环形缓冲）加 adb 版本与路径 |
| /api/diagnostics | 200 JSON | 无 | 一键体检报告，必须脱敏（见 I1） |
| /api/nowplaying | 200 JSON | 无 | Now Playing；解析失败安静降级为空 |
| /api/volume | 200 JSON | 无 | 查询音量 |
| /api/screenshot | 200 PNG | 无 | Android 截屏或 Apple TV 播放画面 |
| /api/macros | 200 JSON | 无 | 预置宏与执行状态 |
| /api/wol | 200 JSON | 无 | 网络唤醒配置 |
| /api/setup | 200 JSON | 无 | 令牌与局域网地址（仅渲染接入二维码用） |
| /api/qr.svg?text= | 200 SVG | 400 缺参、503 未装 qrcode | 二维码生成 |
| /install | 200 text/plain | 无 | 会被 curl 管道 bash 执行；Host 头必须过 HOST_RE（I2） |
| /bundle.tgz | 200 gzip | 无 | 内容清单由 bundle_files() 单点决定（I1） |
| /app.apk | 200 APK | 404 | APK 不存在时回 404 加路径提示 |

do_HEAD 与 do_GET 同路由、只回头不回体：下载管理器与「点链接先探测」的浏览器都先发 HEAD，
早期未实现时回 501，表现为「点了下载没反应」而直接敲 URL 是好的。

### 3.2 写端点（POST，经 ROUTES 分发表）

| 路径 | handler | 说明 |
|------|---------|------|
| /api/connect | handle_connect | Android 连接，target 必须是真实设备 id |
| /api/disconnect | handle_disconnect | 断开当前设备 |
| /api/android/scan | handle_android_scan | 无线调试扫描 |
| /api/android/pair | handle_android_pair | 无线调试配对 |
| /api/cmd | handle_cmd | 按键、文本、触摸、应用、IME、timer、macro |
| /api/ime | handle_ime | ADBKeyboard 三态管理 |
| /api/forget | handle_forget | 忘记 Android 设备 |
| /api/switch | handle_switch | 切换当前设备 |
| /api/atv/scan | handle_atv_scan | 扫描 Apple TV |
| /api/atv/pair | handle_atv_pair | 配对（begin 与 finish） |
| /api/atv/connect | handle_atv_connect | 连接 |
| /api/atv/disconnect | handle_atv_disconnect | 断开 |
| /api/atv/forget | handle_atv_forget | 忘记并清凭据 |
| /api/atv/apps | handle_atv_apps | 应用列表 |
| /api/volume | handle_volume_set | 按格精确设置音量 |
| /api/wol | handle_wol | 远程开机配置写入 |

未匹配路由：先把请求体读干净再回 404，否则长连接上下一请求会从半截 body 开始解析，
帧错位比报错严重得多。畸形 body（非 JSON 对象、缺字段、坏值）一律回 400 加中文提示，
不得让 KeyError 或 ValueError 逃成 500。

### 3.3 宏执行语义

宏是唯一在独立线程异步执行的命令：20 步乘每步 10s 延时会超过前端 fetch 耐心，
所以请求线程只做同步校验（拼错立即 400），执行放 _macro_worker 线程，进度写 stderr。
单步失败不中断整条宏（App 未装时后续步骤照跑）。取消用 _macro_stop 事件，
延时中用 Event.wait 实现可打断 sleep。自定义宏只写浏览器 localStorage，不进 state.json。

### 3.4 中文输入语义

adb shell input text 只吃 ASCII，中文与 Emoji 会静默丢失。中文走 ADBKeyboard 输入法，
经 base64 广播（ADB_INPUT_B64）而不是 ADB_INPUT_TEXT。广播是静默失效的：
无接收器时同样返回退出码 0，因此每个中文操作前必须用 require_adbkb() 确认
当前输入法就是 ADBKeyboard（settings get secure default_input_method）。
输入法三态为 installed、enabled、current，只有 current 为真才能输入。
ime enable 对未安装的输入法返回 exit 255，所以 enable 分支先查 installed，
用结构化返回而不是抛异常。

## 4. 不变式（Invariants）

| 编号 | 不变式 | 主要证据 |
|------|--------|----------|
| I1 | 凭据不外泄：未启用令牌时 /bundle.tgz 不含 state.json；/api/diagnostics 不含凭据与令牌值 | tests/test_http_hardening.py、tests/test_diagnostics.py |
| I2 | Host 头不可信：/install 脚本中的地址必须过 HOST_RE 才允许写入 | tests/test_http_hardening.py |
| I3 | keep-alive 不错位：声明的 body 超限时读完再回应，上限 100000 字节 | tests/test_http_hardening.py |
| I4 | 缺陷输入不逃 500：缺字段、坏值、body 非 JSON 对象一律 400 | tests/test_input_validation.py |
| I5 | 休眠不误导：屏幕熄灭时 input 阻塞后报「先点唤醒」而非「未授权」；唤醒键 26/223/224 豁免 | tests/test_smart_wake.py |
| I6 | 令牌单一决策点：resolve_token() 四分支；比较一律 hmac.compare_digest；仅非回环要求令牌 | tests/test_macro_token.py |
| I7 | 手机免令牌：LaunchAgent plist 的 ProgramArguments 含 --no-token | tests/test_launchagent.py |
| I8 | 前后端常量同源：宏 FE 常量与 server.py 逐项相等 | tests/test_macro_dryrun.py |
| I9 | 中文必须经 ADBKeyboard：ASCII 命令打不出中文，IME 三态中只有 current 可输入 | tests/test_atv_fallback.py、tests/test_android_engine.py |
| I10 | 内嵌副本零漂移：android-native 下 6 个副本与根目录逐字节一致 | ./sync-native.sh --check |
| I11 | 图标顺序：index.html 中 PNG 在 SVG 之前，且无 data-URI 占位图标 | tests/test_icons.py |
| I12 | 前端渲染用 textContent：设备名与 IP 不得经 innerHTML 渲染 | tests/test_command_palette.py |

## 5. 验证矩阵（spec 与 harness 对应）

| 不变式或领域 | 验证手段 | 测试文件 |
|--------------|----------|----------|
| 全量基线 | ./check.sh | 见 5.1 |
| I1、I2、I3 | python3 -m unittest discover -s tests | tests/test_http_hardening.py |
| I4 | 同上 | tests/test_input_validation.py |
| I5 | 同上 | tests/test_smart_wake.py |
| I6 | 同上 | tests/test_macro_token.py |
| I7 | 同上 | tests/test_launchagent.py |
| I8 | 同上 | tests/test_macro_dryrun.py |
| I9 | 同上 | tests/test_atv_fallback.py |
| I10 | ./sync-native.sh --check | 无 |
| I11、I12 | python3 -m unittest discover -s tests | tests/test_icons.py、tests/test_command_palette.py |
| 前端行为 | node harness，由 Python 测试经 subprocess 驱动 | 20 个 tests/*_harness.js |

### 5.1 一键验证基线

check.sh 依序执行：import server 与 atv_backend、node --check static/app.js、
node --check static/sw.js、python3 -m unittest discover -s tests、
./sync-native.sh --check，有 .venv 时在 pyatv 分支复跑后两项。
当前基线：602 项单测通过，skipped 11 项。

## 6. 已知缺口（尚未由测试覆盖，后续 spec 候选）

- pyatv 缺失时 Apple TV 的协议层覆盖不足，主要靠 .venv 跑偏移。
- static/app.js 体量 5712 行，交互大量靠 20 个 node harness 覆盖，
   harness 之间没有共享受测代码的单一来源。
- Playwright 或 CDP 真机验收需要真实环境，未纳入 check.sh，靠人工执行。

第 6 节应由后续轮次逐条收敛，收敛时在 docs/harness.md 的第 4 节记一笔。
