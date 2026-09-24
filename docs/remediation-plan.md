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
| P0 | **决定默认暴露面**（三选一，见 review §4）：保持现状 / 首启自动生成令牌 / 默认只绑回环 +`--lan` | 低（≤30 行） | 高 | **产品决策**，影响手机接入体验 |
| P0 | 重启 8300 上的 LaunchAgent，让以上修复真正生效（当前运行实例仍是 9 月 10 日的旧代码） | 秒级 | 高 | 会打断正在用的遥控会话 |
| P1 | 消灭 5 份副本：`build.gradle` 增加从根目录生成 `src/main/python` 的 `Copy` 任务（或直接符号链接），随后停止跟踪这些副本 | 中 | 高 | **必须跑一次 `gradle assembleDebug` 验证**，本轮无构建验证 |
| P1 | 重建 `android-native/ATVRemote-native.apk`（现含过期前端） | 中 | 高 | 同上（需 SDK + JDK17） |
| P2 | `atv_backend._call()` 不要把 RLock 横跨 12s 等待（电视休眠会堵住按键） | 中高 | **低**（涉及 pyatv 线程安全） | 先确认 `interface.AppleTV` 可否并发；否则改成「超时不重试 + 快速失败」这一半可先行 |
| P2 | 加 CI（`.github/workflows/check.yml` 跑 `./check.sh`）或 pre-commit 钩子 | 低 | 高 | 需确认你希望在哪一层强制 |
| P3 | 构建可移植性：SDK/JDK 路径与 `arm64-apple-macos26.0` 参数化、端口与默认设备地址不再硬编码（8 处） | 中 | 高 | 换机验证才有意义 |
| P3 | 触摸坐标按真实分辨率映射（默认 1920×1080 在非 1080p 上偏移） | 低 | 高 | 需真机验证 |
| P3 | 输入文本不回显进日志（电视密码场景）；`index.html` 补 CSP | 低 | 高 | 体验取舍 |
| P3 | 版本标识统一为单一来源 | 低 | 高 | 需确认权威版本号 |

## 质量属性场景（用于以后判定「有没有退化」）

| 属性 | 场景 | 可度量响应 | 现状 |
|---|---|---|---|
| 安全性 | 同网段未授权设备拉取安装包 | 不含配对凭据；开启令牌时 401 | **已达成**（F1，有回归测试） |
| 安全性 | 伪造 `Host` 投递安装脚本 | 任意文本不进入被 `bash` 执行的脚本 | **已达成**（F3） |
| 性能（延迟） | 电视休眠时连按方向键 | 第二键不被第一键的 12s 超时阻塞 | **未达成**（O3/F8 只缓解不改结构） |
| 可用性 | 服务端进程挂掉 | 手机界面 ≤1 个轮询周期内显示「连不上服务端」 | **已达成**（F12） |
| 可维护性 | 修改 `server.py`/`static/*` 后打包 APK | 内嵌副本不一致时 `check.sh` 失败 | **已达成**（F9） |
| 可维护性 | 同一源码跨 4 种交付形态 | 依赖版本单一来源 | **已达成**（F10）；文件副本 **未达成**（O1） |
| 可观测性 | 500 错误 | 服务端日志有堆栈、客户端只看到通用文案 | **已达成**（F5）；但访问日志仍全静默（有意为之） |
| 兼容性 | HTTP/1.1 keep-alive 复用 | 异常 body 后同连接下一请求仍可正确解析 | **已达成**（F2/F3 测试） |
