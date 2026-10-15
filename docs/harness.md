# ATV Remote — 验证面与评审台账（harness.md）

> 配套 docs/spec.md：spec.md 定义「应当具备的契约」，本文件记录「如何验证、覆盖到哪、
> 最近一次核验结论」。
> harness 产物根目录 .qoder/better-harness/ 与 .mimosa/ 已 gitignore，不在本台账内。
> 最近核验：2026-10-15（布局优化：I16 / I17，见第 4.5 节）

## 1. 一键验证命令

```bash
./check.sh                          # 全量基线（下面几条依序执行）
python3 -c 'import server, atv_backend'   # 后端导入自检
node --check static/app.js          # 前端语法
node --check static/sw.js           # Service Worker 语法
python3 -m unittest discover -s tests    # 行为回归（638 项，skipped 11）
./sync-native.sh --check            # 内嵌副本逐字节比对
./sync-native.sh                    # 漂移时同步，并顺带跑一遍 check.sh
.venv/bin/python -m unittest discover -s tests   # pyatv 分支复跑
```

起真服务做人工冒烟（可选，需要真实设备）：python3 server.py --host 0.0.0.0 --port 8300

## 2. 测试资产盘点

| 类别 | 数量 | 说明 |
|------|------|------|
| Python 单测文件 | 40 | tests/test_*.py |
| Python 用例总数 | 642 | 其中 skipped 12（依赖 pyatv 或 Pillow）；.venv 下 7 |
| node harness | 20 | tests/*_harness.js，与宿主测试一一对应 |
| 图标/规格守护 | 1 | tests/test_icons.py 另守护 assets/ 与桌面脚本 |
| 结构守护 | 2 | tests/test_route_contract.py（I14 派发面冻结）、tests/test_delivery_manifest.py（I13 三处交付清单对账） |

契约防漂移：`tests/test_icons.py::SpecContractTest` 把 `docs/spec.md` 第 3.2 节的写端点表
与 `server.ROUTES` 逐条对账，漏记或虚报都会让单测变红。新增写端点时必须同步更新 spec.md。

harness 的驱动方式是「宿主 Python 测试经 subprocess 跑 node」：harness 从 static/app.js 
按标记或函数名原样切出代码，配假 DOM 与假 api() 执行，所以有真行为证据，
而不是源码字符串断言。改函数名会让 harness 直接失败，这正是目的。

### 2.1 harness 与宿主测试映射

| harness | 宿主测试 | 断言哨兵 |
|---------|----------|----------|
| backup_harness.js | test_export_import.py | ALL_BACKUP_CASES_PASSED |
| collapse_harness.js | test_collapse.py | ALL_COLLAPSE_CASES_PASSED |
| diagnostics_harness.js | test_diagnostics.py | ALL_DIAG_CASES_PASSED |
| empty_harness.js | test_empty.py | ALL_EMPTY_CASES_PASSED |
| fav_harness.js | test_favorites.py | ALL_FAV_CASES_PASSED |
| favorder_harness.js | test_favorder.py | ALL_FAVORDER_CASES_PASSED |
| gamepad_harness.js | test_gamepad.py | ALL_GAMEPAD_CASES_PASSED |
| install_harness.js | test_install.py | install harness: all passed |
| intent_harness.js | test_intent.py | ALL_INTENT_CASES_PASSED |
| macro_dryrun_harness.js | test_macro_dryrun.py | ALL_DRYRUN_CASES_PASSED |
| notif_queue_harness.js | test_notif_queue.py | ALL_NOTIF_CASES_PASSED |
| padsens_harness.js | test_padsens.py | ALL_PADSENS_CASES_PASSED |
| palmark_harness.js | test_palmark.py | ALL_PALMARK_CASES_PASSED |
| panemotion_harness.js | test_panemotion.py | ALL_PANEMOTION_CASES_PASSED |
| perf_waterfall_harness.js | test_perf.py | ALL_PERF_CASES_PASSED |
| reconnect_ui_harness.js | test_reconnect_ui.py | OK <n> cases |
| sheet_harness.js | test_sheet.py | ALL_SHEET_CASES_PASSED |
| undo_harness.js | test_undo.py | ALL_UNDO_CASES_PASSED |
| volume_slider_harness.js | test_volume_set.py | ALL_VOLUME_CASES_PASSED |
| wol_harness.js | test_wol.py | ALL_WOL_CASES_PASSED |

## 3. 覆盖率快照（手工盘点，未采 coverage）

| 目标 | 规模 | 覆盖情况 |
|------|------|----------|
| server.py | 111 KB | HTTP 层、路由、令牌、宏、输入校验、诊断、导入导出均有行为测试 |
| atv_backend.py | 18.2 KB | 降级与快速失败有测试；协议细节靠 .venv 分支 |
| atv_media.py | 8.7 KB | 纯解析函数：媒体会话、两条音量读通道、按格夹取与命令降级链；由 test_media_session / test_volume / test_volume_set 通过 server 再导出的同名符号覆盖 |
| static/app.js | 大 | 20 个 harness 覆盖主要交互；无通用 JS 覆盖率统计 |
| static/index.html、style.css | 中 | 结构、ARIA、图标顺序、选择器一致性有静态断言 |
| android/、android-native/、mac/ | 多语言 | 构建与内嵌一致性用 sync-native.sh 把守 |

盲区见 spec.md 第 6 节。

## 4. 最近验证结论


### 4.1 2026-10-07

| 项 | 结论 |
|----|------|
| HEAD | 69dca04 桌面图标修复 |
| 全量单测 | python3 -m unittest discover -s tests 得 603 passed、skipped 11 |
| 内嵌副本 | ./sync-native.sh --check 得「内嵌副本与根目录一致」 |
| 桌面图标 | NSWorkspace 实渲染修前为 AppleScript 羊皮纸、修后为遥控器图标；codesign 验证通过 |
| 清理 | 删除 android-native 构建缓存、.gradle、__pycache__ 与 .DS_Store，释放 330 MB，未触碰任何入库文件 |
| 事实核对 | 端点表、不变式、令牌策略逐条对照源码，与 spec.md 一致 |

### 4.2 2026-10-13（媒体解析外提 + 交付清单跟着 import 走）

| 项 | 结论 |
|----|------|
| 工作树 | fd79857 之上：新增 atv_media.py 与两个结构守护（当时未提交，后由 2dc3ca3 落盘） |
| 全量单测 | python3 -m unittest discover -s tests → 624 passed、skipped 11；.venv 分支同样 624、skipped 7 |
| 一键基线 | ./check.sh 全绿（含 ./sync-native.sh --check「内嵌副本与根目录一致」） |
| 派发面 | test_route_contract 与 32 条冻结三元组逐条相等；改动抽取器后做过红绿：删掉 /api/macros 分支会挂 2 例 |
| 交付清单 | test_delivery_manifest 15 例把 /bundle.tgz、build.gradle 的 syncPythonSrc、sync-native.sh 三处与 server.BUNDLE_PY 对账；副本里根目录已删的 icon-maskable.svg 由同步清掉 |
| 运行实例 | 10-13 07:36 `launchctl kickstart -k` 重启（PID 1165）；`GET /bundle.tgz` 成员 14 个、含 `atv_media.py`、不含 `state.json`，包内 server.py 与磁盘 sha256 相同
| 真机复探针 | 拆分前 22 个只读端点复跑：status / Content-Type / JSON 字段集 / 本轮 Content-Length 与 body 一致性全部通过；唯一字段级差异是 `/api/status` 多出 `version`（HEAD 的 `app_version()`，旧进程起于 10-08 所以基线里没有，与外提无关）；字节数变化 4 处均为设备实时状态或包内容变化。`/api/volume`、`/api/nowplaying` 因当前设备是 Apple TV 仍回 `{"connected": false}`，与基线一致
| 体量 | server.py 2648 → 2558 行（外提 −188、清单闭包 +54）；atv_media.py 167 行纯函数，server.py 再导出所以既有引用点（含 tests/）不用改 |

### 4.3 2026-10-13（I15 已收敛：pyatv 错误文案不再依赖 pyatv）

| 项 | 结论 |
|----|------|
| 动机 | spec 第 6 节第一条缺口：实测系统 python3 下 `dir(AppleTvManager)` 里没有 `_friendly`，
  而它是所有 pyatv 异常的唯一出口，故“错误信息好不好懂”在无 pyatv 环境里零覆盖 |
| 改动 | `_friendly` 静态方法外提为模块级纯函数 `friendly_error()`，原方法保留为薄委托
  （既有调用点不动）；顺带修掉 `None` 入参会抛 AttributeError 的隐患 |
| 新测试 | `tests/test_atv_fastfail.py::FriendlyErrorTest` 5 例（不跳过）：auth/credentials 两类词都要映射、
  keyboard 映射到动作提示、认不出的原样透出、空/空串不崩、静态方法委托一致 |
| 双环境结论 | 系统 python3：9 tests OK（4 项 pyatv 用例跳过，FriendlyErrorTest 5 项全跑）；
  .venv：9 tests OK（全跑）。即无 pyatv 时这五条也有真覆盖 |
| 全量回归 | 系统 python3 629 passed / skipped 11；.venv 629 passed / skipped 7（均比收敛前 +5） |

### 4.4 2026-10-15（GitHub 推送被网络阻断，已落到台账）

| 项 | 结论 |
|----|------|
| 本地 HEAD | `4ff76ee`（工作区干净） |
| Gitee | `4ff76ee` 已推送成功，与本地一致 |
| GitHub | **推送失败**：`LibreSSL SSL_connect: SSL_ERROR_SYSCALL`，https 与 ssh(443) 均不可达 |
| 诊断 | `dig github.com` 返回 `198.18.0.73`（Clash/Surge 类客户端的 fake-ip 段）；
  TCP 能 ping 通（0.35ms）但 TLS 握手被拦。本圸 socks 代理端口 1082 有监听但
  `SOCKS error 1`（代理本身未上线/未选节点）。即网络环境问题，非代码问题 |
| 已尝试 | 直连 / `--resolve` 固定真实 IP / `sslVerify=false` / HTTP1.1 / socks4+socks5h /
  扫描 7 个常见代理端口 / 等待重试（超过 18 小时、多轮）——均失败 |
| 待办 | 网络恢复后执行 `git push origin main`（本地已有完整提交，无需其余操作） |

### 4.5 2026-10-15（布局优化：双列归属 + 首次进入频率折叠）

| 项 | 结论 |
|----|------|
| 动机 | 13 张卡片宽屏下按 DOM 顺序左右横跳，且首次进入 9 张次级卡全展开——「简洁高效」两条都不成立 |
| 改动 | style.css 宽屏媒体查询给每张卡显式 grid-column；app.js 新增 collapseDefaults()，collapseRestore() 在「从没存过」时用它 |
| 新增不变式 | I16 双列归属（不落 auto-flow）、I17 首次进入频率折叠（存过则以用户为准） |
| 新测试 | tests/test_collapse.py::FirstRunDefaultTest 4 例 + collapse_harness.js 3 例 + test_icons.py::WideLayoutTest 3 例；基线 631 → 638 |
| 行为核验 | node 脚本按 CSS 规则推导演练：remoteCard/keypad 跨列，kb/fav/apps 左列，tools/gp/sleep/wol/perf/macros 右列 |
| 折叠核验 | 8 场景全过：首进收 4 张低频、存过空数组保持全展开、只折用户折的那张、坏 JSON 不误折、未知 id 不过界 |
| 双环境 | 系统 python3 638 passed / skipped 11；.venv 638 passed / skipped 7 |
| 副本 | ./sync-native.sh --check 一致（static/ 三件已同步进内嵌副本） |

踩到的两个坑（写下来免得复发）：

1. **给无 id 卡片加 id 会踩测试**：`test_keypad_card_not_collapsible` 靠 `split('<section class="card">')` 认卡片，
   加 id 或类名都会让那段断言失效。改用 `:has(> .brow [data-key="4"])` 选择器，标记零改动。
2. **`nth-of-type` 数序是陷阱**：卡片增删会让序号静默错位。归属要么写显式 id，要么用 `:has()` 这类结构选择器。


### 4.6 2026-10-15（反编译 tvremoted → I18：键盘聚焦改事件驱动）

| 项 | 结论 |
|----|------|
| 参照物 | `/usr/libexec/tvremoted`（macOS Apple TV 遥控系统守护进程，REA 静态分析） |
| 关键发现 | 原生 `TVRXKeyboardControllerDelegate` 全是推送：beganTextEditing / didUpdateAttributes / didUpdateText / endedTextEditing，没有任何轮询 |
| 对应 pyatv | `KeyboardListener.focusstate_update(old, new)` + `atv.keyboard.listener = obj` |
| 改动 | `atv_backend.py` 新增 `KeyboardFocusListener`，connect 时注册、close 时反注册并清缓存；`keyboard_focus()` 推送值优先，轮询只在无推送值时兜底 |
| 为什么不全推送 | 监听器只能在连接建立后注册（atv.keyboard 此时才存在），断线重连与电视侧静默变化都可能丢推送，所以保留轮询兜底 |
| 新测试 | `tests/test_atv_fastfail.py::KeyboardFocusPushTest` 4 例（推送置值 / 短路轮询 / 回调异常不外泄 / 断开清缓存）；无 pyatv 环境整类跳过 |
| 双环境 | .venv 642 passed / skipped 7；系统 python3 638 passed / skipped 12（推送链路 4 例随 pyatv 跳过） |
| 反编译边界 | 仅静态分析系统自带二进制，用于提炼协议语义与架构模式；未破解授权、未复制代码进生产 |

反编译还看到但暂未落地的（记录备查）：`TVRCKeyboardAttributes`（电视端输入框属性，可驱动 inputmode/掩码）、
`throttleSeconds`（授权挑战节流）、`enableTVRemoteOnLockscreen`（锁屏遥控）。前两条依赖 pyatv 暴露对应能力，
第三条是 macOS 侧配置而非遥控协议，均需另行评估。

## 5. Qoder better-harness 台账

来源：.qoder/better-harness/2026-08-26/220155-atv-remote/findings.json
（session-limited 只读评审，3 个 finding，5 个维度）

| id | 严重度 | 状态 | 证据或说明 |
|----|--------|------|-------------|
| no-agent-routing-instructions | High | 已解决 | AGENTS.md 覆盖启动命令、源码结构、构建路径、安全约束 |
| no-automated-validation | Medium | 已解决 | check.sh 与 642 项单测；本文件与 spec.md 固化为契约，并有 SpecContractTest 防漂移 |
| untracked-native-module | Low | 已解决 | android-native/ 已入库，README 有独立章节 |

## 6. Drift 台账（spec 与代码的偏差）

| 编号 | 偏差 | 状态 | 处理 |
|------|------|------|------|
| D1 | check.sh 全量跑通耗时约 30 秒，单次 exec 容易被超时截断 | 已知 | 分段复跑：先 python3 分支，再 .venv 分支 |
| D2 | README 的端点清单曝了 9 个写端点（android/scan、android/pair、disconnect、forget、switch、atv/disconnect、atv/forget、volume、wol） | **已收敛** | 2026-10-14 补齐 README 并加 `tests/test_icons.py::ReadmeEndpointTest` 对账（`server.ROUTES` 必须全部出现）；README 改指向 spec.md |

## 7. 下一步

1. spec.md 第 6 节三条缺口：第一条（pyatv 错误文案）已收敛为 I15；
   第二条（harness 共享基建）取证后判定收益/风险不成比例，暂不动；
   第三条（真机验收）受设备环境约束无法进 CI。后续轮次先复核这三条是否仍成立。
2. **GitHub 推送待恢复**：本地 `a4b3ece` 之后又多了布局优化这一轮，网络恢复后 `git push origin main`。
3. 代码或边界发生实质变更时重跑 ./check.sh，并把结论写进第 4 节（新开 4.x 小节，按日期升序）。
4. 布局若要再收紧，候选是「npCard 与手机安装卡的宽屏归属」——这两张目前走 auto-flow，
   因为它们内容轻（一行状态条 / 折叠 details），但一旦某天内容变重就得显式归列。
