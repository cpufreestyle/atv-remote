# 交接 — ATV Remote（2026-09-26 夜）

> 本轮从「装个 APK 就能用」这条主线出发，一路挖到安装链路、图标、下载三处真 bug。
> 下面按「现在是什么状态 → 怎么验证 → 还剩什么」写，每条结论都有实测数字。

## 0. 一分钟速览

| 项 | 值 |
|---|---|
| 版本 | `VERSION` = 1.34.0 / versionCode 35（本轮已发版） |
| 测试 | `python3 -m unittest discover -s tests` → **455 通过**（skipped=7） |
| 一键校验 | `./check.sh` → ✅ ALL CHECKS PASSED（系统 python3 与 `.venv` 各跑一遍） |
| 内嵌副本 | `./sync-native.sh --check` → ✅ 一致 |
| 服务 | LaunchAgent `com.atv.remote`，`.venv/bin/python server.py --no-open --no-token`，`:8300` |
| 局域网 | `http://192.168.1.109:8300`（令牌已关，手机直连免输入） |
| APK | `ATVRemote-native.apk` 20,261,447 字节（原生版，自带引擎 + adb，SHA256 `b69ece…8482`） |
| git | 工作区**有未提交改动**（33 文件 + 若干新文件，见 §6） |

## 1. 本轮解决的问题（按发现顺序）

### 1.1 手机装 APK 用不了 → 其实是两件事叠在一起

**现象**：装上原生 APK 打不开遥控功能 / 页面提示「apk不存在」。

**根因 A**：页面上「下载 APK」链接触发 `/app.apk`，而后端一直发的是
`android/ATVRemote.apk` —— 那是 **62KB 的 WebView 壳**，没有自己的引擎，必须 Mac 上
跑着服务才能用。

**根因 B**：原生 APK 虽然自带 Python 引擎，但 `server.py` 的 `Adb` 类是 `subprocess`
调 **adb 可执行文件** 的，而 Android App 沙箱里压根没有 adb（`boot.py` 里原来就写着
指向一个不存在的路径）。所以它连 Android TV 也连不上。

**修法**：

- `server.py:2085` 起 `/app.apk` 优先发 `ATVRemote-native.apk`，没有才退回壳；
- **给 APK 塞进一个真能跑的 adb**：
  - `android-native/app/src/main/jniLibs/arm64-v8a/libadb.so` —— Termux `android-tools`
    里为 aarch64 Android 编的 adb。放 jniLibs 是因为装机时 PackageManager 会解到
    `nativeLibraryDir`，**Android 上基本只有那个目录允许 `execve()`**（10+ 起 App 私有
    目录一律 noexec）。**文件名必须以 `.so` 结尾**，否则 AGP 静默不打包（第一次直接叫
    `adb`，包里就真的没有）；
  - 依赖库在 `assets/adb-libs/`（**56 个**，8.0MB），由 `NativeActivity` 首次启动解到
    `files/adb-libs`，再用 `LD_LIBRARY_PATH` 指过去。走 assets 是因为 `libz.so.1` 这类
    带版本号的名字不能改；
  - `build.gradle` 关掉 `jniLibs.useLegacyPackaging` 的现代默认（不解压就没法 execve）。

### 1.2 模拟器实测又抓出三个「Mac 上看不到」的 bug

用 AVD `test_avd`（arm64，headless）真跑了一遍，一跑就崩：

1. **引擎根本没起来**：`boot.py` 的 `_install_crypto_shim()` 里兜底 `from cryptography... import`
   没保护。降级构建（`-PnoAppletv`）不装 cryptography → `ModuleNotFoundError` 冒到线程外
   → 服务线程死掉，App 只显示「引擎启动失败」。
2. **路径全拿不到**：`from com.chaquo.python import Android` 在后台线程报
   `ModuleNotFoundError: No module named 'com'` —— 这条一直是**静默失败**的（旧代码的
   except 把它吞了），所以 `ADB_PATH` 从来没设上过。改成 **NativeActivity 在 Java 侧把
   `files` / `nativeLibraryDir` / `adb-libs` 传进 Python**（Java 本来就握着这些路径，
   没必要绕 Chaquopy 的 Python→Java 桥）。
3. **依赖只收了一层**：adb 自己只要 8 个库，但 `libprotobuf.so` 还要 `libabsl_die_if_null.so`
   等一串 → `CANNOT LINK EXECUTABLE`。抓包脚本改成**传递闭包**解析。

**最终模拟器实测**（可复现：`tools/emulator-test.sh`）：

- 引擎横幅：`adb : .../lib/arm64/libadb.so (Android Debug Bridge version 1.0.41)`
- `/api/status` → `adb_found: true`；`/api/connect` → 拿到设备信息；
  `/api/cmd` 按键 → `{"ok":true}`；`/api/screenshot` → 真 PNG 1080×2400；
- 界面 OCR 确认 WebView 加载了内嵌界面。

### 1.3 「看不到图标」→ 两个独立原因

1. **手机桌面图标是一块深色方块（主因）**：Android 自适应图标 = 背景 `#1d212b` +
   `res/mipmap-*/ic_launcher_fg.png`。9/26 重做图标时只更新了 `mac/` 和 `static/`，
   **漏了 `res/` 那五张**，它们还是 8/27 的旧图。WCAG 对比度实测 **1.08:1**（阈值 3:1）。
   新增 `tools/make-android-icons.py` 生成五档 + **自带对比度自查**（不合格非零退出）。
   现在 15.11:1。
2. **浏览器标签页图标被旧 emoji 图顶掉**：`index.html` 里最后一条 `rel=icon` 是早期写死的
   emoji data-URI（📺）。浏览器取「最后一个能用的 `rel=icon`」当主图标，新图标**根本没被用上**。
   已删掉那条，并锁住顺序：PNG 在前、SVG 在后。

### 1.4 「点击了无法下载」→ 三个原因叠加

用模拟器里真 Chrome 复现：

1. **服务器没实现 HEAD → 回 501**（主因）。下载管理器与「点链接后先探测」的浏览器都会先发
   `HEAD`，看到 501 就放弃——**而地址栏直接敲 URL（GET）却是好的**，极难查。现在 `do_HEAD`
   走同一条 `_serve()`，只回头不回体（`Content-Length` 仍是真实字节数）。
2. **没有 `Content-Disposition`** → 浏览器按 URL 命名（`app.apk`），下载管理器也不当它是安装包。
   现在 `attachment; filename="ATVRemote.apk"`。
3. **Service Worker 把下载搞坏了**：通用分支里 `cache.put()` 的 promise 没被 await，它 reject
   时被后面的 `.catch(() => hit)` 接住——于是「缓存写失败」被当成「网络失败」，无缓存命中时
   `respondWith(undefined)`，浏览器直接报下载失败。20MB 的 APK 写 Cache Storage 很容易踩配额。
   现在下载类请求（`*.apk`/`*.tgz`/`*.zip`、`no-cors`）在进缓存分支**之前就放行**，且
   `cache.put(...).catch(() => {})` 永不吞响应。缓存名 bump 到 `atv-shell-v5`。

**模拟器实测**：落盘 `/sdcard/Download/ATVRemote.apk`，20,261,451 字节，**SHA256 与服务器一致**
（`08063d89…9fd7`），拉回来验包是合法 ZIP、120 条目、`testzip()` 无损坏。

### 1.5 顺手修的两处

- **二维码页面无法加载**：`lan_ip()` 重写（UDP 探测不像局域网 IP 时退 `SIOCGIFADDR` 枚举 +
  `ifconfig`/`ip` 兜底，排 127./169.254./100.64./198.18./198.19./0.）；`main()` 里 `load_state()`
  提到 `resolve_token()` 之前（令牌不再每次重启变）；`manifest.webmanifest` 的 `start_url`/`scope`
  改 `/`。
- **「不要输入访问令牌」**：LaunchAgent 加 `--no-token`（`mac-install.sh` 与线上 plist 都改了）。
  代价是同网段任何设备都能遥控；想收紧就删那行重启。

## 2. 关键文件与它们的「单一事实来源」

```
server.py                        # HTTP 层 + 路由 + INSTALL_SCRIPT + /app.apk
atv_backend.py                   # adb / pyatv 适配 + 降级占位（含平台化提示）
static/{index,app.js,style.css,sw.js,manifest.webmanifest}
android-native/app/src/main/python/boot.py         # Chaquopy 引导（路径由 Java 传入）
android-native/app/src/main/java/.../NativeActivity.java  # 解 adb-libs + 传路径
android-native/app/src/main/jniLibs/arm64-v8a/libadb.so   # adb 本体（必须 .so 结尾）
android-native/app/src/main/assets/adb-libs/              # 56 个依赖库
tools/fetch-adb-android.py       # 抓 adb + 传递闭包解析依赖（可只抓一个包）
tools/make-android-icons.py      # Android 五档前景（带对比度自查）
tools/make_icons.py              # PWA / iOS 四张
tools/emulator-test.sh           # 模拟器端到端验证（六步）
mac/make_icon.swift              # 主图：1024 全出血 + 自适应前景 432
mac/make-icns.sh                 # iconset -> AppIcon.icns
mac/make-desktop-shortcut.sh     # 桌面 ATV Remote.app（含 ad-hoc 重签）
VERSION                          # versionName / versionCode 唯一来源
```

## 3. 验证基线（改完必跑）

```bash
cd ~/atv-remote
python3 -c 'import server; import atv_backend'   # 语法 + 导入
node --check static/app.js && node --check static/sw.js
./check.sh                    # 一键全跑（455 用例 × 2 个解释器 + 副本比对）
./sync-native.sh              # 改了 server/atv_backend/static 之后必须同步内嵌副本
python3 -m unittest discover -s tests
```

APK 重建（chaquo.com 不通时的降级口径）：

```bash
cd android-native
./gradlew assembleRelease -PnoAppletv \
  -PpypiMirror=https://mirrors.cloud.tencent.com/pypi/simple
cp app/build/outputs/apk/release/app-release.apk ../ATVRemote-native.apk
```

模拟器验证（Mac 上测不到的部分）：

```bash
tools/emulator-test.sh            # 默认 test_avd；AVD 名作第一个参数
```

## 4. 本轮新增的测试（共 6 个文件，都带「为什么」注释）

| 文件 | 条数 | 盯什么 |
|---|---|---|
| `tests/test_download.py` | 9 | HEAD 不再 501、HEAD 有头无体、Content-Length 正确、GET 仍有体、Content-Disposition、SW 放行下载、`cache.put` 不吞响应、缓存版本 |
| `tests/test_icons.py` | 8 | favicon 顺序 / 无 data-URI / 文件存在、五档密度齐全、**对比度 ≥3:1**、与主图同源、生成脚本覆盖 |
| `tests/test_android_engine.py` | 10 | jniLibs 里 adb 是 `.so`、**传递闭包完整**、boot 接线、gradle 打包、`/app.apk` 路由优先级、内嵌标记、404 文案、成品 APK 内容 |
| `tests/test_install.py` | 3 | 安装命令/二维码/明文地址用**局域网 IP** 而非 `location.origin`；`/api/setup` 失败退回 origin |
| `tests/test_launchagent.py` | 10 | plist 带 `--no-token`、不写死令牌、`resolve_token` 语义、README 有收紧路径 |
| `tests/test_atv_fallback.py` | 5 | pyatv 缺失时**每个**占位方法都带可粘贴命令、按平台分流、安装脚本暴露真实原因、页面有补救入口 |

外加 `tests/install_harness.js`（node 假 DOM 跑安装引导段）。

## 5. 用户偏好 / 环境坑（务必沿用）

- **不要 `git commit`**（除非明确说「提交」）；不要 `git checkout` 覆盖改动；不要手工 `cp` 同步副本，用 `./sync-native.sh`。
- 改 `server.py` / `atv_backend.py` / `static/*` 后**必须** `./sync-native.sh`。
- `state.json` 与 `debug.keystore` 禁提交；`state.json` 不得进 `/bundle.tgz`（除非启用令牌）。
- 前端渲染设备名 / IP 一律 `textContent`，禁 `innerHTML`（数据来自局域网广播，可伪造）。
- HTTP 层是 keep-alive，所有响应走 `_send()`（它管 Content-Length 与 HEAD 语义）。
- **adb 调用很贵**：`devices()`/`version()` 走缓存，改变在线状态的操作必须 `adb.invalidate_devices()`。
- `exec` 工具只吃 **JavaScript 源码**（不是 JSON）；单次 30s 硬上限，长任务用 `tty:true` + 轮询。
- `rm -f` 被沙箱拒 → 用 Python `os.remove`；macOS 无 `cat -A`，用 `od -c` 或 Python repr。
- 写文件优先 `tools.apply_patch`（多文件用多个 `*** Update File`；`@@` 必须单独一行）。

## 6. 未提交的改动（33 改 + 若干新增，`git status` 61 项）

**改**：`server.py`(+235) `static/app.js`(+793) `static/index.html`(±373) `static/style.css`(+113)
`static/sw.js`(±32) `atv_backend.py`(±33) `make_icon.swift`(±175) `mac-install.sh` `sync-native.sh`
`VERSION` `android-native/app/build.gradle` `NativeActivity.java` `boot.py` 五张 mipmap PNG、
`static/` 四张图标 PNG + `icon.svg` + `manifest.webmanifest`，以及 `docs/*` 三个文档。

**新增**：`tools/` 四个脚本、`tests/` 六个测试 + 一个 harness、`mac/make-icns.sh`、
`mac/make-desktop-shortcut.sh`、`android-native/adb-bundle.txt`、
`android-native/app/src/main/{jniLibs,assets}/`（adb 与 56 个依赖库）。

**注意**：`VERSION` 已 bump 到 1.34.0/35；`tests/test_undo.py` 的版本断言同步更新。

## 7. 下一步候选（按优先级）

1. **真机验一次**：模拟器过了，但真机（尤其非 arm64 / 老系统）还没试。重点看
   `libadb.so` 能否 execve、`adb-libs` 能否加载。真机报错的话，App 横幅会打出
   `CANNOT LINK EXECUTABLE ...` 这类信息，直接发回来即可定位。
2. **bump 版本发版**：`VERSION` → 1.34.0 / 35，然后 `./check.sh` + APK 重建。
3. **提交**：本轮改动量大（33 文件），建议拆成几个提交（安装链路 / 图标 / 下载 / 令牌 / 文档），
   但用户没说要提交，**不要自作主张**。
4. **Apple TV 在手机 APK 里仍不可用**：协议依赖 pyatv → pydantic-core（Rust），Chaquopy 没有
   Android 预编译 wheel。想手机独立遥控 Apple TV 只能走 Termux 方案（页面里已折叠）。
5. 已记但未修的既有缺口：`sync-native.sh` 同步清单只有 6 个文件，`static/sw.js` 与 icon PNG
   不在内嵌副本里（APK 里 SW 注册会 404）—— 现在 `app.imy` 里其实**有** `static/sw.js` 与四张
   PNG（构建时 Copy 整个 static 目录），但 `sync-native.sh --check` 不比对它们，是校验盲区。

## 8. 交接时的实时状态快照

```
服务     : LaunchAgent com.atv.remote（--no-open --no-token），:8300
局域网   : http://192.168.1.34:8300（页面 200；/app.apk 200 / 20,261,447 字节）
/api/status: version 1.34.0 | embedded false | adb true | appletv available true
APK      : ATVRemote-native.apk 20,261,447 字节（9/28 22:39 重建，SHA256 b69ece…8482）
测试     : 455 通过（系统 11 skipped / .venv 6 skipped）；check.sh ✅；sync-native --check ✅
模拟器   : 已停（launchctl bootout gui/501/com.atv.emulator）
```
