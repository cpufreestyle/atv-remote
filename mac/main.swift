import Cocoa
import WebKit

// ATV Remote — Mac 原生窗口壳（WKWebView 加载本地遥控服务）
final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    var retryTimer: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupMenu()
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 880),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        window.center()
        window.title = "📺 ATV Remote"
        window.minSize = NSSize(width: 380, height: 620)
        window.backgroundColor = NSColor(srgbRed: 0.05, green: 0.06, blue: 0.08, alpha: 1)

        let config = WKWebViewConfiguration()
        webView = WKWebView(frame: NSRect(x: 0, y: 0, width: 460, height: 880), configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.underPageBackgroundColor = NSColor(srgbRed: 0.05, green: 0.06, blue: 0.08, alpha: 1)
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        window.orderFrontRegardless()
        NSApp.activate(ignoringOtherApps: true)
        load()
        StatusBarController().attach(app: self)
    }

    /// 无菜单栏的裸 App 在部分场景下无法正确前置窗口，必须建基础菜单
    func setupMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 ATV Remote",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "退出 ATV Remote",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenuItem.submenu = appMenu

        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editMenuItem.submenu = editMenu
        NSApp.mainMenu = mainMenu
    }

    func load() {
        webView.load(URLRequest(url: URL(string: "http://127.0.0.1:8300")!))
    }

    // 服务未就绪时每 2 秒自动重试（LaunchAgent 常驻时秒开）
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        retryTimer?.invalidate()
        retryTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: false) { [weak self] _ in
            self?.load()
        }
    }

    @objc func showMainWindow() {
        NSApp.activate(ignoringOtherApps: true)
        window.makeKeyAndOrderFront(nil)
    }
}

// 菜单栏遥控器（C1）：不打开主窗口完成日常操作 —— 音量 / 播放控制 / D-pad / 电源。
// 全部复用本地 HTTP API，不重写任何协议；macOS 上走 127.0.0.1，天然免令牌。
final class StatusBarController: NSObject, NSPopoverDelegate {
    private var statusItem: NSStatusItem!
    private var popover: NSPopover!
    private weak var appDelegate: AppDelegate?
    private var statusLabel: NSTextField!
    private let base = "http://127.0.0.1:8300"

    // 与 static/app.js 的 KEYMAP 对齐（Android 键码；Apple TV 在服务端映射）
    private static let codes: [(String, Int)] = [
        ("⏴", 21), ("⏵", 22), ("⏶", 19), ("⏷", 20), ("OK", 23),
        ("⏮", 88), ("⏯", 85), ("⏭", 87),
        ("🔊", 24), ("🔉", 25), ("🔇", 164),
        ("⌂", 3), ("⏎", 4), ("⎋", 111), ("⏻", 26),
    ]
    private static let layout = [2, 5, 3, 4]   // 每行按钮数：D-pad / 播放 / 音量 / 系统

    func attach(app: AppDelegate) {
        appDelegate = app
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "tv",
                                   accessibilityDescription: "ATV Remote")
            button.action = #selector(togglePopover(_:))
            button.target = self
        }
        popover = NSPopover()
        popover.behavior = .transient
        popover.delegate = self
        popover.contentViewController = buildViewController()
        refreshStatus()
    }

    @objc private func togglePopover(_ sender: Any?) {
        if popover.isShown {
            popover.performClose(sender)
            return
        }
        guard let button = statusItem.button else { return }
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        refreshStatus()
    }

    private func buildViewController() -> NSViewController {
        let root = NSStackView()
        root.orientation = .vertical
        root.alignment = .centerX
        root.spacing = 10
        root.edgeInsets = NSEdgeInsets(top: 12, left: 12, bottom: 12, right: 12)

        statusLabel = NSTextField(labelWithString: "连接中…")
        statusLabel.font = NSFont.systemFont(ofSize: 11)
        statusLabel.textColor = .secondaryLabelColor
        root.addArrangedSubview(statusLabel)

        var index = 0
        for count in StatusBarController.layout {
            let row = NSStackView()
            row.orientation = .horizontal
            row.spacing = 8
            for _ in 0..<count {
                let (title, code) = StatusBarController.codes[index]
                let b = makeButton(title)
                b.action = #selector(sendKey(_:))
                b.target = self
                b.tag = code   // NSControl.tag 带键码，比 representedObject 省一个子类
                row.addArrangedSubview(b)
                index += 1
            }
            root.addArrangedSubview(row)
        }

        let spacer = NSView()
        spacer.translatesAutoresizingMaskIntoConstraints = false
        spacer.heightAnchor.constraint(equalToConstant: 4).isActive = true
        root.addArrangedSubview(spacer)

        let win = NSButton(title: "打开遥控器窗口", target: self,
                           action: #selector(openWindow(_:)))
        root.addArrangedSubview(win)

        let vc = NSViewController()
        vc.view = root
        return vc
    }

    private func makeButton(_ title: String) -> NSButton {
        let b = NSButton(title: title, target: nil, action: nil)
        b.bezelStyle = .rounded
        b.font = NSFont.systemFont(ofSize: 14)
        b.translatesAutoresizingMaskIntoConstraints = false
        b.widthAnchor.constraint(equalToConstant: 48).isActive = true
        b.heightAnchor.constraint(equalToConstant: 32).isActive = true
        return b
    }

    @objc private func sendKey(_ sender: NSButton) {
        send(["type": "key", "code": sender.tag])
    }

    @objc private func openWindow(_ sender: Any?) {
        appDelegate?.showMainWindow()
    }

    private func send(_ body: [String: Any]) {
        guard let url = URL(string: base + "/api/cmd"),
              let data = try? JSONSerialization.data(withJSONObject: body) else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = data
        URLSession.shared.dataTask(with: req).resume()
    }

    /// popover 打开时拉一次状态，让用户一眼看到「连的是哪台电视 / 服务起没起」
    private func refreshStatus() {
        guard let url = URL(string: base + "/api/status") else { return }
        URLSession.shared.dataTask(with: url) { [weak self] data, _, _ in
            guard let self = self, let data = data,
                  let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { return }
            let name = (j["android"] as? [String: Any])?["name"] as? String
                ?? (j["appletv"] as? [String: Any])?["name"] as? String
            let text = name ?? "未连接电视"
            DispatchQueue.main.async { self.statusLabel.stringValue = text }
        }.resume()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
