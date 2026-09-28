#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Android 原生 APK「装完就能用」的输入是否齐全。

动机：用户要求「手机不要装虚拟机，直接装 APK 就能用」。以前那个 APK 是个空壳——
`server.py` 的 Adb 类是 subprocess 调 adb 可执行文件的，而 App 沙箱里什么都没有，
所以装上只能看界面、连不上电视。现在 adb 与它依赖的几个库都随包分发，这里盯着别丢：

  1) jniLibs 里有以 .so 结尾的 adb 本体（AGP 只打包 *.so，名字不带后缀会被静默丢掉）；
  2) 它的 DT_NEEDED 里每个非系统库都能在 assets/adb-libs 找到（少一个就是启动即崩）；
  3) boot.py 把 ADB_PATH 指到 nativeLibraryDir 的那份，并解压依赖库、设 LD_LIBRARY_PATH；
  4) build.gradle 关掉了「不解压 native lib」的现代默认（不解压就没法 execve）；
  5) 如果仓库根目录已经有打好的 APK，它里面也得真有这些东西。

纯读文件 + 解析 ELF，不起进程、不联网。
"""

import struct
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NDK = ROOT / "android-native"
JNILIBS = NDK / "app/src/main/jniLibs/arm64-v8a"
ADB_LIBS = NDK / "app/src/main/assets/adb-libs"
BOOT = NDK / "app/src/main/python/boot.py"
GRADLE = NDK / "app/build.gradle"
APK = ROOT / "ATVRemote-native.apk"

# Android 系统自带，不用随包发
SYSTEM_LIBS = {"liblog.so", "libc.so", "libdl.so", "libm.so"}


def elf_needed(data):
    """解析 ELF64 LE 的 PT_DYNAMIC，返回 DT_NEEDED 列表。"""
    if data[:4] != b"\x7fELF":
        raise AssertionError("不是 ELF")
    machine = struct.unpack_from("<H", data, 18)[0]
    assert machine == 183, "必须是 AArch64(183)，现在是 %d" % machine
    e_phoff = struct.unpack_from("<Q", data, 32)[0]
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 54)
    dyn_off = dyn_sz = strtab = None
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type = struct.unpack_from("<I", data, off)[0]
        if p_type == 2:                      # PT_DYNAMIC
            dyn_off = struct.unpack_from("<Q", data, off + 8)[0]
            dyn_sz = struct.unpack_from("<Q", data, off + 32)[0]
    assert dyn_off, "没有 PT_DYNAMIC"
    # 先拿 DT_STRTAB 的 vaddr，再换算成文件偏移
    ents = []
    for o in range(dyn_off, dyn_off + dyn_sz, 16):
        tag, val = struct.unpack_from("<qQ", data, o)
        if tag == 0:
            break
        ents.append((tag, val))
    strtab_vaddr = next((v for t, v in ents if t == 5), None)
    assert strtab_vaddr, "没有 DT_STRTAB"
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if struct.unpack_from("<I", data, off)[0] != 1:
            continue
        p_off, p_vaddr, _pa, p_filesz = struct.unpack_from("<QQQQ", data, off + 8)
        if p_vaddr <= strtab_vaddr < p_vaddr + p_filesz:
            base = p_off + (strtab_vaddr - p_vaddr)
            break
    else:
        raise AssertionError("DT_STRTAB 不在任何 PT_LOAD 里")
    out = []
    for tag, val in ents:
        if tag == 1:                          # DT_NEEDED
            end = data.index(b"\x00", base + val)
            out.append(data[base + val:end].decode())
    return out


class AdbBundleTest(unittest.TestCase):
    """adb 本体 + 依赖库必须都在仓库里。"""

    def test_libadb_present_and_arm64(self):
        p = JNILIBS / "libadb.so"
        self.assertTrue(p.is_file(),
                        "缺 jniLibs/arm64-v8a/libadb.so——AGP 只打包 *.so，"
                        "直接叫 adb 会被静默丢掉，APK 就又变空壳了")
        self.assertGreater(p.stat().st_size, 1 << 20, "adb 至少 1MB，文件不对")
        elf_needed(p.read_bytes())            # 顺带校验 AArch64 ELF

    def test_every_needed_lib_bundled(self):
        needed = elf_needed((JNILIBS / "libadb.so").read_bytes())
        have = {p.name for p in ADB_LIBS.iterdir()}
        missing = [n for n in needed
                   if n not in SYSTEM_LIBS and n not in have]
        self.assertEqual(missing, [],
                         "adb 需要的库没随包发，启动时会找不到符号：" + str(missing))
        self.assertGreaterEqual(len(have), 8, "依赖库数量不对：" + str(sorted(have)))


    def test_transitive_closure_complete(self):
        """传递依赖也必须齐。

        模拟器实测踩过：adb 自己只要 8 个库，但 libprotobuf 还要 libabsl_*、
        libbrotlicommon、libutf8_*。只按 adb 的 DT_NEEDED 收，adb 能启动，
        libprotobuf 一加载就 CANNOT LINK EXECUTABLE。
        """
        have = {p.name: p for p in ADB_LIBS.iterdir()}
        seen, frontier, missing = set(), [(JNILIBS / "libadb.so").read_bytes()], set()
        while frontier:
            for name in elf_needed(frontier.pop()):
                if name in SYSTEM_LIBS or name in seen:
                    continue
                seen.add(name)
                p = have.get(name)
                if p is None:
                    missing.add(name)
                    continue
                frontier.append(p.read_bytes())
        self.assertEqual(sorted(missing), [],
                         "传递依赖缺库：adb 能启动，但加载到一半会 "
                         "CANNOT LINK EXECUTABLE：" + str(sorted(missing)))
        self.assertGreaterEqual(len(seen), 20,
                                "闭包太小，说明有文件没扫到：" + str(sorted(seen)))


class BootWiringTest(unittest.TestCase):
    """boot.py 要把 ADB_PATH 指到 nativeLibraryDir 的 libadb.so。"""

    @classmethod
    def setUpClass(cls):
        cls.boot = BOOT.read_text(encoding="utf-8")

    def test_points_at_libadb(self):
        self.assertIn("libadb.so", self.boot)
        self.assertIn("nativeLibraryDir", self.boot)
        # Android 10+ App 私有目录 noexec：不能再把 adb 解到 files/ 再 exec
        self.assertNotIn('files + "/adb"', self.boot)

    def test_extracts_libs_and_sets_ld_path(self):
        self.assertIn("adb-libs", self.boot)
        self.assertIn("LD_LIBRARY_PATH", self.boot)
        self.assertIn("ADB_PATH", self.boot)


class GradlePackagingTest(unittest.TestCase):
    """native lib 必须解压到磁盘，否则 execve 不了。"""

    def test_legacy_packaging(self):
        g = GRADLE.read_text(encoding="utf-8")
        self.assertIn("useLegacyPackaging", g)
        self.assertIn("useLegacyPackaging=true", g.replace(" ", ""))


class BuiltApkTest(unittest.TestCase):
    """已经打好的 APK 也要真的带上这些东西。"""

    def test_apk_contents(self):
        if not APK.is_file():
            self.skipTest("仓库里还没有 ATVRemote-native.apk")

    def test_app_apk_route_prefers_native(self):
        # 网页「下载 APK」链接触发 /app.apk。它必须发原生版——否则用户下到的
        # 是 WebView 壳（几十 KB，离开 Mac 什么都干不了）
        src = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('if path == "/app.apk":', src)
        i = src.index('if path == "/app.apk":')
        block = src[i:i + 600]
        self.assertIn("ATVRemote-native.apk", block)
        self.assertLess(block.index("ATVRemote-native.apk"),
                        block.index('ROOT / "android" / "ATVRemote.apk"'),
                        "原生版要排在 WebView 壳前面")
        z = zipfile.ZipFile(str(APK))
        names = z.namelist()
        self.assertIn("lib/arm64-v8a/libadb.so", names)
        libs = [n for n in names if n.startswith("assets/adb-libs/")]
        self.assertGreaterEqual(len(libs), 8, "APK 里缺 adb 依赖库")
        # 内嵌的前后端也得是新的（web 界面 / sw.js / 图标）
        imy = [n for n in names if n.endswith("app.imy")]
        self.assertTrue(imy, "APK 里没有 app.imy")

    def test_embedded_hides_apk_card(self):
        """手机 App 内嵌引擎里，「把遥控器装到手机」那张卡片必须隐藏。

        模拟器实测：App 内的服务收到 /app.apk 会 404（APK 不会把自己装进自己），
        而页面照旧显示下载链接 → 用户点下去就是「APK 不存在」。
        """
        boot = (NDK / "app/src/main/python/boot.py").read_text(encoding="utf-8")
        self.assertIn('os.environ["ATV_EMBEDDED"] = "1"', boot,
                      "boot.py 要标记内嵌运行，否则页面无从判断")
        srv = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("ATV_EMBEDDED")', srv)
        self.assertIn('"embedded": EMBEDDED', srv, "/api/status 要暴露这个标记")
        js = (ROOT / "static/app.js").read_text(encoding="utf-8")
        self.assertIn("s.embedded", js)
        self.assertIn("phoneInstall", js)

    def test_apk_404_message_is_human(self):
        """404 文案不能只说「不存在」——手机上看不懂该做什么。"""
        srv = (ROOT / "server.py").read_text(encoding="utf-8")
        i = srv.index('if path == "/app.apk":')
        block = srv[i:i + 1200]
        self.assertIn("EMBEDDED", block)
        self.assertIn("手机 App 里没有这个文件", block)


if __name__ == "__main__":
    unittest.main()
