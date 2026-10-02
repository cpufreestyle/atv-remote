# 开源项目参考台账（第三十一轮，2026-09-29）

> 本文回答一个问题：**这个项目该参考哪些开源项目、具体参考它们的什么、依据是什么。**
> 每条都写明「核实到了哪一步」——读过真实源码的口径照抄，只读过设计文档的标明是概念参照，
> 没核过的进「候选」并说明为什么现在不写进代码。

## 0. 本次怎么核实的（以及为什么这样核实）

本机网络到 api.github.com 的 TLS 握手直接超时，GitHub 页面与 raw 内容同样不可达
（ghproxy.com / hub.gitmirror.com 也都连不上）。所以走的是**包管理器镜像 + 本地读源码**：

- npm 用 registry.npmmirror.com，PyPI 用 mirrors.cloud.tencent.com/pypi/simple，两者均可达；
- 把源码包下载到 /tmp/osref 后直接 rg / sed 读实现，不依赖任何网页描述。

这样做的好处是结论可复现：本文引用的每个数字都能在对应版本里 grep 到，不存在「凭印象引用」。
下一轮换台机器，重跑同样的下载命令即可再核一遍。

## 1. Fuse.js 7.1.0 —— 模糊匹配的错误预算（已核实源码）

| 项 | 值 |
|---|---|
| 包名 / 版本 | npm fuse.js 7.1.0 |
| 许可 | Apache-2.0 |
| 自述 | "Lightweight fuzzy-search" |
| 核实方式 | 下载 tgz，读 package/dist/fuse.mjs |

**直接抄走的两个口径**（都在代码里，不是文档结论）：

1. 打分函数 computeScore()：accuracy = errors / pattern.length，
   proximity = |expectedLocation - currentLocation|，
   score = accuracy + proximity / distance（ignoreLocation 为真时只留 accuracy）。
2. 默认值 FuzzyOptions：location = 0、threshold = 0.6、distance = 100；
   BasicOptions：isCaseSensitive = false、shouldSort = true。

**它的查法也值得抄**：BitapSearch.searchIn() 先用 text.indexOf(pattern) 走精确快路径，
再按 MAX_BITS 分块，块内逐级放宽允许的错误数（for (let i = 0; i < patternLen; i += 1)），
配一次二分搜索把候选窗限制在 expectedLocation ± binMid。翻译成本项目的话就是
「先精确、再逐步放宽」，本项目 palScore 的五档结构与此同构。

另外它的字段长度归一 1 / Math.pow(numTokens, 0.5 * weight)（短字段权重高）解释了
为什么「标签越短分越高」——原来的 palScore 是凭直觉写成 - label.length 的，
现在知道这是在复刻一个已知结论。

**落在哪**：static/app.js 的 intent 段 —— intentEditErrors() 取错误数、
intentSubstrErrors() 做「近似子串匹配」近似位置项、intentMaxErrors() 定错误预算。
差异写清楚在段注释里：只做整串比较时 proximity 恒 0，故 score 退化为 errors/len；
阈值用 0.34 而不是 0.6，理由是两字符查询容忍一个错会产生大量噪音（中文两字词另给底线，见第 4 节）。

## 2. rapidfuzz 3.14.3 —— 「一条查询对一串候选」的打分组合（已核实源码）

| 项 | 值 |
|---|---|
| 包名 / 版本 | PyPI rapidfuzz 3.14.3（cp314 macOS arm64 wheel） |
| 许可 | MIT，# Copyright (C) 2021 Max Bachmann |
| 核实方式 | 下载 wheel 解包，读 fuzz.pyi / process.pyi / 目录结构 |

**可复用的是它把多个视角组合起来的思路**：fuzz 一侧提供
ratio / partial_ratio / partial_ratio_alignment / token_sort_ratio / token_set_ratio 等，
全部接受 processor 与 score_cutoff 两个参数；process 一侧提供 extract / extractOne，
由调用方选 scorer。翻译成本项目的语言就是：**同一条查询，同时用「整串相似」
「片段相似」「词序无关相似」三个视角各打一次分，取最高**——而不是只用一个。

token_sort_ratio / token_set_ratio 的存在本身是个提示：**词序不该影响命中**
（「开一下 Netflix」与「Netflix 打开」是同一件事）。本项目 intentSim() 照此组合。

**落在哪**：intentSim() / intentPartialRatio() / intentTokenRatio()，
以及 intentPick()（本项目版的 process.extract 最小形态，含 cutoff 过滤）。

## 3. Home Assistant conversation —— 意图/别名路由（概念参照）

Home Assistant 的对话组件是本项目前几轮已经引用过的对象（见 docs/ui-study.md
各轮的「学习源」），本次没有重新核实源码，因此只作**概念参照**：
同义词与别名优先于模糊匹配，未命中时不改写用户输入。这条原则直接决定了
intentParse() 的返回约定——**返回 null 表示「这不是命令」**，调用方必须原样
sendText 下发。这是本功能最重要的安全属性，测试里正反都钉住了。

## 4. 本轮落地：说话即遥控（intent 段）

### 修的两个真问题

1. **语音识别结果原来直接 sendText**：说「声音小一点」会把这五个字打进电视搜索框，
   用户的意图根本没被理解。这是功能缺陷，不是「不够智能」。
2. **命令面板 palScore 只有四档**：词首 / 标签子串 / 别名词首 / 散乱子序列，
   打错一个字母就 0 命中。对着 51 条命令找「yutube」什么都找不到。

### 实现要点（含被测试逼出来的修正）

按「同义词别名 → 容错别名 → 动词+对象 → 整句直中」四级递进，任一级不命中就返回 null。

四条规则是跑用例跑出来的，不是设计时想到的：

1. **别名只认「整句相等」或「后缀命中」，不认任意子串**。
   最初用任意子串，「打开电视机顶盒」里的「打开电视」会触发开机键——用户说的是装什么。
2. **容错只跟「句尾等长那一小段」整串比，且首字必须相同**。
   「看电视」与「关电视」只差一个字，「开机」与「关机」同理；放开首字就会把
   「看电视」判成关机。中文命令的区分信息主要在首字。
3. **歧义时不猜**：「音量城」距「音量减 / 音量低」与「音量加 / 音量增」都只差一个字，
   猜任何一边都有五成概率按错键——拒绝并原样下发。
4. **有动词就只走对象分支，不再整句直中**；对象是通用名词（电视 / 设备 / 应用…）时直接放弃。
   否则「看电视」会命中 conn 命令 terms 里的「连接电视」。

另外把错误预算改成**按中英分流**：纯中文查询给 1 的底线（中文两字词错一个字仍是同一个词，
且中文标签本身只有两三个字，不容错等于中文命令永远不能容错），拉丁查询不给
（两字母容一个错等于什么都匹配——yt 会命中 youtube）。

### 验证

- tests/intent_harness.js：15 组行为用例，抽 intent 段原样执行，ALL_INTENT_CASES_PASSED。
- tests/test_intent.py：13 项（段标记唯一 / 无 DOM 与持久化 / harness 绿且用例数不减少 /
  安全底线三条 / 别名 id 与 palCommands() 同源 / 偏好只进 localStorage / ARIA 契约）。
- 真实浏览器（Playwright + 线上 51 条命令）：「打开 Netflix」得到 app:com.netflix.ninja、
  「声音小一点」得到 key:25；「周杰伦」/「看电视」/「音量城」全部返回 null。
- ./check.sh：468 用例 OK（较上轮 +13），./sync-native.sh --check 一致。

## 5. 候选：下一轮值得参考什么（未核实源码，仅列理由）

下面几条都还没读源码，写在这里是为了下一轮直接接着做，不要从零想。

| 候选 | 参考它什么 | 为什么是它 |
|---|---|---|
| Genymobile/scrcpy | （本轮已核实：不提供可抄的客户端死区，负面结论） | 见第 10 节：它只把轴重标度成 HID 值，死区交给设备端 InputReader；纯 adb 场景用不上它的 UHID/AOA 模式 |
| rapidfuzz 的 distance 子模块 | ScoreAlignment 把「最佳对齐区间」一起返回 | 「命令面板高亮命中片段」需要对齐区间而不只是分数，现在是整行不高亮 |
| VS Code Settings Sync / chezmoi | 配置导出的白名单与坏档回滚 | 提案 E6（导入导出）的安全边界：只导非敏感键、坏 JSON 不得写坏现有配置 |
| kubectl cluster-info dump / Home Assistant /api/diagnostics | 「一键体检报告」的字段白名单思路 | 提案 E3：用户报「连不上」时要来回问版本/adb/IME 三态/token，取字段的做法可直接照搬 |
| Textualize textual | 键盘优先界面的焦点管理与部件状态 | 命令面板目前选中态靠行下标 + aria-activedescendant，textual 在「焦点环 / 部件禁用态」上有更完整的约定 |

## 6. 复现方式

    mkdir -p /tmp/osref && cd /tmp/osref
    # Fuse.js（npm 镜像）
    curl -sSL -o fuse.tgz https://registry.npmmirror.com/fuse.js/-/fuse.js-7.1.0.tgz && tar xzf fuse.tgz
    rg -n 'accuracy = errors|threshold: 0.6|distance = 100' package/dist/fuse.mjs
    # rapidfuzz（PyPI 镜像，先取 cp314 macOS arm64 的链接）
    curl -sS https://mirrors.cloud.tencent.com/pypi/simple/rapidfuzz/ | rg -o '/packages/[^"]*cp314-cp314-macosx[^"]*arm64[^"]*\.whl' | tail -1
    curl -sSL -o rf.whl https://mirrors.cloud.tencent.com/pypi/packages/<上面的路径> && unzip -oq rf.whl
    rg -n 'def ratio|def partial_ratio|def token_sort_ratio' rapidfuzz/fuzz.pyi


## 7. 第三十二轮补记：命中可见 + 常用可排（2026-10-01）

### rapidfuzz.distance —— ScoreAlignment（已核实源码并落地）

上一轮只读了 fuzz.pyi / process.pyi（打分类接口）。这一轮读到 rapidfuzz/distance/ 目录，
发现除了打分还有一组对齐接口。核实到的类型定义（rapidfuzz/distance/_initialize.pyi）：

    class ScoreAlignment:
        score: int | float
        src_start: int
        src_end: int
        dest_start: int
        dest_end: int

这条信息的价值：相似度接口只回答「有多像」，对齐接口还回答「像在哪一段」。
模糊匹配的界面缺的正是后者 —— 有 tier 5 容错之后，「打开 YouTube」能匹配查询「yutube」，
用户却看不出来这一行为什么冒出来。

落在 static/app.js 的 palmark 段：palMarkRanges(label, q) 返回区间数组，
palMarkWindow 做近似窗口搜索。取舍：区间对不上就返回空数组 —— 错的高亮比没有更糟，
用户会以为「高亮的地方就是我要找的」。

### SortableJS 1.15.6 —— oldIndex/newIndex 契约（已核实源码并落地）

| 项 | 值 |
|---|---|
| 包名 / 版本 | npm sortablejs 1.15.6 |
| 许可 | MIT |
| 自述 | JavaScript library for reorderable drag-and-drop lists on modern browsers and touch devices. No jQuery required. |
| 核实方式 | 下载 tgz，读 modular/sortable.core.esm.js |

核实到的事实：拖拽结束事件携带 oldIndex 与 newIndex（见 core.esm.js 中 evt.oldIndex /
evt.newIndex 的赋值，以及 _drop 里的 newIndex = index(dragEl)）。也就是说：
库负责重排 DOM，消费者的责任是把数据重排得和 DOM 一致。

本项目取其契约（移动 = 从 oldIndex 取出、插到 newIndex），不取它的纯拖拽交互：
SortableJS 只提供指针/触摸拖拽，没有键盘通道；而这个界面同时跑在手机触屏和
Mac App（键盘可达）上，纯拖拽会把键盘用户挡在外面。所以落地形态是一对显式的
上移/下移按钮，数组移动语义照抄，并接上项目既有的 undoable 撤销链路。

### 仍未核实的候选

第 5 节表格里 Textualize textual 等条目依旧只是候选。下一轮要接着做的话，
还是走 npm / PyPI 镜像下载源码那条路，别凭印象引用。

## 8. 第三十三轮补记：重连退避（2026-10-01）

### tenacity 9.1.4 —— wait_exponential，以及「什么时候不该加抖动」（已核实源码并落地）

| 项 | 值 |
|---|---|
| 包名 / 版本 | PyPI tenacity 9.1.4 |
| 许可 | Apache 2.0 |
| 作者 | Julien Danjou |
| 自述 | Retry code until it succeeds |
| 核实方式 | mirrors.cloud.tencent.com/pypi/simple 下 wheel，解包读 tenacity/wait.py |

核实到的事实，原文（tenacity/wait.py 第 183-187 行，wait_exponential 的 docstring）：

    The intervals are fixed (i.e. there is no jitter), so this strategy is
    suitable for balancing retries against latency when a required resource is
    unavailable for an unknown duration, but *not* suitable for resolving
    contention between multiple processes for a shared resource. Use
    wait_random_exponential for the latter case.

实现（同文件 202-208 行）与这个口径一致：

    exp = self.exp_base ** (retry_state.attempt_number - 1)
    result = self.multiplier * exp
    return max(max(0, self.min), min(result, self.max))

这条信息的价值不只是「指数退避怎么写」，而是它明确说了什么时候不该加抖动。
网上讲指数退避几乎都会接着讲 AWS 那篇的 Full Jitter，很容易一路推到「退避就该加抖动」。
但 tenacity 的 docstring 把两种场景分开了：资源不可用、时长未知 → 固定指数；
多个无协调进程争用同一资源 → wait_random_exponential（即 Full Jitter）。
抖动的存在理由是打散争用，不是为了「看起来更随机」。

落到 server.py：reconnect_delay(fails) 是单进程后台循环在等一台正在重启的电视，
属于前者，所以刻意不加抖动。加了抖动的后果在这个项目里是负的：页面上要显示
「还要等几秒」，抖动会让这个数字每次轮询都跳，而这里根本没有第二个进程需要打散。
RECONNECT_BASE = 6.0 / RECONNECT_MAX_DELAY = 60.0，序列 6→12→24。

顺带对齐了一个边界：wait_exponential 用 except OverflowError: return self.max
兜住 attempt_number 很大时的 multiplier * exp。本项目 reconnect_delay 改成先判再乘
（if exp >= cap / base: return cap），结果与 tenacity 一致，但不需要抛异常再捕获 ——
后台线程里捕 OverflowError 会把真正该看的堆叠埋掉。

### 为什么值得改

老行为是固定 30 秒冷却。电视重启通常几十秒，于是前两次重试的间隔完全一样：
既可能在电视还没起来时白白打一次 adb connect，也可能在电视早就能连时还在干等。
30 秒这个数字对两头都是错的，而它错的原因不是「选得太长」，是恒定。

### 可观测性

光改退避不够 —— 后台线程在等，用户看到的是「自动重连中」四个字，
不知道还要等 6 秒还是 24 秒。所以 /api/status 的 auto_reconnect 增加 next_in
（距下次重试的秒数），前端 static/app.js 的 renderAutoReconn / arPaint 把它当校准点、
中间按本地秒走（沿用睡眠定时那条「服务端给终点、本地走秒」的路数，不靠 8s 轮询刷新）。

### 踩到的坑

1. 先把 reconnect_delay 的函数体写到 /tmp 却忘了插进 server.py，1005 行还在引用已删掉的
   RECONNECT_COOLDOWN，import server 直接 NameError。教训：常量改名要连着函数一起落，
   改完立刻 python3 -c 'import server'，别等整轮结束。
2. arPaint 里写了 ("#info")。页面上的元素其实叫 #tvInfo，
   是 tests/test_command_palette.py 的 SelectorDriftTest 抓住的（它扫 app.js 里所有
   ("#xxx") 字面量，要求 id 必须存在于 index.html）。这类「渲染到不存在的元素上」
   的回归，import 检查和 node --check 都查不出来。修法是把元素当参数传进来，
   顺带省掉每秒一次 querySelector。
3. harness 一开始在 finally 里还原 globalThis.setInterval，被测函数是运行时按名字
   找全局的，还原之后拿回去的就是真定时器，于是「没挂上计时器」。假定时器必须挂到用例跑完。

### 验证

- tests/test_reconnect_backoff.py 7 项：指数序列、上限钳制、fails<1 返 0、
  参数可注入、单调不缩水、恒定冷却已消失。
- tests/test_android_discovery.py 新增 3 项：tick 真的按 6/12/24 排下次重试
  （用 next_retry - before 实测，不是读常量）、/api/status 暴露 next_in、
  空闲时 next_in 为 0。
- tests/reconnect_ui_harness.js 12 组用例（node，抽真函数 + 假 DOM + 假 setInterval）：
  排队显示秒数、本地走秒、归零清计时器、停机不挂计时器、空闲无后缀、
  切到 Apple TV 时 arBase 复位。tests/test_reconnect_ui.py 驱动它，并锁住
  「harness 靠函数名切片」这件事不被悄悄改坏。
- ./check.sh 499 用例 OK（较第三十二轮 486 +13，skipped 11）；
  ./sync-native.sh --check 一致；git diff --check 干净。

## 9. 第三十四轮补记：一键体检报告（2026-10-01）

### Home Assistant helpers/redact.py —— 把「哪些键不能外泄」写成集合（已核实源码并落地）

| 项 | 值 |
|---|---|
| 包名 / 版本 | PyPI homeassistant 2026.9.4 |
| 许可 | Apache-2.0 |
| 自述 | "Open source home automation that puts local control and privacy first" |
| 核实方式 | mirrors.cloud.tencent.com/pypi/simple 下 homeassistant-2026.9.4-py3-none-any.whl（61 MB），unzip -p 抽 homeassistant/helpers/redact.py 直接读 |

核实到的事实（redact.py 全文 68 行）：

    REDACTED = "**REDACTED**"

    def async_redact_data(data, to_redact):
        if not isinstance(data, (Mapping, list)):
            return data
        if isinstance(data, list):
            return [async_redact_data(val, to_redact) for val in data]
        redacted = {**data}
        for key, value in redacted.items():
            if value is None:
                continue
            if isinstance(value, str) and not value:
                continue
            if key in to_redact:
                if isinstance(to_redact, Mapping):
                    redacted[key] = to_redact[key](value)
                else:
                    redacted[key] = REDACTED
            elif isinstance(value, Mapping):
                redacted[key] = async_redact_data(value, to_redact)
            elif isinstance(value, list):
                redacted[key] = [async_redact_data(item, to_redact) for item in value]
        return redacted

四条可以直接搬的口径：

1. 敏感键名是**显式集合**，不是「记得别加这个字段」的自觉。写进代码才拦得住手滑。
2. 判定顺序：先放行 None / 空串（它们不是值，替换了反而破坏形状），再判键名。
3. dict / list 都递归；叶子（字符串 / 数字 / 布尔 / None）原样返回，不包一层。
4. 常量就叫 REDACTED，值 "**REDACTED**" —— 原样沿用，见者即懂。

### 分歧（有意）：不做留头去尾

to_redact 除了键名集合，还接受「键名 → callable」的映射，用来做部分掩码。
这不是没人用的空架子，HA 自己在用（均已从 wheel 核实）：

    # homeassistant/components/google_assistant/data_redaction.py:11-15
    GOOGLE_MSG_TO_REDACT: dict[str, Callable[[str], str]] = {
        "agentUserId": partial_redact,
        "uuid": partial_redact,
        "webhookId": partial_redact,
    }

    # homeassistant/components/airvisual/__init__.py:67
    partial_redact(api_key)

partial_redact(x) 默认留前 4 后 4（x 长度不足 unmasked_prefix + unmasked_suffix 的两倍
则整值替换）。

我们只整值替换，理由是**报告的用途不同**：HA 的脱敏结果写进自家日志，a1b2***c3d4
对排障有用（能看出是不是同一个 key 变了、什么时候轮换的）。而这份报告是用户手动贴到
微信群 / GitHub issue 的，「前 4 后 4 位」在明文渠道里同样是明文，掩了等于没掩，
还多给人一层「已经安全了」的错觉。所以 DIAG_REDACTED 只整值替换，将来也不引部分掩码。

### 再退一步：字段白名单，而不是「先整体序列化再脱敏」

HA 的做法是先有一份完整 dict（它本来就要脱敏整个配置），再异步脱敏。我们侧过来了：

make_diagnostics() 不去碰 state.json 整体，九个顶层键全部现场拼装。已配对 Apple TV
只报 len(appletvs)，不报条目 —— 条目里有 MRP 配对凭据。局域网令牌只报「开没开 + 模式」，
值永远不进报告。

递归脱敏保留为纵深防御：redact_diagnostics() 套在返回值外面，将来谁手滑加了个名叫
token / credentials 的键也漏不出去。tests/test_diagnostics.py 有一条用例专门往
state["appletvs"] 塞带 credentials 的假条目、把 AUTH_TOKEN 设成哨兵值，断言两者都不
出现在 json.dumps 的输出里，并额外反证哨兵确实在输入里（防 vacuously passed）。

### 为什么值得改

用户报「连不上」时的现状是来回问：版本多少？adb 在哪？输入法切了没？设备显示什么状态？
四个问题四轮对话，而答案全在服务端手里。提案 E3 的做法是设置里放一个「复制诊断信息」按钮，
一份纯文本报告把版本 / 平台与 Python / adb 在不在（含路径与版本）/ 常驻 shell 存活 /
设备列表与在线状态 / 中文输入法三态 / Apple TV 可用性与已配对连接 / 令牌开关 /
mDNS 可用性一次讲完。关键取舍是**报告默认会被贴出去**，所以凭据一个都不进。

### 踩到的坑

1. redact_diagnostics 第一版只在 value 是字符串时才替换，于是 {"token": 12345} 会漏。
   HA 的原文是先判 None / 空串、再判键名，命中就换，与值类型无关 —— 照抄顺序就顺带修掉了。
2. harness 有一条用例把 d.adb.shell_alive 留着 true 却断言「常驻 shell 无」。这两个事实
   正交：找不到 adb 二进制与常驻 shell 是否活着没有因果关系，而「adb 在、shell 活着、
   但一台设备都没扫到」（未授权 / 授权弹窗 / 网段不同）恰恰是最需要报告说清楚的场景。
   改成两条用例分别覆盖，而不是把实现改成迁就断言。
3. 前端契约测试一开始把 "/api/" 和 "_check_auth" 也列进禁用词，结果纯函数段的**注释**
   （点名接口名）与路由段的**注释**（说明鉴权在上游统一处理）双双误报。注释里提一句上游
   约定是有价值的文档，不该为了过检把它删掉。收敛成精确匹配：禁 api( 而不是 /api/，
   禁 self._check_auth( 而不是 _check_auth。

### 验证

- tests/diagnostics_harness.js 10 组用例（从 static/app.js 抽纯函数段原样执行）：
  十行拼齐与顺序稳定、adb 未找到 / 零设备两态、Apple TV 前缀按 type 渲染、
  IME 三态不全、令牌与 mDNS 负状态、空 payload 不炸且不露字面量、
  敏感键混进 payload 也漏不出去、diagYes 真值表、diagImeLine 缺失即「未查询」。
- tests/test_diagnostics.py 30 项：脱敏语义 7 项（含「不 mutate 输入」与哨兵反证）、
  make_diagnostics 白名单形状 12 项（9 个顶层键精确集合、adb 子键精确集合、
  devices 只暴露 serial+state、current 对 android 取 target 对 appletv 取 id、
  缺 adb 报未找到、Apple TV 时不查 IME、凭据与令牌不外泄、开关与模式分离）、
  路由契约 3 项、前端契约 7 项、harness 驱动 1 项。
- ./check.sh 529 用例 OK（较第三十三轮 499 +30，skipped 11）；
  ./sync-native.sh --check 一致；git diff --check 干净。


## 10. 第三十五轮补记：手柄当 D-pad——死区、边沿触发与跳变补键（2026-10-01）

### 先记一条负面结论：scrcpy 不提供可抄的客户端死区

第 5 节候选清单里原来写着「提案 E4（手柄当 D-pad）缺的正是轴死区 + 边沿触发的成熟口径，
scrcpy 是这件事上被广泛审计过的实现」。本轮把 scrcpy 3.3.4 源码包下载读完，
**这个假设不成立**。记在本第 10 节，免得下一轮再挖一遍：

| 项 | 值 |
|---|---|
| 包名 / 版本 | Ubuntu universe scrcpy 3.3.4 orig 源码包 |
| 许可 | Apache-2.0 |
| 核实方式 | aliyun 镜像下载 orig.tar.gz，读 `app/src/hid/hid_gamepad.c` 与 `doc/gamepad.md` |

- `app/src/hid/hid_gamepad.c:197` 的 `AXIS_RESCALE(V) = (uint16_t)(((int32_t)V) + 0x8000)`
  只是把 SDL 的轴值重标度成 uint16 塞进 HID descriptor，**死区它根本不管**——交给设备端
  InputReader。
- `doc/gamepad.md:3-8` 列出全部模式：`disabled` / `uhid` / `aoa`。
  后两者是「模拟物理 HID 手柄」（UHID 内核模块 / AOAv2 协议），走设备输入子系统，不是 adb。

结论：**死区与边沿触发必须本项目自己做**。通道不同是根因：`adb shell input` 只支持
`keyevent / tap / swipe / text`，没有轴注入能力；scrcpy 的 UHID/AOA 路径在纯 adb
场景里用不上。另外 Browser Gamepad API 给的是 `[-1, 1]`，而两个参照驱动全都活在
`±32767` 的原始轴上，第一件事是先换基。

### qjoypad 4.3.1 ——「没变就不发键」的边沿状态机（已核实源码）

| 项 | 值 |
|---|---|
| 包名 / 版本 | Debian main qjoypad 4.3.1 orig 源码包 |
| 许可 | GPL-2.0 |
| 核实方式 | aliyun 镜像下载 orig.tar.gz，读 `src/axis.h` / `src/axis.cpp` / `src/constant.h` |

- `src/axis.h:16` `#define DZONE 3000`；`src/constant.h:16-17` `JOYMAX 32767` /
  `JOYMIN -32767`。默认死区 3000/32767 = 0.0916（本项目默认取 0.15，依据见下）。
- `src/axis.cpp:205-233`（`Axis::jsevent`）就是本轮立论要的**纯边沿状态机**：

      if (isOn && abs(state) <= dZone) { isOn = false; ... }      /* 回中：抬键 */
      else if (!isOn && abs(state) >= dZone) { isOn = true; ... } /* 顶出：按键 */
      else return;   /* 原注释：otherwise, state doesn't change! Don't touch it. */

  第三分支是重点：**方向没变就一个键都不发**。前端 60fps 轮询读手柄，没有这个 return
  就会把同一个方向键反复下发。
- 死区判据用「小于」：`src/axis.cpp:281` `inDeadZone()` 是 `abs(value) < dZone`
  ——**等于阈值算已经顶出**。本项目照抄了这个比较符（顶出即发，不含糊）。
- **qjoypad 的坑**（本项目特意避开）：轴从 +0.5 直接跳到 -0.5、不经过死区时，上面两个
  分支都不满足，代码直接 `return`，正向键就一直按着不松。xorg 驱动在这件事上补了一刀，

  见下。

### xf86-input-joystick 1.6.4 —— 死区数值、重标度、跳变补键与占空比截止（已核实源码）

| 项 | 值 |
|---|---|
| 包名 / 版本 | Debian main xserver-xorg-input-joystick 1.6.4 orig 源码包 |
| 许可 | MIT |
| 核实方式 | aliyun 镜像下载 orig.tar.gz，读 `src/jstk.c` / `src/jstk_axis.c` / `src/backend_joystick.c` / `src/jstk_options.c` / `man/joystick.man` |

逐条对上本轮的常量：

1. **默认死区 5000**：`src/jstk.c:524` `priv->axis[i].deadzone = 5000;`
   → 5000/32768 = 0.1526。本项目 `GP_DEADZONE_DEFAULT = 0.15` 的来源。
2. **重标度**：`src/jstk_axis.c:83` `scale = 32768.0f / (float)(32768 - axis->deadzone)`。
   顶出死区后的残差要放大回满量程，否则轻推没有满速。
3. **残差**：`src/jstk_axis.c:411-416`，绝对轴取 `value ± deadzone` 再除以
   `2*(32768-deadzone)`，原注释 `rel contains numbers between -0.5 and +0.5 now`。
   本项目 `gpResidual()` 同一口径（除以 `1-dz`，落在 [-1, 1]）。
4. **死区内只发一次事件**：`src/backend_joystick.c:172` 起，`abs(js.value) < deadzone`
   时先判 `value != 0` 才发，原注释 `We only want one event when in deadzone`。
   防的正是「按住不动还反复发」。
5. **跳变补键**：`src/jstk_axis.c:494-505`，原注释
   `PWM Axis %d jumped over. Forcing keys_low up.` —— 轴从负顶到正、不经过死区时，
   强制把旧方向的键抬起来。这正是 qjoypad 缺的那一刀；本项目 `gpAxisEvents()`
   的「先抬旧键再按新键」照此实现。
6. **占空比与两个截止**：`src/jstk_axis.c:518-551`。
   `time_on = (|v|-dz)/32768 * 32768/(32768-dz)`，两相各 `+0.01` 防除零；
   `scale = 50.0f * amplify` 且只除较小相（`src/jstk_axis.c:527-531`），
   所以**较小的一相永远被缩放成 50ms**；一相超过 600ms 就直接判「一直按着 / 一直松着」
   （`src/jstk_axis.c:537` 与 `:547`）。本项目 `GP_PWM_MIN_PHASE_MS = 50` /
   `GP_PWM_HOLD_MS = 600` 直接取这两个数。
7. **死区可配但有上限**：`src/jstk_options.c:285-295` 解析 `deadzone=`，
   超过 30000 只 warning、不改值。本项目 `GP_DEADZONE_MIN/MAX = 0.02/0.5` 同思路
   （钳制而不是报错）；下限不为 0：关成 0 会让摇杆的静止噪声一直触发方向键。
8. **man 页把本轮立论说全了**：`man/joystick.man:271-276` ——
   `Accelerated mode should be selected, if the axis is a directional pad, which
   reports only three states: negative, center, positive`（三态轴就是 D-pad）；
   `:194-196` 「轴顶出死区发一串 keydown，回中发匹配的 keyup」；
   `:218` 「偏转 100% 只有一个 keydown，等于一直按着」；
   `:349` 「第三四轴只在进出死区时发一次，autorepeat 交给 X.Org」。

### 分歧（有意）：不照抄占空比的 U 形速度律

驱动的 PWM 是给指针移动设计的：偏转越大，两相越趋向「一直按」，表现为先慢后快的
非线性节奏。本项目 `gpRepeatMs()` 刻意做成**单调**：残差越大重复间隔越短
（60ms..600ms），因为安卓 D-pad 的场景是**在列表里移动光标**，用户预期「推到底就最快」，
U 形节奏在那里只显得飘。`gpPwmCycle()` 与驱动口径的手算对账表保留在
`tests/gamepad_harness.js` 里，是为了让 50/600 两个常量有据可查，不是要复用它的
速度曲线。

### 验证

- `tests/gamepad_harness.js` 17 组用例（从 static/app.js 抽 gamepad 纯函数段原样执行）：
  ALL_GAMEPAD_CASES_PASSED。含死区边界（等于阈值算顶出）、跳变补键顺序、仲裁迟滞、
  重复间隔单调，以及驱动占空比口径的逐项手算对账（残差 0 / 0.1 / 0.3 / 0.5 / 0.7 /
  0.9 / 0.95 / 1.0 对应的两相毫秒数）。
- `tests/test_gamepad.py` 13 项：段标记唯一、纯函数段不碰 DOM 与网络、九个函数齐、
  常量按参照钉住、死区比较符、跳变先抬后按、未接线（html 无 gamepad 字样）、
  出处六条全可在本第 10 节 grep 到、scrcpy 负面结论在册、段注释指向本文。
- `./check.sh` 542 用例 OK（较第三十四轮 529 +13，skipped 11）；
  `./sync-native.sh --check` 一致；`git diff --check` 干净。

### 复现方式

    mkdir -p /tmp/osref/dead && cd /tmp/osref
    # qjoypad 4.3.1（Debian 源）
    curl -sSL -o dead/qjoypad_4.3.1.orig.tar.gz \
      https://mirrors.aliyun.com/debian/pool/main/q/qjoypad/qjoypad_4.3.1.orig.tar.gz
    # xf86-input-joystick 1.6.4（Debian 源；这个包在 Ubuntu pool 里没有，别去那儿找）
    curl -sSL -o dead/xserver-xorg-input-joystick_1.6.4.orig.tar.gz \
      https://mirrors.aliyun.com/debian/pool/main/x/xserver-xorg-input-joystick/xserver-xorg-input-joystick_1.6.4.orig.tar.gz
    # scrcpy 3.3.4（Ubuntu pool）
    curl -sSL -o scrcpy_3.3.4.orig.tar.gz \
      https://mirrors.aliyun.com/ubuntu/pool/universe/s/scrcpy/scrcpy_3.3.4.orig.tar.gz
    cd dead && tar xzf qjoypad_4.3.1.orig.tar.gz && tar xzf xserver-xorg-input-joystick_1.6.4.orig.tar.gz
    rg -n 'DZONE' qjoypad-4.3.1/src/axis.h
    rg -n 'else return;' qjoypad-4.3.1/src/axis.cpp
    rg -n 'deadzone     = 5000' xf86-input-joystick-1.6.4/src/jstk.c
    rg -n 'jumped over' xf86-input-joystick-1.6.4/src/jstk_axis.c
    rg -n 'AXIS_RESCALE' ../scrcpy/Genymobile-scrcpy-b64fca9/app/src/hid/hid_gamepad.c

三个包的 sha256（本次核对用）：

    qjoypad_4.3.1.orig.tar.gz                     0081974dc75b4a9804499f9340564a1aab7c5630c81503c0e624d5b3b6172b4f
    xserver-xorg-input-joystick_1.6.4.orig.tar.gz 5582db8abbd38273463b53c49cd67554ee35abbd9f4c57d30672f0bf01b69d14
    scrcpy_3.3.4.orig.tar.gz                      d6141c37e8b10e81068540ea4754861c7d090516c9d263f2f3d57968ac327768

## 11. 第三十六轮补记：一帧一个出口、消斜与连发节拍（2026-10-01）

第三十五轮只回答了「一根模拟轴怎么翻成一个方向键」。第三十六轮要回答的是三个多输入源
问题：物理十字键与左摇杆同时偏转时谁说话、斜按怎么裁、按住不放时节拍怎么算。三件事
xboxdrv 0.8.8 都有现成实现，逐行读完直接抄口径。

| 项 | 值 |
|---|---|
| 包名 / 版本 | Ubuntu universe xboxdrv 0.8.8 orig 源码包（.tar.bz2，268021 字节） |
| 许可 | GPL-3.0+（源文件头：either version 3 of the License, or (at your option) any later version） |
| Copyright | (C) 2008 / 2010 Ingo Ruhnke <grumbel@gmail.com> |
| 核实方式 | aliyun 镜像下载 orig.tar.bz2，读 src/uinput_options.cpp、src/modifier/、src/buttonfilter/、src/controller_slot_config.cpp |

### 一份输入只留一个出口

`src/uinput_options.cpp:189-198` 的 `UInputOptions::dpad_as_button()`：

    189: void
    191:   get_btn_map().bind(XBOX_DPAD_UP,    ButtonEvent::create_key(BTN_BASE));
    194:   get_btn_map().bind(XBOX_DPAD_RIGHT, ButtonEvent::create_key(BTN_BASE4));
    196:   get_axis_map().bind(XBOX_AXIS_DPAD_X, AxisEvent::invalid());
    197:   get_axis_map().bind(XBOX_AXIS_DPAD_Y, AxisEvent::invalid());
    198: }

四向 bind 成按键的**同时**，把承载这四向的两根轴 bind 成 `AxisEvent::invalid()`。
同一份物理偏转若从轴和按键两条路各出一份，下游看到的就是双倍速度——所以绑一边就必须
把另一边作废，不是「谁先到算谁」。本项目 `gpFrame()` 就是这个口径：物理十字键
按着时摇杆整帧让位（`if (btn !== null) return { dir: btn, axis: null, val: 0 };`），
而不是让两条路各发一份、靠设备端自己收敛。

### 斜按裁掉一根轴：只清零，不重算；平手判 Y 胜

`src/modifier/four_way_restrictor_modifier.cpp:44-59` 的
`FourWayRestrictorModifier::update()`：

    47:  if (abs(get_axis(msg, m_xaxis)) > abs(get_axis(msg, m_yaxis)))
    49:    set_axis(msg, m_yaxis, 0);      /* X 胜：清零 Y */
    51:  else if (abs(get_axis(msg, m_yaxis)) > abs(get_axis(msg, m_xaxis)))
    53:    set_axis(msg, m_xaxis, 0);      /* Y 胜：清零 X */
    55:  else
    57:    set_axis(msg, m_xaxis, 0);      /* 平手：也清零 X —— Y 胜 */
    59: }

三条分支共同点是**只清零、不重算**：赢的那根轴原样透传，输的那根归零，没有归一化也没有
按比例缩放。搬到布尔输入上就是 `gpBtnDir()`：按着任一竖直方向就判竖直胜，斜按
不会同时出两个方向（`if (u || d) return u ? "up" : "down";`）。

### 分歧（有意）：模拟轴那侧保留迟滞

FourWayRestrictor 完全没有迟滞：|x| 与 |y| 只差一个计数单位就能抢走方向，推着摇杆画圆
会一路来回翻。本项目只在**十字键**这一路照抄（布尔输入不存在临界抖动），模拟轴那一路
仍走第三十五轮的 `gpArbitrate` 迟滞——新轴残差要超过当前轴
`GP_ARBIT_MARGIN = 1.25` 倍才抢得走。理由：摇杆是连续量，拇指停在两轴临界附近是
常态；十字键是离散的，不存在「停在一个计数单位差」的状态。这是本台账少数几处
**有意不复刻**，记下来免得下轮当漏抄补上。

### 连发：先发一拍，再按速率重发

两个默认值在 `src/buttonfilter/autofire_button_filter.cpp:27-28`（`from_string` 开头）：

    int rate  = 50;   /* 毫秒：连发间隔 */
    int delay = 0;    /* 毫秒：按下后多久才进连发 */

节拍在 `src/buttonfilter/autofire_button_filter.cpp:82-97` 的 `filter()` 里：

    82:  if (m_autofire) {
    84:    if (m_counter > m_rate) { m_counter = 0; return true; }  /* 到点才发 */
    89:    else                     { return false; }              /* 未到点，吞掉 */
    93:  }
    94:  else {
    96:    return true;                                            /* 首拍直接放行 */
    97:  }

其中 `m_autofire` 由 `update()` 置位：按住期间 `m_counter += msec_delta`，
`m_counter > m_delay` 才置真。合起来两条结论：

1. **首拍永远不被速率门控**——刚按下时 `m_autofire` 还是 false，`filter(true)`
   直接 `return true`。若把首拍也并入延时，快速点按会感觉「第一下没反应」。
2. **松键立即复位**：`if (!value) { m_counter = 0; m_autofire = false; return false; }`。

本项目 `gpNextFireMs()` 同一形状：`elapsed < GP_REPEAT_FIRST_MS` 时返回剩余
延时，之后每 `gpRepeatMs()`（残差越大越密，60ms..600ms）一拍；首发那一拍在按下时
就发出、不计入延时。一处不照抄写明白：xboxdrv 判延时用严格大于（`m_counter > m_delay`），
差不到一个轮询周期，不值得为它歪一份实现。

### 反面结论：xboxdrv 默认什么都不挂

`src/controller_slot_config.cpp:207-212`：

    207: if (!opts.autofire_map.empty())
    208: {
    211:   for(std::map<XboxButton, ButtonFilterPtr>::const_iterator i = opts.autofire_map.begin();
    212:       i != opts.autofire_map.end(); ++i)
             buttonmap->add_filter(i->first, i->second);
    217:   modifier->push_back(buttonmap);
    218: }

`autofire_map` 非空才会把这条滤波链 push 进 modifier 链；**默认是空的，连发链路一个
都不挂**（手册里也当可选项列）。这条对我们是反向约束：本项目默认就开连发（在列表里移动
光标离不开它），所以第三十五轮的死区纪律必须更硬——死区内一个键都不发，
`gpResidual()` 归零时节拍器也必须闭嘴，否则摇杆的静止噪声会被放大成自动翻页。
参照实现「默认关」的经验，落到「默认开」的项目上就是一条安全带。

### 验证

- `tests/gamepad_harness.js` 25 组用例（第三十五轮 17 组 + 本轮 8 组：
  gpNormPad ×2、gpBtnDir、gpDirKey、gpFrame ×2、gpNextFireMs ×2）ALL_GAMEPAD_CASES_PASSED。
- `tests/test_gamepad.py` 17 项（第三十五轮 13 项 + 本轮 4 项）：新增接线已落地、
  第三十六轮常量钉住、DOM 胶水在段外、一拍 80ms 单列、本第 11 节在册。
- 接线落在 `static/index.html` 的 `#gpCard`（插在按键卡之前，默认关）与
  `static/app.js` 段外的胶水段：`GP_TICK_MS = 80` 单独一拍（连发节奏经不起 8s
  的状态轮询），手柄 id 一律 `textContent`（局域网广播可伪造，沿用全项目纪律）。
- `./check.sh` 全量通过；`./sync-native.sh --check` 一致；`git diff --check` 干净。

### 复现方式

    mkdir -p /tmp/osref/xb && cd /tmp/osref
    curl -sSL -o xb/xboxdrv_0.8.8.orig.tar.bz2 https://mirrors.aliyun.com/ubuntu/pool/universe/x/xboxdrv/xboxdrv_0.8.8.orig.tar.bz2
    cd xb && tar xjf xboxdrv_0.8.8.orig.tar.bz2
    rg -n 'dpad_as_button' xboxdrv-0.8.8/src/uinput_options.cpp
    rg -n 'set_axis' xboxdrv-0.8.8/src/modifier/four_way_restrictor_modifier.cpp
    rg -n 'int rate  = 50' xboxdrv-0.8.8/src/buttonfilter/autofire_button_filter.cpp
    rg -n 'autofire_map.empty' xboxdrv-0.8.8/src/controller_slot_config.cpp
    head -3 xboxdrv-0.8.8/COPYING

sha256（本次核对用）：

    xboxdrv_0.8.8.orig.tar.bz2  f307ba95442cfedd06a934ac572fe5b1d66ef3b73caa458c10c5a82d7b4819b9

## 12. 第三十七轮补记：坏档先校验、写入原子替换（chezmoi v2.73.0，2026-10-01）

前十一节回答的都是「一个输入怎么变成一个键位」。本节（第 12 节）要回答的是另一个问题：
**用户换机 / 换浏览器时，本机攒的设置怎么一次带走，且不许搬坏。** 手工重设十几项偏好
没人受得了；但「导出一份 JSON 让对方导入」这条路上，真正的风险不是搬不完，而是搬坏——
导入到一半失败，主题换了、手柄开关没换，这种半份配置比导入失败更难查。chezmoi 的
配置读取链与落盘路径正好把这三件事都做对了，逐行读完照抄口径。

| 项 | 值 |
|---|---|
| 包名 / 版本 | chezmoi v2.73.0（github.com/twpayne/chezmoi/v2，proxy.golang.org 源码 zip） |
| 许可 | MIT（LICENSE 首两行：The MIT License (MIT) / Copyright (c) 2018 Tom Payne） |
| Copyright | Copyright (c) 2018 Tom Payne |
| 核实方式 | curl proxy.golang.org 拉源码 zip 到 /tmp/osref，解压后 sed 逐行读，命令见文末 |

### 口径 1：坏档在写任何东西之前就失败

`internal/cmd/config.go`（2074 行）的配置读取链是 `config.go:998-1035`（decodeConfigFile → decodeConfigContents → decodeConfigMap），任一步失败都直接 return，
`configFile` 一个字都不会被写：

    989: func (c *Config) decodeConfigContents(format chezmoi.Format, contents []byte, configFile *ConfigFile) error {
    991: 	if err := format.Unmarshal(contents, &configMap); err != nil {
    995: 	return c.decodeConfigMap(configMap, configFile)
    998: func (c *Config) decodeConfigFile(configFileAbsPath chezmoi.AbsPath, configFile *ConfigFile) error {

而**语义级**的互斥校验排在链尾（`config.go:1028-1033`，`git.commitMessageTemplate` 与
`git.commitMessageTemplateFile` 不能同时给）：

    1028: 	if configFile.Git.CommitMessageTemplate != "" && configFile.Git.CommitMessageTemplateFile != "" {
    1029: 		return fmt.Errorf(
    1033: 	}

即「格式对」不等于「能写」：JSON 解析成功只过第一关，键值语义还要单独一关，且这一关
仍在写任何东西之前。落到本项目：`cfgParseBackup()` 就是这一关——坏 JSON / magic 不符 /
版本不符 / `keys` 缺失 / 顶层不是对象 / 白名单内值不是字符串，一律 `ok:false` 带人话
error；胶水 `cfgImportText()` 看到 `!parsed.ok` 只 toast 报错，现有 localStorage 一个
字节都不动（harness 里有「坏档导入后 B 机原配置原样」的钉子用例）。

### 口径 2：写入只认「完整的一份」（原子替换）

`internal/chezmoi/realsystem_unix.go:68-104` 的 `WriteFile` 在 safe 模式下不直接写目标
文件，而是经 `github.com/google/renameio` 写同目录临时文件再原子替换：

    89: 			tempDir = renameio.TempDir(dir.String())
    96: 			defer chezmoierrors.CombineFunc(&err, t.Cleanup)
    103: 			return t.CloseAtomicallyReplace()

三处行号各就各位：realsystem_unix.go:89 定临时目录、realsystem_unix.go:96 兜失败清理、realsystem_unix.go:103 才真正落盘。三个细节每个都有理由：临时文件必须在**同目录**（同一文件系统，`os.Rename` 跨盘会失败）；
失败路径由 `defer ... t.Cleanup()` 统一清理（不留半截临时文件）；`CloseAtomicallyReplace()`
让并发读者只见到旧或新的完整文件。落到本项目：导出是**一次性生成的完整 JSON**
（`cfgBuildBackup()` 里任何一个值不是字符串就整体失败——半份备份比没有备份更坏，
空的也直接失败）；导入是**全部校验通过后才批量落盘**，且「写成功数必须等于 apply 数」，
否则明确报「导入未完整」，绝不「能写几个写几个」。

### 口径 3：账目化——排除了什么、跳过了什么，都要能原样打印

`internal/chezmoi/sourcestate.go`（3080 行）在 ignore 判定处把每个命中的路径记下来
（`sourcestate.go:917-926`，Ignore 记、Ignored 取）：

    917: func (s *SourceState) Ignore(targetRelPath RelPath) bool {
    921: 		s.ignoredRelPaths.Add(targetRelPath)
    924: 	return ignore
    928: func (s *SourceState) Ignored() []RelPath {

`internal/cmd/ignoredcmd.go`（仅 41 行）把这个账目原样暴露给用户——`ignoredcmd.go:18` 的
`Short: "Print ignored targets"`，`ignoredcmd.go:37` 的 `sourceState.Ignored()`：

    18: 		Short:             "Print ignored targets",
    37: 	return c.writePaths(stringersToStrings(sourceState.Ignored()), writePathsOptions{

不是黑箱：用户问「这个文件怎么没生效」，一条 `chezmoi ignored` 就能看到它被哪条规则
排掉。落到本项目：导出要说清「导了哪些键、因敏感排了哪些键」（`cfgPlanExport()` 的
include / excluded 两堆）；导入要说清「写了哪些、跳了哪些、坏在哪」
（`cfgApplyBackup()` 的 apply / skipped / invalid 三堆，胶水把三个数都报进状态行）。

### 敏感纪律：白名单是主闸，敏感词副闸是第二道

`CFG_EXPORTABLE` 13 键白名单只收非敏感配置；`CFG_SENSITIVE_RE`（token|cred|pairing|
secret|password|cookie）作为副闸再筛一遍键名。`state.json`（配对凭据 + 访问令牌）后端
从来不下发给前端，前端想导也导不出来——但白名单之外万一有别的工具往 localStorage 塞了
token 类键，副闸一律排掉（AGENTS.md：凭据禁出本机）。harness 里专门有一组用例把
`atv_token` / `pairing_cred` 塞进导出源，断言导出文本 `grep token|cred|pairing` 为 0。

### 三处有意修正 / 分歧（对着源码改掉的印象）

1. **`format.go:172` 不是校验点**：那只是 `func (formatTOML) Unmarshal` 里的
   `toml.Decode(string(data), value)`，TOML 分支没有任何严格性旋钮。真正的旋钮在
   `format.go:180`——`func (formatYAML) Unmarshal` 里的 `yaml.DisallowUnknownField()`
   （未知键直接报错，不是警告）。初稿凭印象写成「172 行是严格模式」，逐行核对后改正。
   本项目学的是后半句：**白名单就是我们的 DisallowUnknownField**，未知键不是警告、
   不是自动跳过，是明明白白进 skipped 并报数。
2. **`ignoredcmd.go:18` / `:37`，不整段引用**：41 行的小文件，`Short` 在 18 行、
   `sourceState.Ignored()` 在 37 行。写「ignoredcmd.go:1-41」等于没引，下次复核的人
   还是得从头读。
3. **chezmoi 是 MIT，不是 Apache-2.0**：初稿按「Go 大项目多为 Apache」想当然写了
   Apache-2.0，读 LICENSE 首两行改正（MIT，Copyright (c) 2018 Tom Payne）。

### 落到本项目

- `static/app.js` `cfgxfer` 纯函数段（段标记 `/* ===== cfgxfer:begin / end ===== */`，
  禁 DOM / localStorage，DOM 胶水全在段外）：`CFG_MAGIC` / `CFG_VER` / 13 键
  `CFG_EXPORTABLE` / `CFG_LABELS` / `CFG_SENSITIVE_RE`，以及 `cfgLabelOf` /
  `cfgIsExportable` / `cfgPlanExport` / `cfgBuildBackup` / `cfgParseBackup` /
  `cfgApplyBackup`。
- 胶水 `cfgExport()` / `cfgImportText()` / `cfgReloadUI()`：导出触发 `<a download>`;
  导入走 `FileReader`，`!parsed.ok` 只报错不动配置；导入成功后当场刷新主题 / 灵敏度 /
  折叠 / 手柄 / 短语 / 收藏 / 最近应用 / 键位图 / 宏——即时手感不许等重开。
- `static/index.html` 设置弹窗「配置搬家」卡片：`#cfgExportBtn` / `#cfgImportBtn` /
  `#cfgxferStatus`（`aria-live="polite"`）/ `#cfgFileInput`。
- 通知历史、键盘历史、引导状态等推导 / 一次性数据**不进**白名单：换机重放没有意义，
  还可能夹带隐私文本。

### 验证

- `tests/backup_harness.js` 21 组行为用例（`node tests/backup_harness.js`）：白名单 13 键
  过副闸、敏感键分堆、导出文本 `grep token|cred|pairing = 0`、坏 JSON / magic / 版本 /
  `keys` 缺失一律拒收、A 机导出 B 机导入逐键复现、导出-导入-再导出逐字节幂等、
  坏档导入不写一个字节。ALL_BACKUP_CASES_PASSED。
- `tests/test_export_import.py`：段边界唯一、剥注释后段内无全局态、胶水在段外、白名单
  13 键钉住（推导 / 敏感键不在内）、副闸正则钉住、代码里不出现 state.json、坏档分支
  先于写入、HTML 接线齐全、台账出处可 grep、harness 子进程全绿且改坏段标记必失败。
- `./check.sh`、`./sync-native.sh --check`、`git diff --check` 全过。

### 复现方式

    mkdir -p /tmp/osref/cz /tmp/osref/czv
    curl -sSL -o /tmp/osref/cz/cz73.zip https://proxy.golang.org/github.com/twpayne/chezmoi/v2/@v/v2.73.0.zip
    cd /tmp/osref/czv && unzip -qo ../cz/cz73.zip
    CZ=/tmp/osref/czv/github.com/twpayne/chezmoi/v2@v2.73.0
    sed -n '989,1000p;1026,1036p' $CZ/internal/cmd/config.go
    sed -n '68,70p;88,97p;102,104p' $CZ/internal/chezmoi/realsystem_unix.go
    sed -n '170,173p;178,182p' $CZ/internal/chezmoi/format.go
    sed -n '915,932p' $CZ/internal/chezmoi/sourcestate.go
    sed -n '15,20p;35,39p' $CZ/internal/cmd/ignoredcmd.go
    head -4 $CZ/LICENSE
    shasum -a 256 /tmp/osref/cz/cz73.zip

sha256（本次核对用）：

    cz73.zip  c99c43dd1724f4cea26ecab00bd06bb31bb28b23710b92eb3c1852e3609a14a1

## 13. 第三十八轮补记：音量按格设置（androidtv 0.0.75，2026-10-01）

前十二节回答的是「一个输入怎么变成一个键位 / 一份配置怎么搬家」。本节要回答的是
另一个问题：**音量此前只能连按音量键去凑，什么时候该「一步设到第 N 格」。**
从 3 格调到 12 格要按 9 次音量+，还容易按过头；老 ROM 连级数都读不到，用户根本不知道
现在几格。Home Assistant 遥控 Android TV 的协议库 androidtv 把这件事做对了：逐行读完，
三条口径照抄。

| 项 | 值 |
|---|---|
| 包名 / 版本 | PyPI androidtv 0.0.75（github.com/JeffLIrion/python-androidtv，commit 343b74ea7bb3d159f8a715190b4b9d8c00c2c0fd） |
| 许可 | MIT（LICENSE 首两行：The MIT License (MIT) / Copyright (c) 2020 Jeff Irion） |
| Copyright | Copyright (c) 2020 Jeff Irion |
| 核实方式 | git clone 到 /tmp/osref/atv/python-androidtv，checkout 343b74e，sed 逐行读 |

### 口径 1：set_volume_level() 的夹取公式——先 round 后夹

`androidtv/basetv/basetv_async.py:810` 的 `set_volume_level()` 里，
`basetv_async.py:830` 一行是整个功能的核心：

    830:         new_volume = int(min(max(round(self.max_volume * volume_level), 0.0), self.max_volume))

只有六个字值得抄：**先 round 后夹**。round 先把 7.6 变成 8，再夹到 [0, max]；
只夹不舍会把 7.6 卡成 7（滑条看上去停在 8、电视却是 7），只舍不夹会放出 16 / -1，
电视侧命令直接报错。配套的放弃路径在 `basetv_async.py:825-828`：max_volume
拿不到就先去 volume() 读一次，仍拿不到直接返回 None——**读不到格数就不设**，
不猜一个数发出去。

### 口径 2：两条 set 命令的有序降级

`androidtv/constants.py:145` 与 `androidtv/constants.py:148` 各有一条候选命令：

    145: CMD_VOLUME_SET_COMMAND = "media volume --show --stream 3 --set {}"
    148: CMD_VOLUME_SET_COMMAND11 = "cmd media_session volume --show --stream 3 --set {}"

`androidtv/basetv/basetv.py:261-284` 的 `_cmd_volume_set()` 按 sw_version 二选一
（11-14 走 :148 的新命令，其余走 :145 的旧命令）。本项目不学「按版本号选」：
那要先花一次 build.prop 查询才知道版本，而读通道本来就在跑
`media volume --show --stream 3`。改成**有序降级**：读通道已在用的
`media volume --show --stream 3 --set N` 先试，抛 AdbError 再试
`cmd media_session volume --show --stream 3 --set N`；两条都废就报错，
前端**退回按键**（音量键 24/25 在任何 ROM 上都认），不猜格数。

（`constants.py:675` 的 `MAX_VOLUME_REGEX_PATTERN = r"Max: (\d{1,})"` 是读通道解析
dumpsys audio 的正则，本项目读通道此前已实现同款解析，本轮未改动。）

### 分歧（有意）：松手才提交 + 半点向上

1. **拖动过程不提交**。androidtv 是协议库，一次调用就是一次 adb；本项目是遥控 UI，
   滑条拖动每帧一次 adb 会把输入锁堵死（AGENTS.md：adb 调用很贵）。所以前端 input
   事件只画本地、change（松手）才 POST；dragging 期间的轮询回读也不许把滑条弹回
   旧格数（`volSetReconcile` 的第三个参数就是干这个的）。
2. **舍入用半点向上，不用 Python 内建 round**。内建 round 是银行家舍入（8.5→8），
   前端 Math.round 是 8.5→9，直接用内建 round 会造出「滑条停在 9、电视设成 8」的
   错位。服务端 `_vol_finite_int()` 写成 `int(num + 0.5)`，与 `volSetClamp` 的
   Math.round 逐值对齐（`tests/test_volume_set.py` 有正反对调用例钉住）。

### 落到本项目

- `server.py`：`clamp_volume_level()`（先 round 后夹；max 缺省 / 0 / 负数 / 空 / 非数字
  一律退到 `VOLUME_MAX_FALLBACK = 15`）、`volume_set_cmd()`（两条候选的有序降级链）、
  `volume_set()`（读通道取 max → 夹取 → 按序试两条；成功后主动失效
  `_volume_cache`——音量是被我们亲手改掉的真状态，不清缓存下一次读会返回旧格数）、
  `POST /api/volume`（`handle_volume_set()`，经 ROUTES 分发，不自己判权限）。
- `static/app.js`：`volset` 纯函数段（`volSetClamp` / `volSetPct` / `volSetPresets` /
  `volSetReconcile` / `volSetUsable`，禁 DOM / localStorage / fetch，可搬进 node 跑
  harness）+ 段外胶水（`volSetPaint` / `volSetSync` / `volSetCommit` / `volSetRetarget`）。
- `static/index.html` / `static/style.css`：工具卡片里的滑条行（静音钮 + range + 填充 +
  格数）与五档预设按钮；读通道全废时整行 `dead` 置灰。

### 验证

- `node tests/volume_slider_harness.js`：10 组行为用例（抽 volset 段原样执行），
  ALL_VOLUME_CASES_PASSED。
- `python3 -m unittest tests.test_volume_set -v`：33 项。含 Python / JS 夹取逐值对齐
  （半点向上两条）、降级顺序（第二条兜底 / 两条都试过才放弃）、读通道全废时
  `adb.sets` 必须为空、clamp 发生在发命令之前、假 adb 断言缓存已失效、DOM 胶水
  接线（滑条 input/change/pointerup、静音 click、预设渲染、轮询钩子）、本第 13 节
  出处可 grep。
- `./check.sh`、`./sync-native.sh --check`、`git diff --check` 通过。

### 复现方式

    mkdir -p /tmp/osref/atv && cd /tmp/osref/atv
    git clone https://github.com/JeffLIrion/python-androidtv.git
    cd python-androidtv && git checkout 343b74ea7bb3d159f8a715190b4b9d8c00c2c0fd
    sed -n '145p;148p' androidtv/constants.py
    sed -n '810p;825,831p' androidtv/basetv/basetv_async.py
    sed -n '261,262p;280,284p' androidtv/basetv/basetv.py
    sed -n '675p' androidtv/constants.py
    head -3 LICENSE
    git log -1 --format='%H %ad %s'
