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

# 第十轮：PWA 离线壳 + 主图标（2026-09-25 深夜）

## 学习源
- Workbox 7（npm 镜像取 workbox-strategies@7.4.1 源码）：stale-while-revalidate 语义
  核对——缓存与网络并行、有缓存先返缓存、成功响应回写缓存；app-shell 预缓存。
- MDN「Service Worker API」scope 规则：SW 脚本的默认最大 scope 是自己所在目录。

## 设计
- static/sw.js：CACHE="atv-shell-v1" 预缓存 app shell（导航页 + 全部静态资源）；
  导航请求 network-first（失败回落缓存的壳，再 504 占位）；同源其他 GET 走
  stale-while-revalidate（res.type !== "opaque" 才写缓存）；/api/* 与非 GET 一律
  放行——实时状态进缓存就是 bug；activate 清旧缓存，防「新 HTML + 旧 JS」幽灵。
- static/app.js：load 后注册，成功置 dataset.sw="on" 并播报一次，失败置 "fail"
  （信号位，CDP 可断言）。首次控制器为空才播报，刷新不重复。
- 图标：tools/make_icons.py（4x 超采样，PIL）产出 192 / 512 / maskable-512 /
  apple-touch 四张 PNG；manifest 从「SVG any + maskable SVG」换成三张 PNG，
  保留 SVG any 行；index.html 补 apple-touch-icon 链接。
- 渐进增强边界：SW 只在安全上下文（https / localhost）注册，纯 HTTP 局域网注册
  静默失败、功能不变；图标 / manifest 随处可用。服务端无 TLS（已查证），不强推。
- server.py 新增 static_ctype()：显式 MIME 映射兜底——Android / Chaquopy 机没有
  /etc/mime.types 时 .webmanifest 会被 mimetypes 降级成 octet-stream，manifest 直接废掉。

## 踩到的坑
- SW scope 锁目录（CDP 真机抓到的 SecurityError）：脚本在 /static/sw.js，默认
  scope 锁死 /static/，注册 / 被浏览器拒。修复：/static/sw.js 响应带
  Service-Worker-Allowed: / 头（_send 加可选 extra_headers，Content-Length 纪律
  不变）。返工就出在这——iab 里 dataset.sw 是 "fail"，一度以为是平台不支持，
  用 CDP Runtime.evaluate 才拿到真实报错：先看错误再下结论。
- /api/ 与非 GET 必须在 fetch handler 最前面放行，否则 POST 遥控命令会被 SW 吞掉。
- iab 的 playwright.evaluate 只读作用域里 navigator 不可用（serviceWorker 探测
  会 TypeError），DOM 属性可读——验证 SW 信号走 dataset + CDP Runtime.evaluate。

## 验证
- tests/test_pwa.py 新增 13 个用例：manifest 字段 / 图标尺寸（IHDR 解，不依赖
  Pillow）/ maskable purpose / SW 三个事件 handler / api 与 POST 放行 / 壳预缓存 /
  缓存版本号 / static_ctype() 映射与大小写；tests/test_http_hardening.py 补 2 条：
  Service-Worker-Allowed: / 与 manifest 的 application/manifest+json。
- CDP 实测：dataset.sw 从 "fail" → "on"；Network.emulateNetworkConditions
  {offline:true} 后刷新，标题 / 连接按钮 / 样式全在（壳来自缓存），实时数据
  （Now Playing）按预期缺省——API 不进缓存。
- curl -D - /static/sw.js 有 Service-Worker-Allowed: /；manifest 为
  application/manifest+json。
- ./check.sh 全绿（87 单测）+ ./sync-native.sh 副本一致。

# 第11轮：键盘输入历史（2026-09-26）

## 学习源
npm @algolia/autocomplete-plugin-recent-searches@1.19.11（dist/esm 源码）：submit 时
onAdd 前置追加、getAll 截 limit、有查询时大小写不敏感子串过滤、localStorage 先试写
探测可用性（Safari 隐私模式 setItem 会抛）。补它的缺口：trim + 去重（它的去重靠
服务端 Query Suggestions 插件配合）；隐私模式整行隐藏 + 不记录。

## 设计
- #phraseRow 后加一行 #histRow：最近 N=10 条发送过的文本（单条截 200 字符），chip
  点击回填 #textInput 并聚焦，✕ 删单条，🗑 清空；输入框打字时按大小写不敏感子串
  收窄，无匹配整行隐藏。
- 数据结构就 atv.kbhist 一个 string[]，写入走 histSave() 试写容错；不进 state.json
  （敏感文件不放可编辑内容）的纪律与自定义宏一致。
- 历史文本一律 textContent 渲染（同 phrase chips），不碰 innerHTML。

## 踩到的坑
- Algolia 那个插件**不去重**（去重靠服务端 Query Suggestions），本地版必须自己补
  [t, ...filter(h!==t)]。
- 隐私模式「整行隐藏」比「仅不记录」更稳：否则旁人点一下输入框就看见此前的记录。
- chip 内层要再包一个 span（.hist-t）才能 ellipsis：flex 子项默认 min-width:auto，
  不会小于内容宽。
- CDP 行为流别在一条 evaluate 里塞 ~8 个 sleep（超时）；拆 3 段跑，每段少放 await。
- iab 的 playwright.evaluate 只读沙箱没有 navigator / localStorage 副作用，行为断言
  必须走 CDP Runtime.evaluate。
- 无匹配过滤时 renderHist 会清空整行 innerHTML，行为脚本要在点 chip 前把输入框清回去。

## 验证
- CDP 实测三段全过：① 发送记录 + 最新在前 + 重复发送去重不增条 + localStorage 一致；
  ② 「ep」「EVEN」大小写不敏感子串过滤、「zzz-nomatch」整行隐藏、chip 回填 + 聚焦；
  ③ 隐私模式整行隐藏且「secret-pw」录不进存储、关闭恢复可见、✕ 删单条 2→1、
  🗑 清空回 []。
- node --check static/app.js 过；./sync-native.sh 全绿（87 单测，副本一致）。
# 第12轮：Now Playing 快退/快进 ±15s（2026-09-26）

## 学习源
Plyr（sampotts/plyr，MIT）src/js/controls.js + defaults.js：
① seekTime 默认 10s 的固定偏移 seek，不做任意 scrub，标签显式带秒数（Rewind 10s）；
② 直播/无时长（hls.js/dash.js 用 2**32 哨兵值）时隐藏进度、不给 seek 控件；
③ rewind / fast-forward 与手势互补，作为显式单步操作。

## 设计
- npmeta 内两个 tiny 按钮 ⏪15 / ⏩15，点击走 sendKey(89) / sendKey(90)（后端无精确 seek，透传 media key）。
- npSeekSync()：seekable = !!(NP.shown && NP.duration)；false 时容器切 noseek class（变暗 + pointer-events:none）并禁用两按钮；true 时还原。renderNowPlaying 的 show / !show 两个分支都调用。
- seek 后 setTimeout(refreshNowPlaying, 800) 回读真值；与触摸板双指横滑（隐式连发）互补不重复。
- 无 duration 的直播流：禁用而非报错，用户点不到、也不会触发无用按键。

## 踩到的坑
- adb 没有精确 seek，实际幅度取决于当前 App；标签照 Plyr 带秒数，语义诚实，不假装精确。
- apply_patch 上下文行必须在当前文件里连续：本轮所有匹配失败都是自己先前的探针补丁破坏了相邻性，写大补丁前先 sed -n 看准原文，探针与正式补丁别交错。
- 同一文件多个 *** Update File: 段会被拒（multiple operations target <file>），同文件要合并成一个段 + 多个 @@ hunk。
- 「音量 OSD」是多行块注释的首行，行尾没有 */。
- CDP Runtime.evaluate 里别写 repl 作用域的 document（报 document is not defined），也别用自造的 _.result；用 IIFE 直接 return。

## 验证
- node --check static/app.js OK；./sync-native.sh 全绿（副本同步 + 87 单测 OK）。
- CDP 实测：点击 #npSeekBack / #npSeekFwd 日志 → keyevent 89 / → keyevent 90 无 ⚠；直播路径 renderNowPlaying({...duration:0}) → 双禁用 + class npseek noseek + npTime 空；refreshNowPlaying() 恢复 enabled（1:02 / 3:33, Rick Astley）。截图交付用户。
# 第13轮：引导改为连接后才提示（2026-09-26）

## 学习源
- driver.js@1.3.1 dist/driver.js.iife.js（20322 B，jsdelivr 固定版本路径）：找不到目标元素时退到 0×0 的 driver-dummy-element，spotlight 绝不为空；API 面是 drive / moveNext / moveTo / isFirstStep / isLastStep / hasNextStep + stagePadding / stageRadius；onDestroyed 钩子用来做「关掉即持久化」。
- shepherd.js@11.2.0 src/js/tour.js + src/js/step.js：tour 本质是 stage queue（addStep(options,index) / addSteps / getById(id) / show(key,forward) / next()，末尾自动 complete()）；关键在 Step.show() 是 beforeShowPromise().then(() => this._show())——步骤出不出场由前置条件决定，不是「进页面就全糊上来」。

## 设计
- 引导拆两段、key 分开存：COACH_KEY_PRE = atv.coached.pre（连接前只教连接），COACH_KEY_POST = atv.coached.post（连接后才教手势：长按连发 / 触摸板四手势 / 键盘遥控 / ⏪15⏩15）。看完、跳过、Esc 关掉都算看过（#coachMark 监听 closeModal 广播的 modalclosed 后 markCoachStage），之后不反复骚扰；设置里「重看一遍」= startCoach("all") 整段 5 步重放。
- coachStage 三态 "pre" / "post" / "all"：startCoach(stage) 里 "all" = COACH_HELP.pre.concat(COACH_HELP.post)；markCoachStage 对 "all" 同时记两个 key，避免重看完还漏一段。
- maybePostCoach()：postCoachPending 防重入 + coachOpen() + coachSeen("post") + status.connected 四道闸；过闸后 setTimeout 400ms 再出场，等本轮 renderStatus 把输入法查询、设备 chips 收尾后再量尺寸，光圈不会对到没稳定的布局上。挂点两处：renderStatus() 末尾（开着页面时连接成功就靠这次）与 boot 首帧（已连接走 maybePostCoach，未连接且没看过才 startCoach("pre")）。
- finishCoach() 的 chained：引导期间用户自己去点了连接，刚教完「怎么连」就已经连着 → 400ms 后顺势接 post，不用等刷新或下一次 8s 轮询。
- 隐藏目标跳步（showCoachStep）：目标 offsetParent === null 或 rect 宽高 < 1px → 后面还有步就 showCoachStep(i+1)，没有了就 finishCoach()。没东西在播时 npCard 整张收起，⏪15 那步自动消失；别家分支没有的卡同理——宁可少一步，也不把光圈空打在页面上。
- 步骤带 tab 字段：showCoachStep 先点 .tab[data-tab=...] 再读 rect（class 切换后同步读会触发重排，拿到的是新值）；resize 与 capture 阶段 scroll 时 repositionCoach 重新对光。

## 踩到的坑
- apply_patch 的上下文行必须先核对原文：static/app.js:167 是多行注释块的首行，行尾并没有 */*（*/ 在第 171 行末尾），连续两次补丁失败都是自己臆造了行尾；补丁后必须 awk 回看结果区——第一版把 #coachPrevBtn 的 classList.toggle 那行写重了一份。
- driver.js 的 master/dist/driver.js.iife.js 路径是 0 字节，必须用带版本号的 npm 路径（@1.3.1/dist/...）才拿得到真文件。
- iab 里新开 tab 才是恢复被 URL 策略困住的 data: 错误页的正解：Page.navigate / Page.reload / tab.goto / tab.back / 点页面内「重新加载」在策略层视角里全是「当前页是 data: URL」而被一律拒绝，换 cua.createBrowserTab("iab", url) 新开标签页即可绕开（tab 天生带 cd / 含 Runtime.evaluate）。

## 验证
- node --check static/app.js、import server + atv_backend、./sync-native.sh（87 单测 OK + 副本逐字节一致）、./sync-native.sh --check 全绿。
- CDP 实测 6 场景全过（fake adb + http://127.0.0.1:8411/）：
  A 已连接 + 清 keys → reload → 出 post 第 1 步「方向键可以长按连发」1/4，光圈 262×262；
  B 点「跳过」→ atv.coached.post=1 落盘 → reload 不再自动弹；
  C 断开 + 清 keys → reload → 出 pre「连接电视」1/1，光圈 208×60；
  D 跳过 pre 后连接 192.168.9.9:5555 成功 → 400ms 后自动接上 post 第 1 步（chained）；
  E 设置「重看一遍」→ stage "all" 共 5 步，顺序为 连接电视 → 方向键长按连发 → 触摸板四种手势 → 直接用键盘遥控 → ⏪15/⏩15 快进快退；
  F 隐藏 #touchpad 后 showCoachStep(2) → 直接跳到第 4 步「直接用键盘遥控」；隐藏 #npSeek 后 showCoachStep(4) → finishCoach() 收尾、弹窗关闭、key 落盘。
- 截图交付：post 第 1 步、chained 连接成功接 post、常态 UI。

## 第十四轮：学 Home Assistant script 实体 → 宏执行进度可视化（1.17.0 / versionCode 18）

### 学习源
homeassistant/components/script（Home Assistant 的 script 实体）。它把一次后台执行的
「当前第几步 / 完成几步 / 失败几步 / 是否被取消」当一等状态暴露给前端，UI 只做投影、
不自己推断跑到哪。同一思路也见 HA 的 automation 实体 trace（每步显式列举）。

### 为什么选它
项目里唯一在服务端线程里异步执行的命令就是 type=macro（_macro_worker），
20 步 × 每步 10s 延时会超过前端 fetch 的耐心。服务端明确「单步失败不中断整条宏」，
但失败原因只进 stderr，手机上完全看不到：哪一步挂了、跑到第几步、是不是被取消了。
进度可见性是这块唯一的窟窿。

### 落地
- server.py：_macro_lock + _macro_prog = {run, name, total, index, done, failed, cancelled}，
  macro_state() 扩为持锁的完整快照（dict 拷贝）；_macro_mark(**kw) / _macro_fail()
  （done / failed 是自增，必须锁内读-改-写）；handle_macro() 的 run 分支递增 run
  并重置进度——run 是单调运行代号，前端靠它区分「新的一次刚结束」和「从没跑过」。
- static/index.html：#macroCancelBtn 之后插 #macroProg（aria-live=polite，
  含 #macroProgFill / #macroProgStep / #macroProgFail）。
- static/app.js：renderMacroState() 重写。首帧只吸收历史结果不当成新事件；结束后汇总
  留在进度条原位，直到下一次运行顶掉；prog 字段缺失时降级、不阻断 8s 轮询。
  Macro.steps 只存步骤描述（自定义宏的步骤只在浏览器 localStorage）。
- static/style.css：.mprog / .mprogtrack / .mprogfill / .mprogmeta / .mprogfail + .ok/.warn/.bad
  语义色（复用音量 OSD 那一套画法），prefers-reduced-motion 下关 transition。
- 顺带两处同主题打磨：
  1. 宏按钮 tooltip 改用 macroStepText()（与进度条共一套步骤描述）——纯延时步以前在
     b.title 里被渲染成字面 undefined（app → undefined+2500ms）。
  2. macroShowResult() 里给 #macroState 收尾——少了这句，宏结束后状态行还停在
     「执行中」，和进度条自相矛盾。

### 踩到的坑
- Service Worker 会骗你：改完 static/app.js 后 reload，页面拿到的是 atv-shell-v1
  缓存里的旧代码，Page.reload / Network.setCacheDisabled 都不管用（SWR 分支先返缓存）。
  必须先 navigator.serviceWorker.getRegistrations() → unregister + caches.delete()，
  再 reload 才拿到新文件。本轮第一轮 CDP 断言里进度条生效但状态行没变，就是这个原因。
- repl 里 const 重声明不会抛到我眼前：第二次用同名变量时 evaluate 直接返回 undefined，
  仿佛页面逻辑坏了。换新变量名即可。
- iab 标签页跨 repl 调用会失效（Tab 20 is not part of browser session …），reload 失败就用
  cua.createBrowserTab("iab", url) 重开，别在旧 tab 上死磕。
- 本 harness 的 nodeRepl.emitImage 两种入参形态都不接受，repl 也没有网络（fetch 本地收件
  服务器失败），CDP 截图要落盘只能把 base64 经我这边中转，成本远高于收益——本轮改用 DOM
  断言做验收，截图不交付。

### 验证
- node --check static/app.js、python3 -c 'import server; import atv_backend' 通过。
- ./sync-native.sh：90 单测 OK（含 3 个新进度断言）+ 内嵌副本逐字节一致 → ALL CHECKS PASSED。
- CDP 实测（fake adb，http://127.0.0.1:8411/，8 次真实运行）：
  - 未执行：#macroProg = mprog hidden（getComputedStyle().display = none）、
    #macroCancelBtn 隐藏、状态行是默认提示词；
  - 执行中（点「看 YouTube」）：mprog running、width 50%、
    第 1 / 2 步 · 🎬 启动 YouTube（2 个候选包），1.5s 轮询一次持续推进；
  - 全部成功（点「回主页」）：mprog ok、100%、「回主页」完成 · 1/1 步、
    ✅ 宏「回主页」完成 · 1 步；
  - 有步骤失败（fake adb 下启动 app 必失败）：mprog warn、1 步失败、
    ⚠ 宏「看 YouTube」完成，2 步里有 1 步失败，#log 同步一行 ⚠ 汇总；
  - 取消（点「观影模式」后立刻点取消）：mprog bad、100%、「观影模式」已取消 · 1/3 步、
    ⏹ 宏「观影模式」已取消 · 执行了 1/3 步，取消按钮重新隐藏；
  - 刷新页面：进度条回到 hidden、步骤文案清空、状态行回默认提示——历史结果不会被
    当成「刚跑完的一条」再报一遍；
  - 服务端 /api/macros：run 每次 +1，末次 {run: 8, total: 3, index: 1, done: 1, failed: 0, cancelled: true}；
  - 5 个宏按钮 tooltip 均为可读步骤描述（🎬 启动 Netflix（2 个候选包） → ⏱ 等 2.5s → ⌨ 音量-、音量-、音量- ×3）。


## 第十五轮：学 Home Assistant automation trace → 宏分步 trace + 一键重跑失败步骤（1.18.0 / versionCode 19）

### 学习源
Home Assistant 每条 automation 的 trace（homeassistant/components/automation/trace）。
script 实体只回答「跑到哪了」，trace 再回答「每一步发生了什么」：每步显式列举、
成功/失败/被跳过各归其位，失败还带原因。第十四轮把「第几步」搬了过来，这一轮把
「每步一条」搬完整。

### 为什么选它
进度条只有一根条加两个数字：「第 2/3 步 · 1 步失败」之后，用户仍然不知道是哪一步、
为什么。手机上更没有 stderr 可看。而失败步骤在服务端本来就是可知的
（_macro_worker 的 except 分支一直拿着异常对象），缺的只是「把它当一等状态暴露，
再每步一条画出来」。既然知道哪一步失败，「只重跑失败步骤」是顺水推舟：宏本来就是
多条独立命令的串联，失败步重跑不需要理解整条宏的上下文。

### 落地
- server.py：
  - 新增 MACRO_ERR_LEN = 120（失败原因截断：轮询包要小，电视上的失败信息一句话够）。
  - _macro_prog 增加 trace 键；macro_state() 持锁深拷贝条目（dict(t)）。
  - 新增 _macro_trace(i, ms, err=None, cancel=False)：条目为 {i, ms, ok, 可选 cancel,
    可选 err}。三个出口都记——循环开头的立即取消、延时中被取消、单步 except、
    成功走 else。
  - handle_macro() run 分支 reset 时清空 trace，两次运行之间不残留。
- static/app.js：
  - macroTraceRows(j)：把服务端 trace 投影成 #macroTrace 的 li 行——状态徽标
    （✅/⏹/❌）+ 步骤描述（本地 Macro.steps 按步号查）+ 失败原因 + 耗时。按有无
    条目切 hidden；按有无「失败且非取消」条目切 #macroRerunBtn。
  - fmtMs(ms)：≥1000 走 fmtSec，否则 Nms——「等了 2.5s」和「卡住 2.5s」终于能分开。
  - #macroRerunBtn：取 lastTrace 里 !ok && !cancel 的条目 → 映射回 Macro.steps →
    runMacro(name + 「（重跑失败步）」, steps)。步骤不在这个浏览器里时 toast 说明
    （自定义宏存在 localStorage，服务端不存，state.json 不放可编辑内容）。
- static/index.html / style.css：#macroTrace + #macroRerunBtn；.mtrace 每步一行、
  三列 grid（徽标 auto / 正文 1fr / 耗时 auto），左边条按 ok/cancel/bad 切语义色，
  耗时列 tabular-nums 等宽数字。
- 测试 +4（trace 记录结果与原因 / 取消条目 / 运行间重置 / 纯延时步不等于失败），
  90 → 94 全过。

### 踩到的坑
- 纯延时步以前会漏进 handle_cmd({})，报「Android TV 不支持该命令: None」——trace
  上线后第一个 CDP 断言就撞见：每个带延时的预置宏（观影模式 / 看 YouTube）都被记
  一步假失败。修法：_macro_exec_step() 里 payload 没有 type 就直接 return（等待在
  _macro_step_delay 里已经做完了）。这是本轮唯一改行为的修复，trace 只是让它显形。
- 测试快照的浅拷贝陷阱：MacroRunTest.tearDown 用 dict(server._macro_prog) 保存现场，
  但 trace 条目是原地 append 进同一个 list 的——上一个用例的条目会漏进下一个用例
  （断言拿到 15 条而不是 3 条）。setUp 里把 _macro_prog["trace"] 换成新 list 才对。
- Service Worker 缓存（第十四轮记过，本轮又中一次）：unregister + caches.delete()
  之后 reload 才拿到新 app.js；判据是页面里 typeof macroTraceRows === "function"。
- 截图落盘这一轮跑通了：nodeRepl.emitImage 不收 base64、tab.screenshot({path}) 的
  path 被忽略、Page.setDownloadBehavior 被 harness 拒（它提示改用
  tab.playwright.waitForEvent("download")，但 data-URL 下载不触发 download 事件）。
  可用配方：python3 起一个写文件的 HTTP 接收器（tty:true 后台），repl 侧
  await fetch("http://127.0.0.1:8899/", {method:"POST", body: base64})，再 base64
  解码落盘。第十四轮「repl 没有网络」的结论作废——fetch 本地回环是通的。

### 验证
- node --check static/app.js、python3 -c 'import server; import atv_backend' 通过。
- ./sync-native.sh：94 单测 OK + 内嵌副本逐字节一致 → ALL CHECKS PASSED。
- CDP 实测（fake adb，http://127.0.0.1:8411/，新开 tab + 清 SW 缓存）：
  - 未执行：#macroTrace 与 #macroRerunBtn 均带 hidden（display: none）；
  - 全部成功（点「看 YouTube」）：执行中 1 行（✅ 启动 YouTube 0ms），结束后 2 行
    （第 2 行 ⏱ 等 2.5s = 2.5s）、mprog ok、状态行「✅ 完成 · 2 步」、重跑按钮保持隐藏；
  - 失败（自定义宏 com.example.notinstalled）：第 1 行 bad ❌
    「monkey 无法启动 com.example.notinstalled」22ms、第 2 行 ok ✅ 2.5s、
    mprog warn、状态行「⚠ 2 步里有 1 步失败」、重跑按钮出现；
  - 一键重跑：/api/macros 显示 name = 验收宏（重跑失败步）、total = 1，trace 只剩那 1 步；
  - 取消（点「观影模式」700ms 后取消）：末行 cancel ⏹「⏱ 等 2.5s」670ms、mprog bad、
    状态行「⏹ 已取消 · 1/3 步」、重跑按钮正确地保持隐藏（取消 ≠ 失败）；
  - 截图 /tmp/atv-macro-trace.png（2560×1440）即失败态 trace 界面。

## 第十六轮：学 VS Code Command Palette / Home Assistant Quick Bar → 命令面板 Ctrl/⌘+K（1.19.0 / versionCode 20）

### 学习源
- VS Code 的 Command Palette（platform/quickinput）：一个入口收拢全部命令，模糊匹配 +
  最近使用置顶 + ↑↓/Enter 键盘直达，鼠标只是可选项。
- Home Assistant 的 Quick Bar / header search：设备、实体、命令混在一个搜索框里，按域分组。
- Raycast 的 fuzzy 排序约定：标签词首 > 标签内子串 > 别名词首 > 散乱子序列。

### 为什么选它
页面能力已经堆到第五轮：连接/切换、应用启动、17 个按键、宏、睡眠定时、主题、隐私模式、
设置、引导重看……手机一屏摆不下，熟手也只能滚。命令面板不新增任何后端能力，只把已有
交互收拢成一个键盘优先的入口——UI 美观与功能实用性同时受益，且零协议风险（纯前端）。

### 设计
- 唤起：Ctrl/⌘+K 全局热键（document capture 阶段注册）+ header 的 ⌘ 按钮（title 含快捷键，
  tooltip 即文档）。
- 分组：设备 / 应用 / 按键 / 宏 / 工具 五组；空查询先「最近使用」再「全部命令」，有查询按
  分数排序取前 80 条。
- 匹配：palScore() 复刻四档计分，子序列兜底让 "yt" 命中 YouTube。
- 执行：全部复用现有函数/按钮 click()（sendKey / launchApp / runMacro / openModal…），
  零新增后端路由；palExec 先收面板再执行，避免和设置/截屏弹窗叠在一起。
- 无障碍：dialog + aria-modal、输入框 combobox（aria-expanded / aria-controls /
  aria-activedescendant 跟随选中行）、列表 listbox + option + aria-selected。
- 安全：设备名 / IP 来自局域网广播可伪造，渲染一律 textContent（AGENTS.md 禁令），
  列表用 DOM API 建节点，无 innerHTML。

### 落地
- static/index.html：header 加 #palBtn；#toast 后插 #cmdpal（modal + pal；#palInput combobox /
  #palList listbox / #palTip）。
- static/style.css：.modal.pal 段（居中卡、输入框、列表滚动、.palrow.on 选中条、.palhead
  分组头、.palempty 空态），只吃 CSS 令牌，不写 hex。
- static/app.js：命令面板段约 190 行。palRecent/palRemember（localStorage，8 条上限；
  自定义宏仍只进 localStorage，不进 state.json）；palScore / palCommands（48 条，设备来源
  是 palStatus 快照，宏来源 Macro.presets + customMacros()）；palRender/palSelect/
  palSyncActive/palMove/palExec。↑↓ 在 palMove 里跨分组头跳，Enter 在 palInput 上
  stopPropagation，K 绝不下发到电视。
- static/sw.js：CACHE v1 → v2（前端壳变更，强制刷新）。
- tests/test_command_palette.py：8 个测试——ARIA 契约（dialog/combobox/listbox）、
  palBtn tooltip、node --check、wiring（#palBtn→palOpen、input→palRender）、palRender 段禁
  innerHTML、CSS 无 hex、$("#id") 引用一致性（含 5 个动态 ID 白名单）。94 → 102 全过。

### 踩到的坑
- 快捷键穿透：palInput 的 keydown 必须自己 stopPropagation，否则 ↑↓/Enter 会同时被
  「键盘遥控电视」的全局 handler 消费——面板开着按 ↓，电视也跟着动。
- Cmd+K 与通用 modal 的 capture 兜底共存：兜底会 preventDefault + stopPropagation，但同节点
  同阶段的后续监听仍会执行。这正是热键注册在 document capture 阶段的原因：任何弹窗开着
  都能唤起/收起，引导（coach mark）开着也不例外（两者是 openModals 栈里的不同条目）。
- CDP 两次「假故障」都是测量顺序问题：Ctrl+K 是开/关切换，上个脚本把面板留在打开态，
  下个脚本的热键就变成关闭；测 visibility 前先读 classList 再决定动作，别假设初态。
- Service Worker 缓存老问题（第十四轮中过一次）：unregister + reload(ignoreCache) 才拿到新
  app.js，判据 typeof palOpen === "function"。
- 截图配方这一轮升级：不再依赖 cua_repl/nodeRepl。Chrome 已开着 9222，
  http://localhost:9222/json/list 拿 target、ws://…/devtools/page/<id> 连页面，Node 22 自带
  全局 WebSocket，约 80 行纯脚本即可 navigate / reload(ignoreCache) / Runtime.evaluate /
  派发 KeyboardEvent / Page.captureScreenshot 落盘。exec 的 JS 运行时不保证 stdout 回传，
  输出一律先重定向到 /tmp 再 cat。

### 验证
- node --check static/app.js、python3 -c 'import server; import atv_backend' 通过。
- ./sync-native.sh：102 单测 OK + 内嵌副本逐字节一致 → ALL CHECKS PASSED。
- CDP 实测（fake adb，http://127.0.0.1:8411/，清 SW 后 reload ignoreCache）24 项断言全过：
  - ARIA：#cmdpal aria-modal=true、#palInput role=combobox、#palList role=listbox、
    #palBtn title「命令面板（Ctrl / ⌘ + K）」；
  - Ctrl+K 唤起：class 由 "modal pal hidden" → "modal pal"，焦点自动落在 #palInput，
    空查询首条即「最近使用」→「全部命令」，#palTip「48 条命令」；
  - 模糊匹配：输入 you 命中 3 条，首位「打开 YouTube」，其余「打开 YouTube（最近使用）」
    「运行宏『看 YouTube』」；
  - Enter 执行：面板先收起再执行，fetch 记录 /api/cmd POST，localStorage atv.palRecent
    记下 app:com.google.android.youtube.tv；
  - 重开：「最近使用」分组置顶且选中态落在第一条；输入 zzzz 时显示
    「没有匹配『zzzz』的命令」；
  - 键盘：↓ 跨分组头自动跳（选中「按键 音量-」，aria-activedescendant = palrow-1 同步跟随）；
    Esc 关闭并把焦点还给触发按钮；
  - 几何：视口 1147×548 下面板 560×385 水平居中（x=294）、输入框 512×41、列表高 285 且
    scrollHeight 1792（可滚动）、行高 36。
  - 截图 /tmp/atv-cmdpal.png（空查询全量列表）与 /tmp/atv-cmdpal-recent.png（最近使用置顶）。

## 第十七轮：学 HA camera 查看器 / Google Photos 近期项目条 / macOS 预览 → 截屏工作台（1.20.0 / versionCode 21）

### 学习源
- Home Assistant 的 camera more-info：点开摄像头快照后可放大看细节、可拖拽平移，而不是只能
  看一张缩得小小的原图。截屏在 ATV Remote 里就扮演「camera snapshot」的角色。
- Google Photos 的近期项目条：底部水平滚动的缩略图条，点哪张看哪张，当前项有 accent 描边。
- macOS 预览的缩放约定：⌘+ / ⌘− / ⌘0（适应）、双击在 1:1 与适应态之间切换、触控板
  双指 pinch、单指拖拽平移——一套被验证了二十年的手势语法。

### 为什么选它
旧截屏弹窗只有一个 img + 两个按钮：480×270 的电视画面塞在 62vh 里，字基本看不清；想对比
「刚才那下动画到底出没出来」只能不停的截了看、看了截，上一张立刻被顶掉。电视的很多问题
（输入法候选栏、HDMI 信号丢失提示、视频网站的迷你播放器）恰恰是小字号细节，缩放+归档是
刚需而非装饰。整轮仍零后端改动——/api/screenshot 早就有，全部工作量在前端。

### 设计
- 三区结构：顶栏（角标 + 缩放按钮组）/ 舞台 / 缩略图条 + 底栏（hint + 保存/关闭）。
- 缩放模型：CSS 的 max-width/max-height 就是「适应态」，transform 只做
  translate+scale（transform-origin: 0 0），倍率 1–6 倍，以 stage 内锚点缩放
  （shotZoomAt：锚点对着的内容不动）。
- 手势：滚轮（passive:false，别把弹窗后的页面滚走）、单指拖拽（仅放大态有意义）、
  双指 pinch（以中指距为倍率，第二指落下重置基准防跳变）、双击 1.5x/回适应态。
- 拖拽边界 shotClamp：适应态强制归零，放大态左/上沿不越过 0、右/下沿不露底色。
- 归档 SHOT_MAX=8：blob URL 常驻内存，8 张约几十 MB 量级；驱逐时**只 revoke 被挤出的
  那一张**——在册的每一张都可能被再次点开；shotShow 换 src 与 revoke 同一 tick，不会
  「正在显示的图被 revoke」。
- 快捷键学预览：+ / − / 0，注册在 document capture 阶段（与命令面板同款），仅当截屏弹窗
  是 openModals 栈顶时生效——命令面板开着时这些键属于输入框。
- 无障碍：对话框 aria-label、缩放组 role=group + 倍率 aria-live=polite、缩略图条
  role=list/listitem + aria-current 标记当前项（选中态同时给出描边，不只依赖颜色）。
- 安全：设备名从状态栏 textContent 读（局域网广播可伪造），缩略图条全程 DOM API
  构建，无 innerHTML（AGENTS.md 禁令）。

### 落地
- static/index.html：#shotModal 从「单图 + 两按钮」换成工作台四段结构；#shotImg 加
  draggable=false（原生拖拽会把 blob 拖成一片空白）。
- static/style.css：在 .modal img 之后插「截屏工作台」段——等特异性后置生效覆盖
  margin-bottom / max-height；.shotstage touch-action:none 让浏览器把手势全交给 JS；
  只用 var(--*) 令牌无 hex。
- static/app.js：截屏段整块替换约 200 行。shotShots[] 归档（新→旧）/ shotUrl 指针 /
  shotView{s,x,y} / shotPtrs Map + shotPinch 基准；shotFmt（文件名）/ shotClock（角标）/
  shotApply / shotClamp / shotFit / shotZoomAt / shotRenderStrip / shotCapText / shotShow /
  shotPush / shotPtrEnd / shotLocal。#shotImg 的 load 事件回填 naturalWidth 到角标
  （截屏返回的是字节流，不加载完不知道真实像素）。modalclosed 复位缩放/手势状态。
- static/sw.js：CACHE atv-shell-v2 → v3。
- VERSION：versionName=1.20.0 / versionCode=21。
- tests/test_screenshot_workbench.py：10 个测试——HTML 契约（dialog/ARIA/download
  默认名）、wiring（14 个函数/常量/模板串）、驱逐正则（段内 URL.revokeObjectURL 恰好
  一次）、缩略图段禁 innerHTML、热键仅栈顶生效、CSS 段无 hex 含 touch-action:none、
  $("#id") 引用一致性。102 → 112 全过。

### 踩到的坑
- 段落边界别拿函数名当锚：测试 setUp 原计划用 loadMacros(); 收尾，它在 app.js 里出现 4 次
  （含 clearMacrosBtn 里的调用），index 取到第 593 行、早于 SHOT_MAX 的 1950 行，切片直接
  变空、两个测试报 substring not found。改用下一节唯一锚点「屏幕常亮（Wake Lock）」。
- 自测的严格度会反噬注释：shotRenderStrip 的注释写了「全程不碰 innerHTML」，被
  assertNotIn("innerHTML") 抓到——改成「全程 DOM API 构建」。
- 写测试文件时 heredoc 的反斜杠层数：\\\( 少转义一层变 \\(，findall 就去匹配字面反斜杠，
  SelectorDrift 假通过（refs 全被过滤掉）。用 od -c 与参照文件逐字节比对才发现。
- apply_patch 在这个 exec 运行时插 120 行新文件失败（FAIL undefined），改用
  cat > … <<'EOF' heredoc 成功；印证第十六轮结论——exec 的 stdout 不保证回传，
  命令输出要么重定向 /tmp 再 cat，要么用 .then(res => text(res.output)) 中转。

### 验证
- node --check static/app.js + node --check static/sw.js、python3 -c 'import server;
  import atv_backend' 通过。
- ./sync-native.sh：112 单测 OK + 内嵌副本逐字节一致 → ALL CHECKS PASSED。
- CDP 实测（fake adb 480×270 真 PNG，http://127.0.0.1:8411/，unregister SW +
  reload ignoreCache）：
  - 判活 typeof shotZoomAt / shotPush === "function"；
  - 首张截图：弹窗 class 由含 hidden → 无 hidden、#shotImg naturalWidth=480/naturalHeight=270、
    缩略图条 1 项、角标「480×270 · hh:mm:ss · Google Chromecast」、#shotSave 为
    blob: href + download「tv-20260926-021324.png」；
  - 缩放：#shotZoomIn → scale(1.4) / 140%，滚轮 → 1.61x（1.4×1.15），快捷键 + → 140%、
    − → 100%、0 → scale(1)、适应按钮 → 100%、双击在 1.5x 与适应态间切换；
  - 拖拽：合成 PointerEvent 单指拖动 60px，translate y 由 -88.2 → -48.2（x 被 clamp 在 0，
    图宽不足满铺时拖不出底色）；
  - 归档：第 2 张后条 2 项、点缩略图 0 切回第一张（aria-current 恰 1 项）；连点 10 次
    截屏后条恒为 8（驱逐生效，共发出 12 次 /api/screenshot）；
  - 安全：全程 Network 监听 /api/cmd 请求数 = 0（快捷键被截屏弹窗栈顶逻辑消费，
    不下发到电视）；
  - 几何：视口 1200×800 下弹窗 173–627 完整可见（顶栏 32 / 舞台 270 / 缩略图条 59 /
    底栏 44），8 个缩略图，关闭按钮可见；
  - 落盘 /tmp/atv-shot-workbench.png（274% 缩放 + 平移态，2400×1600@2x，844 色）。

## 第十八轮：学 Kodi keymap 分层覆盖 / macOS 修饰键重映射 / HA remote command 目录 → 按键自定义（1.21.0 / versionCode 22）

### 学习源
- Kodi 的 keymap.xml：用户改键从不重写整张键位表，只在用户目录放一份「差异」——没写的键
  全部落回内置默认。ATV Remote 的 18 个实体键指令本来就写死在 data-key 上，学它做成
  kmMap 只存差异，未改绑的键直接 sendKey(code)。
- macOS 系统设置→键盘→快捷键的修饰键重映射：一个物理键可被映射到另一个功能，且「轻点 /
  长按」是两种天然分工的手势（长按在 macOS 上还是「连续重复」的另一入口）。
- Home Assistant 的 remote.send_command 命令目录：命令是一份有限清单（HA 文档列成表），
  UI 渲染成可点选列表而不是让用户记字符串——#kmList 就是这份「动作目录」。

### 为什么选它
前三轮把「内容层」（截屏工作台、宏 trace、命令面板）补齐后，剩下的高频痛点是按键本身：
音量键在某些 App 里不管用、OK 键想一键开 Netflix、D-pad 上下想连发更快。过去这些要么改
代码、要么用宏绕。改键是高频诉求，但不能为它牺牲零学习成本——新用户打开就该和以前一模一样。
所以设计目标定为：未改绑的键行为逐字节不变，改绑只加不减。

### 设计
- 分层覆盖：kmMap 只记差异；绑回自己 = delete（kmBind 里 action === "key:" + code 即删键）；
  kmSend 未改绑走 if (!action) return sendKey(code);——零回归路径。
- 动作目录 18 项，四种 id 形态：key:<code>（另一个键位）/ app:<pkg>（启动 App）/
  macro:<id|name>（跑宏）/ none（空操作）。复用既有 sendKey / launchApp / runMacro，
  不新增执行通道。
- 长按是附加入口而非替代：pointerdown 仍即发（保住「一点就发」的旧手感），
  KM_EDIT_MS=550ms 到时先 stop() 再弹 #keymapModal；连发改为跟着绑定走——
  kmHoldable(bound()) 绑定目标是可连发键（HOLD_KEYS）才起连发，替换写死的 HOLD_KEYS.has(code)。
- 持久化只进 localStorage（atv.keymap_v1）：AGENTS.md 规定自定义内容不进 state.json
  （敏感文件），宏同理。
- 可见性：改过的键带 • 圆点 + title「按下：<动作>」；#kmHintBar 一条说清三件事
  （长按可改绑 / • 标记 / 连发跟随绑定）。
- 命令面板加 km 与 km:reset 两条，键盘用户不用摸鼠标。
- 无障碍：#keymapModal role=dialog + aria-modal + aria-label；#kmList role=list；当前行
  aria-current="true"（选中态同时有描边，不只靠颜色）；关闭按钮 data-autofocus。

### 落地
- static/index.html：.brow 后加提示条（#kmHintBar + #kmResetAllBtn）；#keymapModal 放在
  「音量 OSD」注释前。
- static/app.js：「按键自定义」段约 120 行——KM_LS / KM_EDIT_MS / KM_KEY_ACTIONS /
  loadKeymap / saveKeymap / kmKeyName / kmFind / kmSend / kmHoldable / kmActions /
  kmRenderList / kmOpen / kmBind / kmResetAll / kmRefreshMarks；控件绑定段重接 tick/bound/stop
  与 edit 定时器；palCommands 插 km / km:reset。
- static/style.css：插「按键自定义」段（列表 max-height:46vh、当前行 accent-soft 底 +
  accent-solid 描边、.remapped 的 ::after 圆点只吃 var(--*)）；static/sw.js CACHE → atv-shell-v4。
- tests/test_keymap.py：19 个测试（弹窗契约 / wiring / 未改绑回落 sendKey / none 空操作 /
  列表禁 innerHTML / classList.toggle 标记 / 自定义数据不进 state.json / 段边界与 kmSend
  恰好 3 次 / CSS 段无 hex / $("#id") 引用一致性），单测 111 → 130 全过。
- VERSION：versionName=1.21.0 / versionCode=22。

### 踩到的坑
- apply_patch 插段时上下文行同时出现在删除列表里会被整行删掉：第一次往 style.css 插段误删了
  .palfoot .paltip { margin-left: auto; }。以后插入改用「上一段最后一行」做纯 + 上下文。
- 往函数 def 后插回归测试会产生 pass stub + 重名 def（app.js 里 def test_none_action_runs_noop
  后插内容把原函数体挤重复）。插测试要选方法边界的空行，别贴着一个 def 开头。
- 长按与「按下即发」会打架：pointerdown 即发必须保留（旧手感），长按只是附加入口、
  触发时先 stop() 再 kmOpen()，否则粘连的连发会跟着弹窗一起跑。另外 CDP 派发按键只派
  pointerdown/pointerup、不要派 click——合成 click 的 detail===0 会被 click 分支拦掉，
  但真实鼠标点击 e.detail !== 0 才放行，这条 if 两种输入都不能少。
- kmFind(action) 不防空会引发全链路过载：未改绑键刷新标记时 kmFind(undefined) 抛
  TypeError，表现是「每次加载刷新标记就崩、长按没反应」，而 kmEditCode 已被置上、
  kmUnbind 照样能写出 "19":"none"——极具迷惑性。修法是首行 if (!action) return null;，
  并留 test_find_guards_empty_action 回归。
- CDP 脚本里 [data-key=19] 不是合法选择器（属性值必须加引号，数字开头的标识符不行）：
  querySelector 抛 SyntaxError，而 ev() helper 只读 r.result.value，异常被吞成 undefined、
  直到远处 writeFileSync 才炸。教训：helper 必须打印 exceptionDetails，失败要早点炸。
- 圆点像素验收别拍局部 clip：clip 经 dsf×scale 放大后落点难算（105 CSS px 出来 420 px，
  且没落在按钮上），且鼠标可能悬停制造 :hover 边框杂讯。改用「同页绑定前 A1 / A2 噪声
  基线 / 绑定后 B」全页 diff——差异簇与 bbox 直接说话；再注入品红调试盒（content:"D" +
  26px 品红背景）暴露 ::after 真实绘制盒，反证 top:1/right:5 相对按钮生效。

### 验证
- node --check static/app.js + static/sw.js、python3 -c 'import server; import atv_backend' 通过。
- ./check.sh + ./sync-native.sh：130 单测 OK（新增 19）+ 内嵌副本逐字节一致 →
  ALL CHECKS PASSED。
- CDP 实测（fake adb，http://127.0.0.1:8411/，unregister SW + reload ignoreCache）：
  - 零回归：点 [data-key="3"] 只派 pointerdown/pointerup，Network 恰 1 次
    {"type":"key","code":3}，弹窗不现、无 remapped；
  - 长按 [data-key="19"] 900ms 不松：pointerdown 那一发之外无第二发，弹窗开出、
    label=上、cur=当前：默认、rows=30（18+7+5）、groups=3；
  - 绑 app:com.google.android.youtube.tv → ls 落 localStorage、mark19=true、
    title=按下：打开 YouTube；再按 19 恰 1 次 {"type":"app",...}（走 launchApp）；
  - #kmUnbind → ls={"19":"none"}、再按 0 新请求（空操作生效）；
  - #kmResetAllBtn → ls={}、remapped 清空；
  - 几何：视口 1200×800 下弹窗 list 534×368、row 519×36、row_bg rgb(34,37,43)(=card2)、
    row_font 13.5px；落盘 /tmp/atv-keymap.png（2400×1600@2x）。
- 圆点像素级验收（新手法，可复用）：同页「绑定前 A1 / A2（噪声基线）/ 绑定后 B / 注入
  品红调试盒 C」四张全页截图做差——
  - A1-vs-A2 仅 187px 噪声（中下部一个会变的元素）；A1-vs-B 在其之外多出
    device(1720..1759, 600..639) 的 76px 簇 = CSS(860..880, 300..320)，正是键 19 右上角
    （按钮 CSS 798.66..875.33 × 298..374.66，top:1/right:5 应在处）；
  - 该簇 48px 为强蓝 (51,117,246)（≈ --accent-solid = hsl(212,100%,50%)）；B 全局强蓝
    像素比 A1 多 65、bbox 起点一致；
  - C 的品红盒落 CSS x 844..870 × y 299..325，与 top:1px/right:5px 相对按钮严丝合缝
    （position:relative 挂在 .dk.remapped 上，未改绑时 computed position 是 static）；
  - 落盘 /tmp/atv-keymap-dot.png（键 19 带点 / 键 20 无点对比，270×560，点区 65 个强蓝像素）。

## 第十九轮：学 Ansible `--check` / Terraform `plan` 的「计划=执行」同源契约 → 宏预演 dry-run（1.22.0 / versionCode 23）

### 学习源
- Ansible `--check`：dry-run 不另写一套规则，而是复用同一批 handler，把「执行」换成「将要执行」。
- Terraform `plan`：plan 与 apply 共享同一份 HCL + state 求值路径，plan 的输出就是对执行的承诺。
- 共同点：**预览的规则必须和执行的规则同源**。自己另抄一份规则的预览比没有预览更糟——它说
  「没问题」而真跑失败，用户就再也不会信任何预览。

### 为什么选它
第 17/18 轮把按键和宏编辑器做厚之后，宏成了站内最重的「盲发」入口：20 步 × 每步最多 10s 延时，
拼错包名、键码写成 `abc`、忘装 ADBKeyboard 打中文——错误只在点了「运行」几秒后才从 stderr 冒出来，
而宏是异步线程跑的，请求早就返回 200。把「运行时才发现」提前成「编辑时就知道」，是这一轮的事。

### 设计
- 点「🔍 预演」（⌘/Ctrl+Enter）不发请求、不碰设备：本地把宏按服务端 `validate_macro_steps`
  （server.py:1409）的同一套规则静态过一遍，逐行 ✓/⚠/✕ + 备注 + 预计耗时。
- 常量 FE/BE 一一对应，测试逐项比对（改一边忘另一边就红）：`MACRO_MAX_STEPS`/20、
  `MACRO_MAX_DELAY`/10000、`MAX_KEYCODES`/32、`MAX_TEXT_LEN`/5000、`MACRO_STEP_TYPES`、`APP_ID_RE`。
- 环境只回答「本地能回答的问题」：`imeCurrent`（当前是不是 ADBKeyboard）、`knownPkgs`（APPS +
  Now Playing 常见包名）。未知包名给 ⚠ 不给 ✕——「装没装」由设备回答，与服务端口径一致。
- 代码分两段、marker `/* ===== macro-dry-run:begin/end ===== */` 隔开：**纯函数段**（规则，可整段搬进
  node harness 与 Python 测试）+ **DOM 胶水**（取环境、渲染）。纯函数段禁 DOM/localStorage/fetch，有测试盯着。

### 落地
- static/index.html：宏编辑器按钮行加 `#macroDryBtn`（title 写明「本地静态检查，不发送、不执行」），
  其后 `#macroDry`（`aria-live="polite"`）与「预演不执行任何操作」hint。预演与「运行」并排而不是藏进
  折叠——它必须比运行更好点。
- static/app.js：+约 190 行。`macroDryRun(m, env)` 返回 `{rows, errs, warns, steps, totalMs, ok}`；
  行内容全部 textContent（设备名/IP 禁 innerHTML 的同源纪律）。
- static/style.css：「宏预演（dry-run）」段，全语义色（--ok-text/--warn-text/--danger-text），无 hex；
  汇总左侧一道 danger/warn 描边。
- tests/macro_dryrun_harness.js（新）：从 app.js 抽纯函数段 `new Function` 执行，17 用例
  → `ALL_DRYRUN_CASES_PASSED`。真行为在这儿给证据，不靠读代码。
- tests/test_macro_dryrun.py（新）：14 个测试——Markup 契约 / WiringAndPurity / ServerParity
  （常量逐项比对）/ HarnessBehavior（subprocess 跑 node）/ Style / SelectorDrift。
- VERSION：versionName=1.22.0 / versionCode=23。

### 踩到的坑
- **CDP 抓到一个只有真点才现的 bug**：`macroShowDryRun()` 的 JSON 解析失败分支有
  `box.classList.remove("hidden")`，成功渲染分支忘了——DOM 全渲染好了、面板还带着 hidden，用户点
  「预演」什么都没发生，而单测（断言 markup + harness 纯函数）一片绿。修法是在成功路径补上，并加
  `test_panel_unhidden_on_both_paths` 数这个函数里 `classList.remove` 出现次数必须为 2。
  教训：**「内容对不对」和「用户看不看得见」是两件事，后者只能靠浏览器里真点。**
- 第二个真 bug：延时超限时把 `dl.warn + "；"` 直接拼进备注，没有后续备注时就留下尾部分隔符
  （「会被钳到 10s；」）。改成 notes 数组 join，harness 里断言整串相等。
- harness 立抓 `String(c).lstrip("-")`：那是 Python 不是 JS，静态规则段根本跑不起来。JS 用
  `/^[0-9]+$/.test(String(c).replace(/^-+/, ""))`——注意与服务端 `str(c).lstrip("-").isdigit()`
  并不等价（服务端对 `-25` 放行、设备会失败），FE 端按「服务端口径 + 已知设备失败模式」双列，-25 给 ⚠。
- harness 下标假设：有 name 的宏没有顶层行（顶层行只在 name 缺失时出现），照搬「rows[0] 是汇总」直接错位。
- server.py 常量带行尾注释（`MACRO_MAX_DELAY = 10000      # 单步延时上限 ms`），测试比对前要
  `split("#")[0]`；FE 端 `//` 注释也要剥——两边注释风格不同，容易只剥一边（这次就漏了服务端）。
- harness 的汇总输出没有空格（`{"total":17,"failed":0}`），测试正则按 `{"total": 17, ...}` 写匹配不上。
- `tail` 后接 `echo $?` 判退出码会被管道吃掉，unittest 结果看 `OK` 字样。
- CDP 派发按键仍只派 pointerdown/pointerup（第十八轮教训复用）。

### 验证
- node --check static/app.js、python3 -c 'import server; import atv_backend' 通过。
- ./check.sh + ./sync-native.sh：145 单测 OK（新增 14）+ 内嵌副本逐字节一致 → ALL CHECKS PASSED。
- CDP 实测（fake adb，http://127.0.0.1:8411/，unregister SW + reload ignoreCache）：
  - 错误宏（`["abc"]` 键码 + 中文无 ADBKeyboard + 未知包名 + delay 99999）→ 点预演 →
    `#macroDry` 现身，汇总 `✕ 4 步 · 预计约 12.2s · 1 处会被服务端拒绝 · 4 处提醒`，
    5 行级别 [warn, err, warn, warn, warn]，备注逐条对得上；
  - **零副作用双证据**：fetch hook `window.__cmd` = 0，且 CDP `Network.requestWillBeSent` 里
    POST `/api/cmd` 计数为 0（预演期间与前后 delta 都是 0）；
  - Ctrl+Enter 在 `#macroText` 上同样触发；坏 JSON 走「⚠ JSON 解析失败」分支；
  - 干净宏（YouTube + 键码 85）→ `✓ 2 步 · 预计约 1.4s——可以放心跑`；
  - 计算样式：汇总 err 底 rgb(34,37,43) / 前景 rgb(244,113,118) / 12.5px / 左侧 rgb(215,66,71) 描边；
    warn 行 glyph rgb(246,194,90)、err 行 rgb(244,113,118)；面板 436×186 CSS px，
    无横向溢出（scrollW == clientW == 1200）；
  - 落盘 /tmp/atv-dryrun.png、/tmp/atv-dryrun-ok.png（2400×3816@2x 全页）。
