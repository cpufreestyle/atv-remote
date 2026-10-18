# 交接 — ATV Remote（2026-10-18 午轮）

> 本轮一句话：小米 K90 上「点了 Apple TV 页签没反应」的根因不是页签逻辑，是
> static/app.js:2116 一处漏 const 的顶层裸赋值在 use strict 下当场中断整个脚本，
> 3304 行的页签 click 绑定从没注册。已修 + 已加整文件顶层可执行性兜底测试，SW 版本顶到 v9。
> 代码/harness 级全绿；Mac Chrome 端到端（SW v9 → 真实鼠标点 🍎 切 pane、零异常）也已闭环；
> 真机验收（重装 APK）留给下一步。上一轮（2026-09-26，APK 安装链路/
> 图标/下载三处 bug）的交接已并入 git 历史，不再赘述。

## 0. 一分钟速览

| 项 | 值 |
|---|---|
| 根因 | static/app.js:2116 VOLSET_MAX_FALLBACK = 15 漏 const（第 2 行 use strict → 顶层 ReferenceError 中断整文件） |
| 引入 | dd3b99e（2026-10-02 音量按格设置），存活 16 天 |
| 修复 | app.js:2116 补 const；static/sw.js CACHE v8→v9；新增 tests/top_level_harness.js + tests/test_top_level.py |
| 新不变式 | spec.md I24（Apple TV 能力门禁，补登）+ I25（前端顶层整文件可执行） |
| 测试 | ./check.sh 全绿：系统 python3 698 OK skipped=15；.venv 702 OK skipped=7 |
| 内嵌副本 | ./sync-native.sh --check 一致（sw.js 已 v9） |
| git | main 领先 origin 11 提交；只推 gitee（GitHub SSL 阻断）；本轮改动未提交 |
| 服务 | Mac .venv 服务端 192.168.1.111:8300 仍是 Apple TV 控制点 |
| 待办 | 真机验收：重建 APK + 清数据重装 + 点 🍎 验证切 pane（重装会清 state.json） |
| 端到端 | Mac Chrome：SW 自更新 v9 后 CDP 真实鼠标点 🍎 → pane 切换、Runtime.exceptionThrown 零事件（需先跳过 #coachMark 引导层，它是全屏 modal 会吞点击，产品正常行为） |

## 1. 根因链（从表象到骨）

1. 表象：小米 K90 native App 点了 🍎 页签没反应；页面却能正常滚动。
2. 骨因：app.js:2116 VOLSET_MAX_FALLBACK = 15 漏 const。文件第 2 行就是 use strict，顶层
   裸赋值抛 ReferenceError，整个脚本当场停在这一行——3304 行的 .devtab 页签 click 绑定及其后
   所有初始化从未执行。原生滚动不走 JS，所以页面照常滚、点击全被吞。
3. 为何 16 天没人发现：
   - 现有 23 个 harness 全按 atv-xxx:begin/end 标记抽段执行，整文件顶层路径从无行为证据；
   - node --check 只查语法、不执行；
   - Service Worker 的 stale-while-revalidate 持续发放旧 app.js，掩盖症状。
4. 表层放大器：sw.js:97 if (hit) return hit——HTTP 响应虽是 no-store，但管不住 SW 自己的
   Cache Storage。表现即「修了源码、设备仍是旧的」。
5. 关键判定（证伪第二个断点 + 端到端闭环）：
   - tests/top_level_harness.js 用 vm 沙箱整文件执行当前 app.js：顶层不抛、两个 .devtab
     均注册 click——证明当前源码没有第二个断点；Chrome 里 1839~3304 之间看到的第二个
     报错是 SW 旧缓存，不是当前文件。
   - Mac Chrome 端到端闭环（午轮末补）：等旧 SW 自更新到 v9 后，Cache Storage 里 app.js:2116
     已是 const 版；主 world 里 VolSet/pageVisible 均正常初始化（顶层完整执行）；
     CDP Input.dispatchMouseEvent 真实鼠标点击 🍎 → #pane-appletv 加 on、#pane-android 去 on，
     Runtime.exceptionThrown 零事件。
   - Mac Chrome 复现「点击无效」的最后一个混淆变量：#coachMark.modal.coach 首次访问引导层
     是全屏 modal，会吞掉页签区域所有真实鼠标点击（产品正常行为，有「跳过」按钮）。原生
     btn.click() 不经 hit-testing 所以能过——别被它带偏去改绑定代码。

## 2. 本轮改动（文件级）

| 文件 | 改动 |
|---|---|
| static/app.js | :2116 补 const（根因修复） |
| static/sw.js | CACHE v8→v9（强制 SW 更新发新 app.js，修复才落地） |
| tests/test_pwa.py | 版本下限 assertGreaterEqual(..., 8) 改 9 |
| tests/test_download.py | 钉 CACHE = atv-shell-v9 |
| tests/top_level_harness.js | 新增：vm 沙箱整文件执行 app.js，断言顶层不抛 + 两页签 click 注册 |
| tests/test_top_level.py | 新增：驱 harness + 静态挡顶层裸赋值 + 钉住 use strict（3 项） |
| tests/atv_gate_harness.js、tests/test_atv_gate.py | 上一轮 I24 能力门禁测试（未提交，本轮一并纳入 spec） |
| docs/spec.md | 补 I24、新增 I25；验证矩阵/基线（702/698）/harness 数（23）/最近核验 |
| docs/harness.md | 新增 4.13；Drift 台账补 D4；最近核验/基线 |

未跟踪：tests/atv_gate_harness.js、tests/test_atv_gate.py、tests/top_level_harness.js、
tests/test_top_level.py（均在 tests/ 下，unittest discover 已纳入，702/698 已含）。

## 3. 验证结论

已核验（本机，可复现）：
- node tests/top_level_harness.js → 3 项 ok，ALL_TOP_LEVEL_CASES_PASSED
- python3 -m unittest tests.test_top_level -v → 3 tests OK
- 兜底实证：把裸赋值 bug 注回 → harness 红且报错与真机 console 一字不差；还原 → 绿
- ./check.sh 全绿：系统 python3 Ran 698 tests OK (skipped=15)；.venv Ran 702 tests OK (skipped=7)
- ./sync-native.sh --check → 内嵌副本与根目录一致（含 v9 sw.js）
- curl 127.0.0.1:8300/static/app.js → 已是 const VOLSET_MAX_FALLBACK = 15，Cache-Control: no-store
- 端到端（午轮末补）：Mac Chrome 里 SW 自更新 v9 → Cache Storage app.js:2116 已是 const →
  主 world 顶层完整执行 → 跳过 coachMark 后 CDP 真实鼠标点 🍎 切 pane，零未捕获异常

待办（未核验，需真机）：
- 小米 fe0084a2 重装 APK 后点 🍎 验证切 pane（见 §5）
- 已还原测试残留：设备 screen_off_timeout 1800000→60000；Mac launchctl remove atv.recv（端口 8798 取证接收器已停）

## 4. 未决 / 需决策

- 强塞 pyatv 进 APK vs 维持 -PnoAppletv 既定契约：未批准不要动。chaquo 仓库 cryptography 最高 42.0.8，pyatv 0.18.0 要 >=44.0.1，Android 无 wheel（见 harness.md 4.12/D3）。
- 重装 APK 会清 App 内 state.json（最近设备列表没了）——是否现在做由用户定。

## 5. 如何继续（下一步 exact 命令）

真机验收（会清 state.json，先确认）：

```bash
cd android-native && JAVA_HOME=/opt/homebrew/opt/openjdk@17 ./gradlew assembleRelease -PnoAppletv
adb -s fe0084a2 uninstall com.atv.remote.native
adb -s fe0084a2 install app/build/outputs/apk/release/app-release.apk
# 启动 App → 点 🍎 → 截图确认 .devpane 切到 pane-appletv；console 零 error
```

注意：native App 用的是 APK 内嵌的 static/，不是 Mac 的 8300——所以对 native App 必须重装
（清 SW 缓存 + 换内嵌 app.js）；只有浏览器访问 192.168.1.111:8300 才能靠 sw.js v9 自动更新
（后台+前台 App 两次，onResume 会 webView.reload()）。

提交（只推 gitee）：

```bash
git add -A && git status   # 确认无 state.json / debug.keystore
git commit -m 'fix(ui): app.js:2116 补 const 修复顶层中断（无法点到 Apple TV）+ sw.js v9 + 顶层可执行性兜底测试'
git push gitee main
```

## 6. 环境事实与坑（交接给下一轮）

- 设备 fe0084a2（Redmi K90/2510DRK44C，1156×2510@480dpi，1 CSS px = 3 设备 px）；包名 com.atv.remote.native，Activity com.atv.remote.NativeActivity；NativeActivity.onResume() 有 webView.reload()（每次 am start 重载回默认 Android pane）
- Mac LAN 192.168.1.111；git main 领先 origin 11（只推 gitee，GitHub SSL 阻断）
- exec 工具入参是裸 JS（tools.exec_command({cmd,workdir})），JSON 入参 SyntaxError；命令 30s 被杀，check.sh/gradle 用后台写日志或 session 轮询
- apply_patch：Add File 不带 @@；write_stdin 的 session_id 在此环境按 i32 传（字符串会被拒）
- cua_repl playwright 的 typeof 探针在 isolated world 不可信（全返 undefined 不抛 TDZ），别用它探测顶层词法环境
- cua_repl 的 tab.playwright.evaluate 跑在 isolated world：document 可见，但 document.defaultView 的 navigator/fetch/caches 全 undefined；探测主 world 词法环境必须走 raw CDP（await tab.capabilities.get("cdp")，先 agent.documentation.get("capabilities/tab/cdp") 读文档再 send；白名单外的方法直接报 not supported，如 Storage.clearDataForOrigin）
- tab.dev.logs 是 tab 创建起的累积缓冲（时间戳是旧的），判断「本次操作有没有新 error」要用 CDP readEvents({methods:["Runtime.exceptionThrown"]})
- 页面 #coachMark（使用指引）未跳过时是全屏 modal，吞掉所有真实鼠标点击；playwright locator.click 对它不超时但事件被吞，别据此判定「绑定没生效」
- rm 被沙箱拒绝；zsh 里 echo === 被 = 展开，分隔符加引号

