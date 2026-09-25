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
