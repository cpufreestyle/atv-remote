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
