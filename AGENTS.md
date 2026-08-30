# AGENTS.md

## 项目概述

ATV Remote — 跨平台本地遥控器，支持 Apple TV 和 Android TV。
Python 后端提供 HTTP API，Web 前端 + Mac 原生 App + Android 客户端多端接入。

## 启动命令

```bash
python3 server.py        # 主入口，启动 Web 服务
./start.sh               # 脚本封装
```

## 源码结构

| 路径 | 说明 |
|------|------|
| `server.py` | Web 服务器入口 |
| `atv_backend.py` | 设备控制后端（adb / pyatv 协议适配） |
| `static/` | Web 前端（index.html, app.js, style.css） |
| `android/` | Android WebView 客户端 |
| `android-native/` | Android 原生客户端（Chaquopy） |
| `mac/` | Mac 原生 App（Swift + WebKit） |

## 构建路径

- **Android APK**: `./android/build.sh`
- **Mac App**: `mac/main.swift`，使用 `swiftc` 编译

## 安全约束

- `debug.keystore` — Android 调试签名密钥，已在 .gitignore 中排除，禁止提交
- `state.json` — 包含 Apple TV 配对凭据，已在 .gitignore 中排除，禁止提交

## 验证基线

修改代码后，必须运行以下验证命令并确认全部通过（退出码 0）：

### Python 后端语法检查

```bash
python3 -c 'import server; import atv_backend'
```

- 覆盖文件：`server.py`、`atv_backend.py`
- 验证内容：语法正确性 + 模块级导入可用

### JavaScript 前端语法检查

```bash
node --check static/app.js
```

- 覆盖文件：`static/app.js`
- 验证内容：ES 语法正确性（Node.js 内置解析器，无需额外依赖）

### 一键运行全部验证

```bash
python3 -c 'import server; import atv_backend' && node --check static/app.js && echo "✅ ALL CHECKS PASSED"
```
