# UI 优化：开源项目学习笔记（2026-09-25）

## 学习源

GitHub / jsdelivr 在本机不可达，经腾讯 npm 镜像获取两份工程上广泛使用的开源实现作参照：

1. **Radix Colors**（`@radix-ui/colors`，MIT）—— 设计令牌的事实标准之一
2. **Bulma 1.0.2**（MIT）—— CSS 变量主题化的代表作

## Radix Colors 学到的四条

1. **12 级色阶，语义固定**：每色相 12 步，暗色主题下 step 1 最暗、12 最亮；
   实心交互一律用 **step 9-10**，低对比文字用 **step 11**，高对比文字用 **step 12**，
   微妙背景用 **step 3-5**。本项目原先的 `--accent`/`--accent2` 两值体系（一个文字一个实心）
   没有区分「hover/active/实心」三态，也不管对比度。
2. **中性色不是纯灰**：slate 档全程带 6-7% 蓝相（`hsl(220, 14%, 8%)` 一类），
   纯灰暗色下 UI 会发脏、文字发闷。
3. **A 通道后缀（如 `blueA`）是半透明变体**，用于叠加态；本项目用 `color-mix` 或
   预调 alpha 的 hsl 即可，无需全套。
4. **令牌只允许被引用，不允许在组件规则里写死**：新组件要加颜色必须先加令牌。

## Bulma 学到的两条

1. **主题全部挂在 CSS 自定义属性上**（`--bulma-*`），且把 HSL 拆成 H/S/L 三个变量，
   方便整体换肤；我们不需要换肤，但「一个语义一个变量」的命名法直接借用。
2. **控件尺寸/状态集中定义**：hover/focus/active/disabled 的状态写法在框架层统一，
   不在每个组件里重造。对应到本项目：按压反馈统一 `:active/.pressed + shadow-press`。

## 落到 static/ 的改动

| 项 | 之前 | 之后 |
|---|---|---|
| 色板 | 9 个散装 hex | `:root` 令牌：中性 3 档 + 蓝 4 档 + 状态色（ok/warn/danger 各双值：实心/文字），间距/圆角/阴影/动效同样令牌化 |
| 触控反馈 | `transform: scale(.95)` | scale + `--shadow-press`（inset 阴影，模拟按下去），速率提到 60ms |
| 触控目标 | chip/按钮高度随意 | `.chip ≥34px`、`.btn ≥40px`、`.dk ≥56px`；`touch-action: manipulation` 去掉 300ms 点按延迟 |
| D-pad | 定宽 246px | `min(246px, 72vw)` + `aspect-ratio: 1`，任意屏宽不溢出；OK 环用 accent-text 提高对比 |
| iPhone 适配 | 仅 `viewport-fit=cover` | `env(safe-area-inset-*)` 应用到 body/log/toast/modal，底栏不被小黑条盖住 |
| 键盘可达性 | 无 | 全局 `:focus-visible` 描边；`prefers-reduced-motion` 一律停动画 |
| 布局 | 定宽 480px 单列 | ≥760px（或横屏 ≥600px）自动双列：遥控+键盘在左列，应用/工具/宏/睡眠在右列 |
| 日志 | 单行覆盖 | 滚动带：按前缀分级着色、保留 60 行、自动滚底 |
| Toast | 直接出现 | 入场动画 + safe-area 偏移 |
| 输入 | `font-size:14px` | 统一 16px（iOS Safari 键盘弹出时 <16px 会触发页面缩放） |
| 加载态 | 一行文字「正在扫描…」 | `.skel` 骨架屏（shimmer），扫描/应用列表先给结构占位 |
| 连接态 | 只有个状态点 | 未连接时遥控三卡（方向键/键盘/宏）压暗 45% + 降饱和，把视线引到顶部连接操作；不允许 `pointer-events:none`（点了让服务端回「未连接电视」比没反应更能引导） |
| PWA | 无 | `manifest.webmanifest` + 双图标（普通/maskable）+ theme-color + apple-mobile-web-app 元信息，可「添加到主屏幕」全屏用 |

## 新增功能实用性（非纯样式）

1. **长按连发**：方向/音量/seek 键按住 450ms 后每 120ms 重发（滚列表、调音量不用点到手酸）；
   电源/静音/主页等状态翻转键白名单外，不连发。松手在**窗口任何位置**收到 pointerup 都停
   （手指滑出按钮、页面滚动、切后台）。
2. **单击不重发**：指针点击只在 pointerdown 发一次；click 仅服务键盘（Enter/Space，`detail===0`）。
   曾用时间戳判重，慢按 >80ms 会漏，已废弃。
3. **屏幕常亮（Wake Lock）**：遥控打开期间请求 screen wake lock，切前台自动补申请；
   iOS Safari 不支持则静默跳过。
4. **骨架屏**：见上表。

## 验证

- `./check.sh` 全绿（52 例单测 + import + node --check + 副本比对）
- Playwright 实测（430px 与 1280px 两档视口）：连发节奏 120ms、松手即停（无孤儿 interval）、
  单击 1 次、键盘 Enter 1 次、未连接压暗 computed opacity=0.45、manifest/图标 200

## 未采纳 / 后续

- 动效一律 ≤160ms 且可被 reduced-motion 关闭，没有引入复杂转场
- 触觉反馈（`navigator.vibrate`）上上轮已加，本轮未动
- light 主题未做：令牌体系（12 级色阶）已为此留好位置，需要时按 Radix 亮色档填值即可

---

# 第二轮：浅色主题 / 弹窗规范 / Switch（2026-09-25 晚）

## 学习源与四项对照

延续上一轮的「令牌化」结论，本轮对照三份开源实践里最影响日常体验的模式：

1. **Radix Colors 的浅色档方法论**（同一仓库的 light 档）：12 级步进编号不变、
   明暗整体反转；实心交互取最深档、低对比文字取中间档。本项目此前只有深色一档，
   白天/办公室场景一片漆黑。已按此法补 `prefers-color-scheme: light` 全覆盖。
2. **Radix Dialog 的行为契约**：role/aria-modal 语义、Esc 关闭、打开焦点移入、
   关闭焦点还原、Tab 焦点循环、背景锁滚动。本项目两个 modal（设置/截图）此前
   只有 classList 切换，键盘/读屏用户完全不可用。
3. **Radix Switch 的控件形态**：轨道 + 滑块，样式由 aria-checked 驱动，不用文字
   表状态；按压时滑块微撑宽的物理手感细节。
4. **Toast 遮挡问题**（实测发现，非开源对照）：`#toast` 浮层没有
   `pointer-events: none`，会吞掉下方遥控按钮的点击（Playwright 实测应用按钮
   点击全部超时，命中测试失败），已补。

## 落到 static/ 的改动

| 项 | 之前 | 之后 |
|---|---|---|
| 主题 | 仅深色一档令牌 | `:root` 深色 + `@media (prefers-color-scheme: light)` 全套覆盖：中性/语义/阴影/立体棱/遮罩/日志底板 15 个令牌；`theme-color` meta 拆深浅两条 |
| 弹窗 | classList 增删 hidden | `openModal/closeModal` 统一行为：Esc、焦点进出还原、Tab 循环、背景锁滚动；HTML 补 role=dialog/aria-modal/aria-label + data-autofocus |
| 键盘冲突 | Esc=返回电视 | 弹窗打开时 capture 阶段拦截导航键并 stopPropagation；Enter/Space 只断穿透不 preventDefault（保住按钮的键盘激活） |
| 震动开关 | 按钮文字 开/关 | `role="switch"` + `aria-checked` + 滑块 CSS 驱动 |
| 应用启动 | 直接 api 调用 | `launchApp()` 统一入口 + 最近使用（localStorage 最多 5 条置顶，🕘 行） |
| Toast | 吞点击 | `pointer-events: none` |

## 验证

- `./check.sh` 全绿 + `./sync-native.sh` 副本一致
- Playwright 实测：Esc 关弹窗后焦点还原齿轮、Enter 只关弹窗不给电视发 OK、
  方向键弹窗打开时不穿透、switch aria-checked 双向切换、最近应用记录与置顶渲染、
  浅色 computed 样式抽查（bg rgb(246,247,249)/卡片白/立体棱浅灰）、深浅双主题截图

---

# 第三轮：触摸板双指手势（2026-09-25 深夜）

## 学习源

桌面触控板与 Google TV 官方遥控的手势惯例：双指滚动=连续调节、双指轻点=播放/暂停。
落到场景：手机遥控器上「调音量/快进」要么去点小按键（单手时够不着），要么连点到手酸，
双指手势把这两个最高频操作放进触摸板这一块大区域。

## 设计与取舍

- **映射**：双指上下滑=音量（24/25），左右滑=快进快退（89/90），双指轻点=播放/暂停（85）。
  每 26px 发一次键；`sendKey` 自带 90ms 节流，天然限速，还顺带点亮屏幕上的音量/媒体键
  （`flashKey`），用户能看到按键被同步按下。
- **防误触**：第二根手指落下即作废进行中的单指滑动；两根手指都抬起才结算手势，
  剩下那根手指不接续单指操作；位移 <12px 且 <350ms 才算轻点。
- **反馈**：手势期间触摸板内提示语实时显示「🔉 音量 -2 / ⏩ 快进 ×1」，结束 600ms 后恢复默认。

## 实测抓出并修掉的两个 bug

1. **单指操作被自己改崩**：`pointerup` 里先 `padPtrs.delete()` 再判 `size !== 1`，单指永远
   通不过——tap/swipe 全部失效。CDP 多点触控实测（`Input.dispatchTouchEvent`）抓到。
2. **轮询覆盖手势说明**：`renderStatus` 每 8s 用硬编码旧文案重写 `#padHint`，新手势说明
   撑不过一个轮询周期，且恢复逻辑用的是加载时常量。已把两处文案收敛成单一函数
   `padHintText(isApple)`，两处同文，恢复时按当前设备类型取。

## 验证

- CDP 双指实测：下滑→音量减 ×2、右滑→快进 ×2、左滑→快退 ×2、双轻点→播放/暂停、
  单指点→tap、单指下滑→swipe；等一个 8s 轮询后提示语保持完整；无页面报错。
- `./check.sh` 全绿 + `./sync-native.sh` 副本一致。
- 环境备注：本机 playwright-cli 的常驻 Chromium 守护进程会供旧缓存页面，截图/实测
  要用「起新实例 + 校验 DOM」的方式绕过；服务端 `Cache-Control: no-store` 本身没问题
  （curl 20/20 全新）。

---

# 第四轮：触摸板单指长按=连续滚动（2026-09-25）

## 学习源

按键区长按连发已有的交互范式，扩展到触摸板：桌面触控板长按拖拽惯性滚动的简化版
（Android TV 遥控 app 的 continuous scroll）。场景：滚片单/列表时，连点方向键点到手酸，
而触摸板这一大块面积之前只有「点一下=点击、拖动=滑动」两种语义，长按是空白。

## 设计

- 单指按住不动 **450ms** 起进入连续滚动，之后每 **120ms** 发一次方向键（与按键区
  长按完全同款节奏，用户学过一次就懂）；`sendKey` 自带 90ms 节流天然防超发，
  且每发一次都 `flashKey` 点亮屏幕方向键，用户看到按键在同步按。
- **方向语义**：静止按住=向下滚（列表默认方向）；起滚后手指往哪边偏（>24px）就往
  哪边滚，上/左/右都支持，横竖列表都能用。
- **互斥**：450ms 内移动 >12px 就撤掉长按定时器（那是滑动意图）；进入长按后松手
  只停滚，这次按压**不再触发 tap/swipe**——长按和滑动是两种意图，不能都发；
  第二指落下/pointercancel 同样撤销。
- **反馈**：`.holding` 琥珀色描边 + 提示语「⏬ 连续滚动（下）」，与拖动的蓝色、双指的
  高亮色区分。

## 验证（CDP 多点触控）

- 长按 1.4s 静止 → 连发 8 次 DOWN(20)，松手后命令数不增（无 tap/swipe 补发）
- 长按 0.7s 后上拖 80px → 方向切换为 UP(19) 连发
- 450ms 内快速拖动 → 只有 swipe，不长按；快速轻点 → 只有 tap
- 提示语/边框 class 实测正确；无页面报错

---

# 第五轮：首次使用引导 coach mark（2026-09-25）

## 学习源

前三轮加了一堆隐形能力（双指手势、长按滚动、键盘遥控、长按连发）——不引导根本
发现不了。对标 **Material 的 feature discovery（coach mark）**：暗场里只给目标元素
留一块光圈 + 一张吸附式说明卡，Intro.js / driver.js 同款形态。

## 实现

- **光圈**：一个 `position: fixed` 的空 div，用 `box-shadow: 0 0 0 9999px var(--scrim)`
  当遮罩——比 SVG mask / 四个 div 拼都简单，且天然跟随圆角令牌。
- **说明卡**：吸附目标下方，下方放不下（矮屏/目标在底部）自动翻到上方；卡片带步骤
  计数、跳过/上一步/下一步，最后一步变「完成」。
- **基础设施全复用**：直接套 `.modal` + `openModal/closeModal`（Esc、focus 管理、
  背景锁滚动白拿）；`closeModal` 新增关闭时广播 `modalclosed` 事件，引导靠它接住
  「Esc 关掉也算看过了」并落盘，不用把引导的知识塞进通用弹窗代码。
- **tab 联动**：目标在另一个 panel（触摸板步骤）时先切 tab 再量尺寸——class 切换后
  同步读 `getBoundingClientRect()` 会触发重排，拿到的就是新值，不需要 rAF 等待。
- **记忆**：localStorage `atv.coached`，首访加载 600ms 后自动开始；跳过/完成/Esc 都
  写标记不再骚扰；设置弹窗「使用指引 → 查看」随时重看（重看不清标记）。

## 验证（全新浏览器上下文，localStorage 干净）

- 首访 600ms 自动弹出；第 1 步光圈盖住 IP 输入框、焦点落在「下一步」
- 4 步导航正常，第 3 步自动切到触摸板 tab 且光圈高度对准触摸板；「上一步」按步骤显隐
- 完成/Esc 后 `atv.coached=1`，刷新不再自动弹；清标记后又自动弹
- 设置里「查看」能重看（设置弹窗留在下层，引导关掉后焦点回到齿轮）
- 无页面报错；`./check.sh` 全绿 + 副本一致

---

# 第六轮：主题三选（跟随系统 / 浅色 / 深色）（2026-09-25）

## 学习源

Radix Themes 的 theme switcher + next-themes 的无闪烁首帧方案。动机：看电视常在暗房间，
而手机是浅色模式——只跟随系统的话，用户要么忍受白闪闪的遥控器，要么去改系统设置。

## 实现

- **CSS 优先级**：深色是 `:root` 基座；`html[data-theme="light"]` 用属性选择器
  (0,2,0) 压过媒体查询的 `:root` (0,1,0)，显式选择因此能**双向**压过系统；
  媒体查询改署 `:root:not([data-theme])`，用户一旦显式选过就整体退场，回到「跟随」
  （删属性）重新交还系统。浅色令牌两条路径共用一份（已注明必须同步改）。
- **首帧不闪**：`<head>` 内联三行脚本先于渲染读 `localStorage["atv.theme"]` 写上
  `dataset.theme`（next-themes 同款做法）；没有这步，深色偏好用户每次进页面先闪一帧白。
- **控件**：分段按钮组（跟随/浅色/深色，参照 Radix Themes switcher 形态），
  `aria-pressed` 同步状态；偏好存 localStorage 不进 state.json。

## 实测抓到的 bug

初版把浅色令牌块嵌成了 `:root[data-theme="light"] { :root { … } }`——CSS 嵌套会编译成
**后代选择器**，永远不匹配，显式浅色完全失效（系统浅色看着正常是因为媒体查询那条路
还通着，差点漏掉）。改为扁平结构后通过。

## 验证（emulateMedia 切系统 + 显式选择交叉）

- 系统深色/浅色动态跟随 ✅；显式浅色压过深色系统 ✅；显式深色 ✅
- 切换与刷新后持久化保持 ✅；回「跟随」删属性、重新跟随系统（含系统再切换）✅
- 分段控件选中态/aria-pressed 正确；无页面报错

---

# 第七轮：自定义宏可视化编辑器（2026-09-25）

## 学习源

可视化自动化工具的共同形态（iOS 快捷指令 / Tasker / Home Assistant 脚本编辑器）：
文本是事实源、结构化列表是投影、参数用表单收集。本项目原来只给一个 JSON textarea，
用户得手写 `{"name":…,"steps":[…]}`——一级拦路虎。

## 设计

- **textarea 保持唯一事实源**：列表只是它的实时投影（250ms 防抖重渲染）。这样已存的
  本机宏、手动粘贴的 JSON 全部继续可用，不存在两套数据模型漂移。
- **步骤行人话**：`🎬 启动 Netflix`（包名自动解析中文名，含 Apple TV 列表动态补充）、
  `⌨ 音量-、音量-、音量- ×3`、`⏱ 等 2.5s`、`🔤 输入「晚安」`；动作步自带延时的显示
  琥珀色徽标「等 Ns」。
- **行内操作**：↑↓ 重排（splice 换位）、✕ 删除，操作都写回 JSON 再重渲染。
- **快捷添加**：四类参数表单（应用=包名输入 + datalist 候选、按键=下拉、等待=秒数
  钳制 0.1–10s、文本），Enter 直接确认；首次添加自动补 `name`。
- **错误态**：JSON 解析失败只报错**不清空**——用户改 JSON 的半途列表不闪没，改完自动恢复。

## 实测抓到的 bug

`renderMacroSteps` 先 `box.textContent = ""` 再解析，注释写的「保留最后一版好状态」
实际做不到——错误一来列表全空。把解析挪到清空之前后通过。

## 验证

- 四项添加（应用/按键/等待/文本）步骤文案与 JSON 落库正确；重排、删除、保存本机宏
  （macroRow 5 预置 + 1 ⭐）正常；非法 JSON 报错且保留 2 行旧步骤、改回后恢复
- 无页面报错；`./check.sh` 全绿 + 副本一致

---

# 第八轮：正在播放信息条（2026-09-25）

## 学习源

Google TV 官方遥控 / Kodi Remote：顶部常驻 now-playing 条——片名、艺人、进度，
不用切回电视画面就知道在放什么、放到哪。本项目此前只有「电视截屏」能间接看到，
路径太重。

## 设计

- **数据源** `dumpsys media_session`：唯一不依赖 App 主动配合的系统级接口。
  Python 侧 `parse_media_session()` 宽松解析（见下）。代价控制：3.5s 结果缓存、
  休眠不查（`screen_awake` 复用其 1s 缓存）；轮询蹭 8s 状态轮询的车，页面隐藏时
  随之暂停（`pageVisible` 已有机制），不新增定时器。
- **前端进度本地插值**：8s 一跳会显得像卡带，所以两次轮询之间按 `Date.now()`
  每秒外推 `position`，`npPaint()` 重算填充宽度与 `m:ss / m:ss` 文案；
  `transition: width 900ms linear` 与外推节奏对齐。
- **降级策略**：认不出字段就缺省（不是报错），没有 title 且不在播放 → 卡片收起；
  查询失败 → 收起，不留假进度条。Apple TV 无此接口（pyatv `metadata.playing()`
  字段太稀疏），路由直接返回 `{"connected": false}`，卡片永远隐藏。
- **安全**：片名/艺人来自电视侧元数据，可被伪造/乱码 → 一律 `textContent`。
- **播放/暂停**按钮复用现成 `/api/cmd` key 85，不新增权限判断。

## 踩到的坑

- **dump 格式没有契约**：`state=PLAYING, pos=61250` 与 `PlaybackState {state=2,…}`
  两种风格并存，`pos=` / `position=` 混用。正则第一版忘加 `re.MULTILINE`，
  `^\s*packageName=` 一条都匹配不上（单测立刻抓到）——解析崩了遥控整页挂，
  比不显示信息条严重得多，所以用例按多种 ROM 风格各钉一份。
- **字形豆腐块**：暂停按钮原用 `⏸`，canvas measureText 实测该字形在本机字体栈
  渲染成 tofu（`⏸`/`⏹` 宽度一致且异常）。改用文字「暂停/播放」——零字体依赖，
  语义也更明确。信息条图标只用渲染充分的 `🎬`。

## 验证

- 单测 58 项全绿（新增 `tests/test_media_session.py`：AOSP / 大括号 / 空栈 /
  乱码 / 多会话取栈顶 5 组解析 + 未连接路由行为）
- CDP 实测：播放态（片名截断/副标题/进度 1:02-3:33 29% 起步）、2.2s 后插值到
  1:04/29.9%、暂停态（⏸→文字、`已暂停 · Netflix`、`7:12 / 52:00`）、未连接收起、
  Apple TV 类型保持隐藏、播放暂停按钮 POST `{"type":"key","code":85}` 且无设备时
  toast 报错不崩；深浅两主题截图过
- `./check.sh` 全绿 + `./sync-native.sh` 副本一致
---

# 第九轮：音量 OSD（2026-09-25 晚）

## 学习源

Android TV / Google TV 官方遥控与 Apple TV Remote：按音量键立刻弹出 OSD
（图标 + 格子 + 数字），约 1 秒后淡出。本项目的音量键此前是「盲按」——
手机端零反馈，只能抬头看电视。

## 设计

- **钩子只挂一处**：所有音量入口（按键区 🔊＋/－/🔇、物理键盘媒体键、
  触摸板双指、长按连发）最终都汇到 `sendKey()`，于是在 `sendKey` 里按
  `VOL_KEYS = {24, 25, 164}` 调 `volBump()`——本地先画 OSD，不等 adb 回包。
- **乐观步进 + 真值校正**：本地按 `level+1`（静音键直接进静音态）先推一格；
  220ms 防抖后打 `/api/volume` 取真值校正，1.6s 无新按键自动隐藏。
- **后端 `/api/volume`**：`media volume --stream 3 --get` 为主通道，
  `dumpsys audio` 的 STREAM_MUSIC 段兜底；`VOLUME_TTL=0.8` 秒缓存，
  长按连发不会把 adb 打爆；非 Android 设备返回 `{"connected": false}`。
- **降级**：设备断开/不支持时退化成纯图标模式（不出格子）；
  `prefers-reduced-motion` 下停过渡动画；配色全部走既有令牌。

## 实测抓到的 bug（CDP 真机浏览器）

静音后 OSD 只闪约 60ms 就被真值校正抹掉。链路：`media volume --get`
**不返回静音位**，后端原实现默认 `muted: False`，前端 `Vol.muted = !!v.muted`
把「静音」清成了格数。修复为三态：

- 后端 `muted` 默认 `None`（未知），只有 `dumpsys audio` 读到
  `Muted:` / `Mute count:[1-9]` 才给布尔值；
- 前端 `typeof v.muted === "boolean"` 才采纳真值，否则保留本地乐观值；
- 单测补 `assertIsNone` + `test_dumpsys_mute_state_reaches_frontend` 钉住。

## 验证

- `tests/test_volume.py` 新增 14 个用例（解析 5 + volume() 查询 6 + 路由 3，FakeAdb 替身）
- CDP 实测：静音后 80ms / 780ms 均为 `{cls:"vosd ismuted", icon:"🔇", num:"静音", fill:"53%"}`，
  不再回弹；音量 8/15 正常
- `curl /api/volume` → `{"connected":true,"supported":true,"level":8,"max":15,"muted":null}`
- `./check.sh` 全绿（72 单测）+ `./sync-native.sh` 副本一致
