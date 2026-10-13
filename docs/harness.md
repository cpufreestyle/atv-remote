# ATV Remote — 验证面与评审台账（harness.md）

> 配套 docs/spec.md：spec.md 定义「应当具备的契约」，本文件记录「如何验证、覆盖到哪、
> 最近一次核验结论」。
> harness 产物根目录 .qoder/better-harness/ 与 .mimosa/ 已 gitignore，不在本台账内。
> 最近核验：2026-10-13（HEAD 2dc3ca3；最新一轮 I15 已收敛，见第 4.3 节）

## 1. 一键验证命令

```bash
./check.sh                          # 全量基线（下面几条依序执行）
python3 -c 'import server, atv_backend'   # 后端导入自检
node --check static/app.js          # 前端语法
node --check static/sw.js           # Service Worker 语法
python3 -m unittest discover -s tests    # 行为回归（629 项，skipped 11）
./sync-native.sh --check            # 内嵌副本逐字节比对
./sync-native.sh                    # 漂移时同步，并顺带跑一遍 check.sh
.venv/bin/python -m unittest discover -s tests   # pyatv 分支复跑
```

起真服务做人工冒烟（可选，需要真实设备）：python3 server.py --host 0.0.0.0 --port 8300

## 2. 测试资产盘点

| 类别 | 数量 | 说明 |
|------|------|------|
| Python 单测文件 | 40 | tests/test_*.py |
| Python 用例总数 | 629 | 其中 skipped 11（依赖 pyatv 或 Pillow）；.venv 下 7 |
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

### 4.1 2026-10-13（媒体解析外提 + 交付清单跟着 import 走）

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

### 4.2 2026-10-07

| 项 | 结论 |
|----|------|
| HEAD | 69dca04 桌面图标修复 |
| 全量单测 | python3 -m unittest discover -s tests 得 603 passed、skipped 11 |
| 内嵌副本 | ./sync-native.sh --check 得「内嵌副本与根目录一致」 |
| 桌面图标 | NSWorkspace 实渲染修前为 AppleScript 羊皮纸、修后为遥控器图标；codesign 验证通过 |
| 清理 | 删除 android-native 构建缓存、.gradle、__pycache__ 与 .DS_Store，释放 330 MB，未触碰任何入库文件 |
| 事实核对 | 端点表、不变式、令牌策略逐条对照源码，与 spec.md 一致 |

## 5. Qoder better-harness 台账

来源：.qoder/better-harness/2026-08-26/220155-atv-remote/findings.json
（session-limited 只读评审，3 个 finding，5 个维度）

| id | 严重度 | 状态 | 证据或说明 |
|----|--------|------|-------------|
| no-agent-routing-instructions | High | 已解决 | AGENTS.md 覆盖启动命令、源码结构、构建路径、安全约束 |
| no-automated-validation | Medium | 已解决 | check.sh 与 629 项单测；本文件与 spec.md 固化为契约，并有 SpecContractTest 防漂移 |
| untracked-native-module | Low | 已解决 | android-native/ 已入库，README 有独立章节 |

## 6. Drift 台账（spec 与代码的偏差）

| 编号 | 偏差 | 状态 | 处理 |
|------|------|------|------|
| D1 | check.sh 全量跑通耗时约 30 秒，单次 exec 容易被超时截断 | 已知 | 分段复跑：先 python3 分支，再 .venv 分支 |
| D2 | README 的端点清单只列常用子集，不是完整表 | 已接受 | 完整契约以 spec.md 第 3 节为准 |

## 7. 下一步

1. 收敛 spec.md 第 6 节列的三条缺口，收敛后更新 harness.md 第 3、4 节。
2. 代码或边界发生实质变更时重跑 ./check.sh，并把结论写进第 4 节。
