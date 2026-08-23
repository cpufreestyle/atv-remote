import AppKit

// ATV Remote 图标生成：macOS 主图标(1024) + Android 自适应图标前景层(432)
// 设计：深色圆角底 + 蓝色电视(播放) + D-pad 遥控徽章，对应 App 暗色主题

let BLUE = NSColor(srgbRed: 0.31, green: 0.55, blue: 1.0, alpha: 1)        // #4f8cff
let BLUE_DEEP = NSColor(srgbRed: 0.18, green: 0.42, blue: 0.88, alpha: 1)  // #2f6ae0
let BG_TOP = NSColor(srgbRed: 0.17, green: 0.196, blue: 0.26, alpha: 1)    // #2b3242
let BG_BOT = NSColor(srgbRed: 0.066, green: 0.078, blue: 0.125, alpha: 1)  // #111420

func roundRect(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, _ r: CGFloat) -> NSBezierPath {
    return NSBezierPath(roundedRect: NSRect(x: x, y: y, width: w, height: h), xRadius: r, yRadius: r)
}

func triangle(_ p1: CGPoint, _ p2: CGPoint, _ p3: CGPoint) -> NSBezierPath {
    let p = NSBezierPath()
    p.move(to: p1); p.line(to: p2); p.line(to: p3); p.close()
    return p
}

/// 在指定尺寸画主题元素（原点左下角；scale=1 时按 1024 布局）
func drawMotif(scale s: CGFloat, badgeScale bs: CGFloat = 1.0) {
    // —— 电视 ——
    let tvX = 232 * s, tvY = 430 * s, tvW = 560 * s, tvH = 380 * s
    // 机身
    NSColor(srgbRed: 0.04, green: 0.05, blue: 0.07, alpha: 1).setFill()
    roundRect(tvX, tvY, tvW, tvH, 44 * s).fill()
    // 屏幕蓝色渐变 + 白色播放三角
    let inset = 26 * s
    let scr = roundRect(tvX + inset, tvY + inset, tvW - inset * 2, tvH - inset * 2 - 10 * s, 30 * s)
    NSGradient(starting: BLUE, ending: BLUE_DEEP)!.draw(in: scr, angle: -90)
    // 白色描边(机身)
    BLUE.withAlphaComponent(0.9).setStroke()
    let body = roundRect(tvX, tvY, tvW, tvH, 44 * s)
    body.lineWidth = 10 * s
    body.stroke()
    // 播放三角
    let cx = tvX + tvW / 2 - 24 * s, cy = tvY + (tvH - 10 * s) / 2
    NSColor.white.setFill()
    triangle(CGPoint(x: cx - 34 * s, y: cy + 52 * s),
             CGPoint(x: cx - 34 * s, y: cy - 52 * s),
             CGPoint(x: cx + 60 * s, y: cy)).fill()
    // 底座
    NSColor(srgbRed: 0.29, green: 0.33, blue: 0.42, alpha: 1).setFill()
    roundRect(tvX + tvW / 2 - 52 * s, tvY - 42 * s, 104 * s, 40 * s, 10 * s).fill()
    roundRect(tvX + tvW / 2 - 118 * s, tvY - 78 * s, 236 * s, 30 * s, 12 * s).fill()

    // —— D-pad 遥控徽章（压在电视右下角）——
    let bR = 176 * s * bs
    let bCx = tvX + tvW - 22 * s, bCy = tvY - 8 * s
    // 外圈
    NSColor(srgbRed: 0.09, green: 0.10, blue: 0.15, alpha: 1).setFill()
    NSBezierPath(ovalIn: NSRect(x: bCx - bR, y: bCy - bR, width: bR * 2, height: bR * 2)).fill()
    BLUE.setStroke()
    let ring = NSBezierPath(ovalIn: NSRect(x: bCx - bR, y: bCy - bR, width: bR * 2, height: bR * 2))
    ring.lineWidth = 9 * s
    ring.stroke()
    // 四向箭头
    NSColor.white.setFill()
    let d = 86 * s * bs, a = 30 * s * bs   // 距中心 / 箭头半径
    triangle(CGPoint(x: bCx - a, y: bCy + d - a), CGPoint(x: bCx + a, y: bCy + d - a),
             CGPoint(x: bCx, y: bCy + d + a)).fill()                                    // 上
    triangle(CGPoint(x: bCx - a, y: bCy - d + a), CGPoint(x: bCx + a, y: bCy - d + a),
             CGPoint(x: bCx, y: bCy - d - a)).fill()                                    // 下
    triangle(CGPoint(x: bCx - d + a, y: bCy - a), CGPoint(x: bCx - d + a, y: bCy + a),
             CGPoint(x: bCx - d - a, y: bCy)).fill()                                    // 左
    triangle(CGPoint(x: bCx + d - a, y: bCy - a), CGPoint(x: bCx + d - a, y: bCy + a),
             CGPoint(x: bCx + d + a, y: bCy)).fill()                                    // 右
    // 中心 OK 圆
    NSGradient(colors: [BLUE, BLUE_DEEP])!.draw(in: NSBezierPath(ovalIn:
        NSRect(x: bCx - 44 * s * bs, y: bCy - 44 * s * bs, width: 88 * s * bs, height: 88 * s * bs)), angle: -90)
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
    print("已生成 \(path) (\(px)px)")
}

let out = "/Users/a1-6/atv-remote/mac"

// —— 1) macOS 图标 1024：圆角底 + 主题 ——
let mac = NSImage(size: NSSize(width: 1024, height: 1024))
mac.lockFocus()
let bg = roundRect(20, 20, 984, 984, 232)
NSGradient(starting: BG_TOP, ending: BG_BOT)!.draw(in: bg, angle: -90)
NSColor(srgbRed: 0.29, green: 0.32, blue: 0.40, alpha: 0.55).setStroke()
let border = roundRect(26, 26, 972, 972, 226)
border.lineWidth = 5
border.stroke()
drawMotif(scale: 1.0, badgeScale: 1.0)
mac.unlockFocus()
savePng(mac, out + "/AppIcon_1024.png", 1024)

// —— 2) Android 前景层 432（透明底，内容在中心 66% 安全区）——
let fg = NSImage(size: NSSize(width: 432, height: 432))
fg.lockFocus()
NSGraphicsContext.current?.saveGraphicsState()
let safe = NSBezierPath(ovalIn: NSRect(x: 60, y: 60, width: 312, height: 312))
safe.setClip()  // 只在安全圆内画，防裁切出怪边
// 按比例：把 1024 布局中的主题（约 x212..1010, y300..870 → 中心 (611,585)）映射到 432 中心
let s: CGFloat = 0.36
NSGraphicsContext.current?.cgContext.translateBy(x: 216 - 611 * s, y: 216 - 585 * s)
NSGraphicsContext.current?.cgContext.scaleBy(x: s, y: s)
drawMotif(scale: 1.0, badgeScale: 0.92)
NSGraphicsContext.current?.restoreGraphicsState()
fg.unlockFocus()
savePng(fg, out + "/ic_launcher_fg_432.png", 432)
