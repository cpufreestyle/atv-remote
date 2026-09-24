# ATV Remote — 架构风险与质量审查

审查日期：2026-09-21 · 分支 `main` @ `4ec1fc8` · 目标：找出可执行的优化点并按优先级给出修复计划
方法：全量通读 `server.py`(1130 行) / `atv_backend.py`(401 行) + 两个并行探查（前端、打包与漂移）+ **对运行中的实例做黑盒验证**

## 0. 证据口径

- **实测**＝在本机起了临时实例真打过请求（或 tar 里点过成员名），有可复现命令。
- **静态**＝代码/配置里能直接读到，但未在运行时复现。
- **推断**＝从结构风险推出的后果，明确标注，不当结论用。
- 严重度＝爆炸半径 × 可达性；置信度＝证据强度。两者分开记。

本轮关键的一点：**最高危的那条不是推断，是实测确认的**（见 F1，附取证命令与输出）。

## 1. 已修复并验证（15 项）

| # | 严重度 | 位置 | 问题 | 证据 | 修法 |
|---|---|---|---|---|---|
| F1 | **Critical** | `server.py` `build_bundle()`/`bundle_signature()` | `/bundle.tgz` 把 `state.json` 打进包，而该端点在默认形态（`--host 0.0.0.0` + 无 `--token`）下对局域网任意设备开放 → Apple TV 配对凭据外泄，拿到即等同拿到遥控器 | **实测**：`GET http://198.18.0.1:8300/bundle.tgz` → HTTP 200、成员含 `atv-remote/state.json`，且与磁盘上那份**逐字节相同**、`companion_cred` 长度 275 | 凭据改为「启用令牌后才进包」（`bundle_files()` 单一清单）。功能不丢：持令牌者照样免二次配对；无令牌时手机自行配对 |
| F2 | High | `server.py` `_body()` | 声明的 body >100KB 时直接 `return {}`，**字节不读干净** → 这条 HTTP/1.1 长连接上的下一个请求从半截 body 开始解析（帧错位）；`Content-Length: abc` 还会 `int()` 抛 ValueError 冒到 500 | **实测**：连发「超大 POST + 正常 GET」，修复后第一响应 400 + `Connection: close`、该连接上无残留响应 | 非法/超限一律 400 并显式关连接 |
| F3 | High | `server.py` `/install` | `Host` 头未校验就拼进 `curl … \| bash` 的脚本（用户会复制到手机执行）。另外兜底值 `lan_ip()` 走默认路由探测，本机有 VPN/TUN 网卡时会给出一个手机根本连不上去的地址 | **实测**：`Host: evil.example.com/oops` → 修复后不出现在脚本里，回退为「本次被访问的那个本机地址」（`_reachable_host()` 取 `getsockname`）；合法 Host 仍生效 | 新增 `HOST_RE` 白名单 + 回退到实际可达地址 |
| F4 | Medium | `server.py` `handle_cmd()` | 读 `state["current"]` 未持 `state_lock`，违反本项目自己写在 AGENTS.md 的约定（同文件的 `handle_cmd_android` 就持锁） | 静态（代码自相矛盾即证据） | 分发前持锁取 `type` |
| F5 | Medium | `server.py` `do_GET`/`do_POST` | 500 响应把异常原文回给客户端（内部细节外泄）；同时 `log_message` 全静默 → 服务端**零可观测性**，出错只有一句「内部错误」且无人能看到堆栈 | 静态 | 堆栈进 stderr（LaunchAgent 已收进 `server.log`），响应只给通用文案 |
| F6 | Medium | `server.py` `_send()` | 鉴权 cookie 缺 `HttpOnly` | 静态 + 实测响应头 | 补 `HttpOnly`（令牌本就只由服务端读，前端用注入的 meta） |
| F7 | Medium | `server.py` `handle_cmd_appletv()` | Apple TV 分支缺 Android 分支已有的护栏：`text` 不限长、`codes` 不限量（每条最坏阻塞 12s）、`pkg` 不做字符白名单 | 静态（两分支并排对比） | 复用 `MAX_TEXT_LEN`/`MAX_KEYCODES`，抽 `APP_ID_RE` 两支共用 |
| F8 | Medium | `server.py` `make_status()` | `keyboard_focus()` 挂在 8s 轮询里，而它要经 pyatv 的 loop + 遥控器锁（最坏 12s）——与 AGENTS.md 对 `ime` 的既有约定（「不能挂进 8s 轮询」）同一类问题 | 静态（调用链 `make_status → _call → run(timeout=12)`） | `kb_focus_cached()`：TTL 10s + 取上次值 |
| F9 | Medium | `check.sh` | 验证基线只做 `import` + `node --check`，**看不见 5 份手工副本**；本项目已经因此发出过「新后端 + 旧前端」的 APK | 静态 + 上一轮实际事故 | 新增 `./sync-native.sh`（同步原语，杜绝再次手工 `cp` 拷成 `static/static/`）+ `check.sh` 跑 `--check` 做逐字节比对 |
| F10 | Medium | `build.gradle` / `INSTALL_SCRIPT` / `README` | 同一份源码在 4 处依赖版本不同：`requirements.txt` 钉 0.18.0，Gradle 与 Termux/README 不钉 → 实测 APK 内是 **pyatv 0.13.2**（`atv_backend.py` 里那段 `# pyatv < 0.14` 兼容分支就是为它存在的） | 静态 + APK 内 `requirements-common.imy` 取证 | 全部对齐 `pyatv==0.18.0`、`qrcode==8.2` |
| F11 | Low | `static/app.js` | 令牌拼进每个请求 URL（进浏览器历史 / Referer / 中间代理日志），而服务端早就支持 `X-ATV-Token` 头 | 静态 | `api()` 与截屏 fetch 改走请求头；`curl`、`<img>`、APK 直链这类发不出自定义头的场景保留 `?token=` |
| F12 | Low | `static/app.js` `refreshStatus()` | `catch { /* ignore */ }` 静默吞掉失败：服务端挂了状态栏仍显示上一次的「已连接」；且无在途保护，慢响应会叠加轮询 | 静态 | 在途锁 + 失败时把状态点置灰并写明「连不上服务端」 |
| F13 | Low | `static/app.js` 删除已配对设备 | `api(...).then(...)` 无 `catch` → unhandled rejection，失败无任何提示 | 静态（探查报 3 处，**逐条核对后只有这 1 处成立**，另 2 处调用的是自带 try/catch 的函数） | 改 `async/await` + `toast` |
| F14 | Low | `README.md` / `AGENTS.md` | 文档指向不存在的命令：`./gradlew`（树里没有 wrapper）、`./start.sh`（Termux shebang，macOS 跑不了）；README 还写着「`/bundle.tgz` 里的凭据」这一既有风险作为开令牌的理由 | 静态 | 改 `gradle`、标注 `start.sh` 用途；README 改写为新的凭据下发规则 |
| F15 | Low | `server.py` `/install` | **HOST_RE 放行但不带端口的 Host 会生成指向 80 的地址**：`Host: [::1]`（或反向代理改写过的 Host）合法通过白名单，却被原样拼成 `http://[::1]/bundle.tgz` → 手机照着 curl 连不上，且报错看起来像"服务没开" | **实测**：修复前 `curl -H 'Host: [::1]' /install` 输出 `http://[::1]/bundle.tgz`；红→绿验证（临时删掉修复，`test_install_appends_port_to_host_without_one` 立即失败） | 用 `HOST_RE` 的端口捕获组判断，缺失时补真实监听端口；`INSTALL_SCRIPT` 里那句「有配对记录会一并同步」的注释同步改为「仅启用 `--token` 时」 |

回归测试：`tests/test_http_hardening.py`（8 例，仅标准库，临时清空 `LOOPBACK_HOSTS` 以走真实的「非回环＝局域网」鉴权分支），已并入 `check.sh`。`./check.sh` 全绿（8/8，退出码 0）。

### 1.1 在真实运行实例上的复核（2026-09-21 重启 LaunchAgent 后）

上面 F1/F2/F3 的修复此前只在进程内测试里验证过。重启 `com.atv.remote`（旧 PID 4924 → 新 PID）后在 `:8300` 上实测：

| 检查 | 修复前（同一端点、同一命令） | 修复后 |
|---|---|---|
| `GET /bundle.tgz \| tar tz` | 含 `atv-remote/state.json` ← **凭据确实在局域网里可下载** | 6 个成员，**无 `state.json`** |
| `POST /api/cmd` 198KB body | — | `400 {"error": "请求体过大（198917 字节）"}`、`Content-Length: 47`、`Connection: close` |
| 同一连接「未知 POST 路由（带 body）+ 正常 GET」 | 帧错位风险 | `[404, 200]`，后续请求解析正确 |
| `Content-Length: abc` | 冒到 500 | `400 Bad Request`，**`server.log` 零增长**（无堆栈落盘） |
| `Host: x;rm -rf /` | 原样进脚本 | 回退 `http://127.0.0.1:8300`，且 `/tmp/pwned` 未被创建 |
| `Host: [::1]` / 无端口主机名 | `http://[::1]/bundle.tgz`（指向 80） | `http://[::1]:8300/bundle.tgz` |
| 功能未回退 | — | `/api/status` 200；Android 设备 `state: device` 在线；`appletv.available: true`、已配对 1 台（重启后状态文件完好） |

## 2. 仍未解决（按优先级，含证据与验证口径）

| # | 严重度 | 置信度 | 问题 | 为什么这轮没动 | 怎么验收 |
|---|---|---|---|---|---|
| O1 | **High** | 高 | **没有单一事实来源**：`server.py`+`static/` 在仓库里有 5 份副本（根目录、`android-native/…/python/`、Termux 运行目录 `termux-setup.sh:25`、`/bundle.tgz`、以及 APK 内 `app.imy`）。F9 的 `diff` 只能**事后发现**漂移，不能消除 | 真正的解法是 Gradle `Copy` 任务或符号链接让 `src/main/python` 由根目录**生成**，但这要跑一次 `gradle assembleDebug` 才能确认没把 Chaquopy 的源集搞坏；本轮无 SDK 构建验证，不敢盲改 | `build.gradle` 里出现从根目录生成 `src/main/python` 的任务，且 `git ls-files` 不再跟踪那 5 个副本；随后 `gradle assembleDebug` 成功、APK 内 `static/app.js` 与根目录 md5 相同 |
| O2 | **High** | 高 | **部署形态默认无鉴权**：默认 `--host 0.0.0.0` 且 `AUTH_TOKEN` 为空 → 同网段任何人都能对你的电视发 `input text` / `monkey`。F1 只堵住了凭据外泄，没堵住「直接遥控」 | 改成默认只绑回环、或首启自动生成令牌，是**产品决策**（会改变「手机打开就能用」的体验），需你定 | 见 §4 的两个选项 |
| O3 | Medium | 高 | `atv_backend._call()` 持 `self._lock`（RLock）跨越 `run(coro, timeout=12)` → 电视休眠时一条命令能把后续所有按键堵最多 12s。这是「延迟」这条质量属性上的结构性瓶颈（该目标写在 commit `3b2fb26` 的标题里） | 放宽锁要先确认 pyatv 的 `interface.AppleTV` 是否可并发调用；没有官方结论，盲改可能把「慢」换成「坏」 | 单命令在电视休眠时不阻塞第二条按键（用计时测试固定），且连接/配对/关闭路径无竞态 |
| O4 | Medium | 高 | **无 CI、无提交钩子**：`check.sh` 全靠人跑，`.github` 不存在。本轮新增的行为测试因此随时可能被跳过 | 属于工程外部动作（要建工作流、可能拖慢你现有节奏），未擅自加 | 一次 PR 上 `check.sh` 自动跑并阻断失败 |
| O5 | Medium | 中 | 仓库里 checked-in 的 `android-native/ATVRemote-native.apk` 内含**过期前端**（`assets/chaquopy/app.imy` 里 `static/app.js` 19751 B 旧 + `static/static/app.js` 25522 B 新），而 `boot.py` 不设 `ATV_STATIC` → 直接装这个 APK 的用户拿到的是旧界面 | 需要重新构建 APK 才能刷新，同上无构建验证 | 重新构建并 `unzip -p … app.imy` 比对 md5 |
| O6 | Low | 高 | 构建不可移植：`android/build.sh` 硬编码 `$HOME/Library/Android/sdk`、`build-tools/35.0.0`、`brew --prefix openjdk@17`；`make_icon.swift:88` 硬编码 `/Users/a1-6/atv-remote/mac`；`android/res/values/strings.xml:4` 把 `http://192.168.1.104:8300` 烧进 APK；端口 8300 散在 8 处；`README:32` `-target arm64-apple-macos26.0` 与 `mac/Info.plist` 的 `LSMinimumSystemVersion 12.0` 互相矛盾 | 纯本机可用，换机器才痛；改动面广、收益偏运维 | 换一台干净机器按 README 能走完三条构建路径 |
| O7 | Low | 中 | 前端触摸坐标：`status.screen` 默认 1920×1080，只在 `!isApple && s.info.w` 时更新 → 非 1080p 设备（本机实测接的是 4128×2208 的 Quest 3）点按会偏移 | 未修：需要真机验证映射，改错比不改更糟 | 在非 1080p 设备上点按命中 |
| O8 | Low | 高 | 前端把用户输入的文本回显进 `#log` → 在电视上输密码时密码留在手机屏幕上；`index.html` 无 CSP | 属体验取舍（日志有用），未擅自去掉 | 敏感输入不回显或可开关 |
| O9 | Low | 高 | 版本标识三处不一致：README「v1.1.0」、`android` manifest `1.0`、`android-native` `2.0-native` | 一次性机械改，但缺「哪个才是权威」的信息 | 一处定义、其余引用 |

## 3. 复核过但**不成立**的指控（避免以讹传讹）

- **「前端用 innerHTML 渲染设备名/IP → XSS」**：不成立。所有 `innerHTML=` 写入点
  （`app.js:170-172,295,348,411,425`）赋的都是静态字面量，AGENTS.md 的 `textContent`
  约定是被遵守的。唯一的真注入点是 `index.html:8` 的 `__ATV_TOKEN__` 直替 —— 令牌来自环境变量/命令行（操作者可控，非网络可控），仍按纵深防御加了 HTML 转义。
- **「`.mimosa/` 快照里泄漏了凭据」**：不成立（上一轮已核）。命中的是 `atv_backend.py` 源码快照里的字典键名 `companion_cred`。
- **「3 处 unhandled promise rejection」**：只有 1 处成立（见 F13）。
- **「`send_keys` 的闭包捕获循环变量」**：不成立。`lambda atv, _n=…, _m=…` 用默认参数固定了取值，写法是对的。

## 4. 需要你拍的一件事

F1/O2 都指向同一个根因：**默认监听 `0.0.0.0` 且默认不鉴权**。三选一：

1. **保持现状**（手机开箱即用，风险由「同网段可信」这一假设兜着）——已完成 F1，凭据不再裸奔，但仍可被同网段直接遥控；
2. **首启自动生成令牌**并打印在终端/二维码里：安全性最大，代价是首次连接要多输一次令牌；
3. **默认只绑 `127.0.0.1`，要局域网访问显式加 `--lan`**：最小惊讶，代价是手机接入要改一行启动参数。

另外：8300 端口上正在跑的那个实例（PID 4924，9 月 10 日启动）**还是旧代码**，本轮修复要重启该 LaunchAgent 才生效 —— 需要我重启吗？
