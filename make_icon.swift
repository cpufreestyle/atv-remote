import AppKit

// ATV Remote 图标生成（Apple 风格 / macOS 11+ 规范）
//
// 设计约定（与 tools/make_icons.py 的 PWA 图标、static/icon.svg 同一套视觉）：
//   1) 1024 全出血，不自己画圆角、不加透明边 —— 系统会套连续圆角（squircle）遮罩，
//      自己画圆角会双重遮罩、接缝处发虚；
//   2) 品牌蓝对角渐变底（#2f7eff → #0047c4）+ 左上径向高光 + 底部轻压暗，
//      这是 macOS 图标「有厚度的实物感」的来源；
//   3) 白色字形（电视屏 + 播放键 + 支架）带柔和投影，屏内上方加品牌蓝信号弧
//      （圆点 + 两弧），小到 16px 仍认得出。
//
// 用法：swift make_icon.swift [输出目录]，默认 ./mac

let OUT_ARG = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : nil
let SIZE: CGFloat = 1024

// —— 配色（static/style.css 的 --accent #006afd 一族）——
let BLUE_TOP = NSColor(srgbRed: 0.184, green: 0.494, blue: 1.000, alpha: 1)   // #2f7eff
let BLUE_BOT = NSColor(srgbRed: 0.000, green: 0.278, blue: 0.769, alpha: 1)   // #0047c4
let SCREEN   = NSColor(srgbRed: 0.961, green: 0.973, blue: 0.988, alpha: 1)   // #f5f8fc
let TRIANGLE = NSColor(srgbRed: 0.024, green: 0.310, blue: 0.855, alpha: 1)   // #064fda

func roundRect(_ r: CGRect, _ radius: CGFloat) -> NSBezierPath {
    return NSBezierPath(roundedRect: r, xRadius: radius, yRadius: radius)
}

func triangle(_ p1: CGPoint, _ p2: CGPoint, _ p3: CGPoint) -> NSBezierPath {
    let p = NSBezierPath()
    p.move(to: p1); p.line(to: p2); p.line(to: p3); p.close()
    return p
}

/// 全出血渐变底：对角渐变 + 左上高光 + 底部轻压暗（1024 坐标系，y 向上）
func drawBackground(_ ctx: CGContext, size: CGFloat) {
    let space = CGColorSpaceCreateDeviceRGB()
    let layers = [BLUE_TOP.cgColor, BLUE_BOT.cgColor] as CFArray
    let grad = CGGradient(colorsSpace: space, colors: layers, locations: [0, 1])!
    ctx.drawLinearGradient(grad,
                           start: CGPoint(x: 0, y: size),
                           end: CGPoint(x: size, y: 0),
                           options: [])
    // 左上径向高光：给瓷面一点「光打上来」的通透感
    let glowColors = [NSColor.white.withAlphaComponent(0.22).cgColor,
                      NSColor.white.withAlphaComponent(0.0).cgColor] as CFArray
    let glow = CGGradient(colorsSpace: space, colors: glowColors, locations: [0, 1])!
    let gc = CGPoint(x: size * 0.28, y: size * 0.76)
    ctx.drawRadialGradient(glow, startCenter: gc, startRadius: 0,
                           endCenter: gc, endRadius: size * 0.70,
                           options: .drawsBeforeStartLocation)
    // 底部压暗：让字形站得住，也避免整块纯平
    let vigColors = [NSColor.black.withAlphaComponent(0.0).cgColor,
                     NSColor.black.withAlphaComponent(0.16).cgColor] as CFArray
    let vig = CGGradient(colorsSpace: space, colors: vigColors, locations: [0, 1])!
    ctx.drawLinearGradient(vig, start: CGPoint(x: 0, y: size * 0.34),
                           end: CGPoint(x: 0, y: 0), options: [])
}

/// 主题字形：白色电视屏（含播放键）+ 支架。s 为缩放系数，1024 布局下传 1。
func drawMotif(scale s: CGFloat) {
    let scr = CGRect(x: 196 * s, y: 356 * s, width: 632 * s, height: 404 * s)
    // 柔和投影：Apple 图标「浮在瓷面上」的关键一笔
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.30)
    shadow.shadowBlurRadius = 26 * s
    shadow.shadowOffset = NSSize(width: 0, height: -14 * s)
    shadow.set()
    // 屏
    SCREEN.setFill()
    roundRect(scr, 76 * s).fill()
    NSShadow().set()   // 后面的支架/三角不要重复投影
    // 播放键（品牌蓝，等比嵌在屏里）
    let cx = scr.midX + 10 * s
    let cy = scr.midY
    let tw: CGFloat = 132 * s
    let th: CGFloat = 166 * s
    TRIANGLE.setFill()
    triangle(CGPoint(x: cx - tw / 2, y: cy + th / 2),
             CGPoint(x: cx - tw / 2, y: cy - th / 2),
             CGPoint(x: cx + tw / 2, y: cy)).fill()
    // 信号弧（圆点 + 两弧）：与播放键同色的品牌蓝，收在屏内上方
    let sig = CGPoint(x: scr.midX, y: 1024 * s - 372 * s)
    TRIANGLE.setFill()
    NSBezierPath(ovalIn: CGRect(x: sig.x - 11 * s, y: sig.y - 11 * s,
                                width: 22 * s, height: 22 * s)).fill()
    for (r, alpha) in [(CGFloat(76), 1.0), (CGFloat(42), 0.55)] {
        let ctx = NSGraphicsContext.current!.cgContext
        ctx.saveGState()
        ctx.setLineWidth(15 * s)
        ctx.setLineCap(.round)
        ctx.setStrokeColor(TRIANGLE.withAlphaComponent(alpha).cgColor)
        ctx.addArc(center: sig, radius: r * s,
                   startAngle: CGFloat(27) * .pi / 180,
                   endAngle: CGFloat(153) * .pi / 180, clockwise: false)
        ctx.strokePath()
        ctx.restoreGState()
    }
    // 支架（颈 + 脚）
    let neckH: CGFloat = 46 * s
    SCREEN.setFill()
    roundRect(CGRect(x: scr.midX - 46 * s, y: scr.minY - neckH,
                     width: 92 * s, height: neckH), 8 * s).fill()
    roundRect(CGRect(x: scr.midX - 134 * s, y: scr.minY - neckH - 34 * s,
                     width: 268 * s, height: 34 * s), 17 * s).fill()
}

func savePng(_ image: NSImage, _ path: String, _ px: CGFloat) {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(px), pixelsHigh: Int(px),
                               bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: px, height: px)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    image.draw(in: NSRect(x: 0, y: 0, width: px, height: px))
    NSGraphicsContext.restoreGraphicsState()
    let data = rep.representation(using: .png, properties: [:])!
    try! data.write(to: URL(fileURLWithPath: path))
    print("wrote " + path)
}

let out = OUT_ARG ?? ((FileManager.default.currentDirectoryPath as NSString)
                      .appendingPathComponent("mac"))

// —— 1) macOS 主图标：1024 全出血 ——
let mac = NSImage(size: NSSize(width: SIZE, height: SIZE))
mac.lockFocus()
let ctx = NSGraphicsContext.current!.cgContext
drawBackground(ctx, size: SIZE)
drawMotif(scale: 1.0)
mac.unlockFocus()
savePng(mac, out + "/AppIcon_1024.png", SIZE)

// —— 2) Android 自适应图标前景层 432（透明底，内容收在 66% 安全区内）——
let FG: CGFloat = 432
let fg = NSImage(size: NSSize(width: FG, height: FG))
fg.lockFocus()
NSGraphicsContext.current?.saveGraphicsState()
let safe = NSBezierPath(ovalIn: NSRect(x: FG * 0.17, y: FG * 0.17, width: FG * 0.66, height: FG * 0.66))
safe.setClip()   // 裁掉安全圆外的东西，防自适应遮罩切出怪边
let s: CGFloat = 0.39
// 1024 布局里字形的包围盒中心约 (512, 517)，映射到 432 的画布中心 (216, 216)
NSGraphicsContext.current?.cgContext.translateBy(x: 216 - 512 * s, y: 216 - 517 * s)
NSGraphicsContext.current?.cgContext.scaleBy(x: s, y: s)
drawMotif(scale: 1.0)
NSGraphicsContext.current?.restoreGraphicsState()
fg.unlockFocus()
savePng(fg, out + "/ic_launcher_fg_432.png", FG)
