# 修复计划（按爆炸半径 / 业务影响 / 可逆性 / 成本排序）

配套：`architecture-review.md`（发现与证据）、`risk-map.dot`（风险关系图）

## 已完成（本轮，`./check.sh` 全绿 + 7 例行为回归）

优先级 P0，全部可逆（单文件改动，`git revert` 即可）：

1. **凭据不再无条件下发** —— `/bundle.tgz` 里的 `state.json` 改由 `bundle_files()` 单点决定，仅启用 `--token` 时进包。
   验收：`tests/test_http_hardening.py::test_bundle_excludes_credentials_without_token`；
   取证复现：`curl -s http://<本机局域网IP>:8300/bundle.tgz | tar tz | grep state.json`（修复前有输出）。
2. **keep-alive 帧完整性** —— 超大 body / 非法 `Content-Length` → 400 且显式关连接；未知路由判 404 前也读完 body。
   验收：`test_oversized_body_closes_connection`、`test_bad_content_length_is_business_error`、`test_unknown_route_still_drains_body`。
3. **`/install` 脚本的 Host 白名单** + 回退到「本次实际被访问的地址」。
   验收：`test_install_rejects_unsanitized_host`。
4. **约定与护栏补齐** —— `handle_cmd` 持 `state_lock`；Apple TV 分支对齐 Android 的限长/限量/字符白名单；`keyboard_focus` 退出 8s 轮询改 TTL 缓存；500 不外泄堆栈但堆栈落 `server.log`；cookie 加 `HttpOnly`。
5. **漂移从「靠自觉」变成「跑不过」** —— 新增 `./sync-native.sh`（唯一的同步原语），`check.sh` 逐字节比对内嵌副本。
6. **依赖对齐** —— `pyatv==0.18.0` / `qrcode==8.2` 统一到 `requirements.txt` 的口径（Gradle、Termux 安装脚本、README）。
7. **前端** —— 令牌改走 `X-ATV-Token` 头（URL 里不再留痕）、轮询加在途锁、服务端挂了要点灰说明、补 `catch`。
8. **文档纠错** —— `./gradlew`→`gradle`、`start.sh` 标注 Termux 专用、凭据规则改写、AGENTS.md 增「内嵌副本」「行为回归」两条约定。

## 待办（需你决策或需构建验证）

| P | 事项 | 成本 | 可逆性 | 阻塞点 |
|---|---|---|---|---|
| ✅ | ~~决定默认暴露面~~ → 已选「首启自动生成令牌」（见第二轮进度） | — | — | 已完成 |
| P0 | 重启 8300 上的 LaunchAgent，让以上修复真正生效（当前运行实例仍是 9 月 10 日的旧代码） | 秒级 | 高 | 会打断正在用的遥控会话 |
| P1 | 消灭 5 份副本：`build.gradle` 增加从根目录生成 `src/main/python` 的 `Copy` 任务（或直接符号链接），随后停止跟踪这些副本 | 中 | 高 | **必须跑一次 `gradle assembleDebug` 验证**，本轮无构建验证 |
| ✅ | ~~重建 `android-native/ATVRemote-native.apk`~~ → 已用降级参数重建并替换 | — | — | 已完成（见第二轮进度 O5） |
| P2 | `atv_backend._call()` 不要把 RLock 横跨 12s 等待（电视休眠会堵住按键） | 中高 | **低**（涉及 pyatv 线程安全） | 先确认 `interface.AppleTV` 可否并发；否则改成「超时不重试 + 快速失败」这一半可先行 |
| P2 | 加 CI（`.github/workflows/check.yml` 跑 `./check.sh`）或 pre-commit 钩子 | 低 | 高 | 需确认你希望在哪一层强制 |
| P3 | 构建可移植性：SDK/JDK 路径与 `arm64-apple-macos26.0` 参数化、端口与默认设备地址不再硬编码（8 处） | 中 | 高 | 换机验证才有意义 |
| P3 | 触摸坐标按真实分辨率映射（默认 1920×1080 在非 1080p 上偏移） | 低 | 高 | 需真机验证（非 Quest 3 设备） |
| P3 | 输入文本不回显进日志（电视密码场景）；`index.html` 补 CSP | 低 | 高 | 体验取舍 |
| P3 | 版本标识统一为单一来源 | 低 | 高 | 需确认权威版本号 |

## 质量属性场景（用于以后判定「有没有退化」）

| 属性 | 场景 | 可度量响应 | 现状 |
|---|---|---|---|
| 安全性 | 同网段未授权设备拉取安装包 | 不含配对凭据；开启令牌时 401 | **已达成**（F1，有回归测试） |
| 安全性 | 伪造 `Host` 投递安装脚本 | 任意文本不进入被 `bash` 执行的脚本 | **已达成**（F3） |
| 性能（延迟） | 电视休眠时连按方向键 | 第二键不被第一键的 12s 超时阻塞 | **已达成**（O3/F8：`_call()` 进锁前查超时窗口，秒级返回「设备无响应，可重试」；RLock 仍横跨调用是 pyatv 并发安全的已知取舍，结构性放开待确认） |
| 可用性 | 服务端进程挂掉 | 手机界面 ≤1 个轮询周期内显示「连不上服务端」 | **已达成**（F12） |
| 可维护性 | 修改 `server.py`/`static/*` 后打包 APK | 内嵌副本不一致时 `check.sh` 失败 | **已达成**（F9） |
| 可维护性 | 同一源码跨 4 种交付形态 | 依赖版本单一来源 | **已达成**（F10）；文件副本 **未达成**（O1） |
| 可观测性 | 500 错误 | 服务端日志有堆栈、客户端只看到通用文案 | **已达成**（F5）；但访问日志仍全静默（有意为之） |
| 兼容性 | HTTP/1.1 keep-alive 复用 | 异常 body 后同连接下一请求仍可正确解析 | **已达成**（F2/F3 测试） |

## 第二轮进度（2026-09-24）

| 项 | 状态 | 证据 |
|---|---|---|
| O1 消灭副本 | **机制已落地** | `android-native/app/build.gradle` 的 `syncPythonSrc`（Copy 任务，`preBuild.dependsOn`）从根目录生成副本；5 份副本已停止跟踪并 gitignore；标记传播实测（改根 `static/style.css` → 副本随之更新） |
| O5 APK 重建 | **完成（降级版）** | chaquo.com 仍双路不通，改用 `-PnoAppletv`（跳过 pyatv，Apple TV-on-Android 关闭、Android TV 全功能）+ `-PpypiMirror`（qrcode 走腾讯镜像）重建：`ATVRemote-native.apk` 已换成新构建（27MB），验收通过 —— `app.imy` 内 `static/*` 三件与根目录逐字节一致、`server.pyc`/`atv_backend.pyc` 头部源码尺寸与当前文件相符、qrcode 在 `requirements-common.imy`、pyatv 确认不在包内。chaquo.com 恢复后去掉两个参数重打即可补回 Apple TV 功能 |
| O3 锁横跨 12s | **半程（用户感知已闭环）** | 「快速失败」半边已落地（`atv_backend._call` 进锁前判断超时窗口，`tests/test_atv_fastfail.py` 覆盖）；结构性放开 RLock 仍需确认 pyatv 并发安全，维持原状 |
| O4 CI | **完成** | `.github/workflows/check.yml`（push/PR 跑 `./check.sh`）；`check.sh` 增加 `.venv` 下的 unittest（覆盖 pyatv 路径） |
| O6 可移植性 | **完成（本次可验证部分）** | `android/build.sh` 的 SDK/build-tools/platform/JDK 全部可探测与环境变量覆盖；`make_icon.swift` 去掉绝对路径；`strings.xml` 不再预填某台机器的 IP；`Info.plist` 最低系统版本与构建目标对齐为 26.0；Gradle wrapper 钉 8.14.3（README 原「仓库里没有 gradlew」已过时） |
| O7 触摸坐标 | **未动（需真机）** | 依赖 Quest 3 实测，另行安排 |
| O8 密码回显 | **完成** | 隐私模式（日志脱敏 + 输入框掩码） |
| O9 版本标识 | **完成** | 根目录 `VERSION` 单一来源，gradle 与 build.sh 都引用 |
| O2 默认暴露面 | **完成（取方案 2）** | 绑 0.0.0.0/:: 且未指定令牌时首启自动生成，存 `state.json` 的 `token` 键（重启不变）；本机回环免令牌；`/api/setup` + 「📱 装到手机」里的带令牌二维码把首次接入成本压到「扫一次码」；`--no-token` / `--token ""` 可显式退回无鉴权。`resolve_token()` 为单一决策点，`tests/test_macro_token.py` 钉住四个分支 |
| D1 一键宏 | **完成** | `/api/cmd` 的 `macro` 类型：请求线程同步校验（400 即时），`_macro_worker` 线程串行执行，单步失败不中断、延时可取消；预置 5 个场景，自定义宏存 localStorage |
| B3 服务自发现 | **部分完成（平台限制内）** | macOS 用 `dns-sd` 广播 `_atv-remote._tcp`（`start_mdns()`）；其余平台静默跳过。接入主路径仍是二维码，广播只加分 |

功能进度（docs/feature-proposals.md）：**提案 12 条全部落地** —— 第一批（智能唤醒 / 睡眠定时 / 隐私模式）、第二批（mDNS 扫描配对 / 自动重连 / 剪贴板短语 / 语音输入 / 触觉）、第三批（D1 一键宏 / B3 服务自发现）已实现并提交（`ea1a84f`、`eaf94a8` 及本轮）；C2 iOS/Mac 快捷指令已落地：README「🎙 iOS/Mac 快捷指令对 Siri 说话遥控」一节给了 4 个高频动作的
POST /api/cmd JSON 配方（暂停 / 静音 / 音量加 / 半小时后关电视），含开启令牌时的
X-ATV-Token 头部填法——零代码、零上传，iPhone/Watch 通用。
C1 Mac 菜单栏 App 已实现：`mac/main.swift` 的 `StatusBarController`（NSStatusItem + NSPopover，
D-pad / 播放 / 音量 / 系统键 + 当前设备名，全部走 `127.0.0.1:8300` 的 HTTP API），
`swiftc -O -target arm64-apple-macos26.0` 编译通过。

第一批（智能唤醒 / 睡眠定时 / 隐私模式）与第二批（mDNS 扫描配对 / 自动重连 / 剪贴板短语 / 语音输入）均已实现并提交（`ea1a84f`、`eaf94a8`）。
