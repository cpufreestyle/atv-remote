#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 Android 原生 APK 抓一份「能直接在手机上跑的 adb」+ 它要的所有库。

为什么需要：`server.py` 的 Adb 类是 subprocess 调 **adb 可执行文件** 控制 Android TV 的，
而 Android App 沙箱里本来什么都没有，所以旧 APK 装上是空壳（提示「adb 未安装」）。

adb 官方只发 macOS/Linux/Windows 客户端，没有 Android 版；Termux 的 android-tools 包里
有为 aarch64 Android 编好的 adb，连带它依赖的几个库。这份脚本把它们抠出来：

- adb 本体放 android-native/app/src/main/jniLibs/arm64-v8a/libadb.so（装机时由
  PackageManager 解到 nativeLibraryDir，那是 Android 上少数允许 execve 的目录；名字必须
  以 .so 结尾，否则 AGP 的 jniLibs 打包会把它静默漏掉）；
- 依赖库放 android-native/app/src/main/assets/adb-libs/（文件名字必须原样保留，
  例如 libz.so.1 这种带版本号的不能改名，所以不能走 jniLibs），运行时由 NativeActivity
  解到 App 私有目录，再用 LD_LIBRARY_PATH 指过去。

依赖是**传递**的：libprotobuf 还要 libabsl_die_if_null.so，所以脚本会把已抓到的 ELF
全部扫一遍 DT_NEEDED，缺谁就按包名补抓，直到收敛（最多 3 轮）。

用法：
    python3 tools/fetch-adb-android.py              # 全量
    python3 tools/fetch-adb-android.py abseil-cpp   # 只补一个包
日志清单写在 android-native/adb-bundle.txt。
"""

import hashlib
import io
import os
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "android-native/app/src/main/jniLibs/arm64-v8a"
LIBS = ROOT / "android-native/app/src/main/assets/adb-libs"
BASE = "https://packages.termux.dev/apt/termux-main"
INDEX = BASE + "/dists/stable/main/binary-aarch64/Packages"
_LOCAL_INDEX = os.environ.get("ATV_TERMUX_INDEX")

# 包 -> 从里面取哪些文件。adb 来自 android-tools，其余是它 DT_NEEDED 的库
WANT = {
    "android-tools": ["bin/adb"],
    "libc++": ["lib/libc++_shared.so"],
    # libprotobuf 包里还带着 libutf8_range / libutf8_validity（它 Replaces 了
    # libutf8-range），brotli 包里带着 libbrotlicommon，所以这两个整包收
    "libprotobuf": "ALL_LIBS",
    "brotli": "ALL_LIBS",
    "zlib": ["lib/libz.so.1"],
    "fmt": ["lib/libfmt.so"],
    "liblz4": ["lib/liblz4.so"],
    "zstd": ["lib/libzstd.so.1"],
    # libprotobuf 还要 abseil；这个包 .so 又多又小，整包收进来省事
    "abseil-cpp": "ALL_LIBS",
}

# Android 系统自带，不用随包发
SYSTEM_LIBS = {"liblog.so", "libc.so", "libdl.so", "libm.so",
               "libc++_shared.so"}   # 最后那个自己收，见 WANT
SYSTEM_LIBS.discard("libc++_shared.so")


def fetch(url, timeout=60):
    """下载。用 curl：Termux 的索引 1.8MB，urllib 直连偶尔被截断，curl 带重试更稳。"""
    p = subprocess.run(["curl", "-fsSL", "--retry", "4", "--retry-delay", "2",
                        "-m", "180", url], capture_output=True)
    if p.returncode != 0:
        raise SystemExit("下载失败 {}: {}".format(
            url, p.stderr.decode("utf-8", "replace").strip()))
    return p.stdout


def parse_packages(text):
    pkgs, cur = {}, None
    for line in text.splitlines():
        if line.startswith("Package: "):
            cur = line[9:].strip()
            pkgs[cur] = {}
        elif cur and ": " in line:
            k, v = line.split(": ", 1)
            pkgs[cur][k.strip()] = v.strip()
    return pkgs


def elf_needed(data):
    """ELF64 LE 的 DT_NEEDED。"""
    if data[:4] != b"\x7fELF":
        return []
    e_phoff = struct.unpack_from("<Q", data, 32)[0]
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 54)
    dyn_off = dyn_sz = None
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if struct.unpack_from("<I", data, off)[0] != 2:
            continue
        dyn_off = struct.unpack_from("<Q", data, off + 8)[0]
        dyn_sz = struct.unpack_from("<Q", data, off + 32)[0]
        break
    if not dyn_off:
        return []
    ents = []
    for o in range(dyn_off, dyn_off + dyn_sz, 16):
        tag, val = struct.unpack_from("<qQ", data, o)
        if tag == 0:
            break
        ents.append((tag, val))
    strtab_vaddr = next((v for t, v in ents if t == 5), None)
    if not strtab_vaddr:
        return []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if struct.unpack_from("<I", data, off)[0] != 1:
            continue
        p_off, p_vaddr, _pa, p_filesz = struct.unpack_from("<QQQQ", data, off + 8)
        if p_vaddr <= strtab_vaddr < p_vaddr + p_filesz:
            base = p_off + (strtab_vaddr - p_vaddr)
            break
    else:
        return []
    out = []
    for tag, val in ents:
        if tag == 1:
            end = data.index(b"\x00", base + val)
            out.append(data[base + val:end].decode())
    return out


def deb_members(data):
    """.deb 是 ar 归档；只取 data.tar.*。返回 (成员名, 字节)。"""
    f = io.BytesIO(data)
    if f.read(8) != b"!<arch>\n":
        raise SystemExit("不是 ar 归档")
    out = {}
    while True:
        hdr = f.read(60)
        if len(hdr) < 60:
            break
        size = int(hdr[48:58].decode().strip())
        name = hdr[0:16].decode().strip().rstrip("/")
        out[name] = f.read(size)
        if size % 2:
            f.read(1)
    for k, v in out.items():
        if k.startswith("data.tar"):
            return k, v
    raise SystemExit("deb 里没有 data.tar.*：" + ",".join(out))


def open_deb(meta):
    name, raw = deb_members(fetch(BASE + "/" + meta["Filename"]))
    mode = "r:xz" if name.endswith(".xz") else "r:gz"
    return tarfile.open(fileobj=io.BytesIO(raw), mode=mode)


def copy_from(tf, want, pkg):
    """按 want（文件列表或 ALL_LIBS）从已打开的 deb 里拷文件。返回新文件数。"""
    names = tf.getnames()
    got = 0
    if want == "ALL_LIBS":
        cands = [n for n in names if "/lib/" in n and n.endswith(".so")]
    else:
        cands = []
        for w in want:
            cands += [n for n in names
                      if n == "./data/data/com.termux/files/usr/" + w
                      or n.endswith("/usr/" + w)]
    for n in cands:
        dst = LIBS / Path(n).name
        data = tf.extractfile(tf.getmember(n)).read()
        if dst.is_file() and dst.read_bytes() == data:
            continue
        dst.write_bytes(data)
        got += 1
    if want != "ALL_LIBS":
        # adb 本体归 jniLibs，其余库归 assets/adb-libs
        adb = OUT / "libadb.so"
        if pkg == "android-tools":
            OUT.mkdir(parents=True, exist_ok=True)
            adb.write_bytes(tf.extractfile(tf.getmember(
                [n for n in cands if n.endswith("/bin/adb")][0])).read())
            got += 1
    return got


def scan_missing():
    """扫已抓到的 ELF，返回缺失的 DT_NEEDED 库名集合。"""
    have = {p.name for p in LIBS.iterdir()} if LIBS.is_dir() else set()
    need = set()
    for p in list(LIBS.iterdir()) if LIBS.is_dir() else []:
        need |= set(elf_needed(p.read_bytes()))
    need |= set(elf_needed((OUT / "libadb.so").read_bytes()))
    return {n for n in need if n not in SYSTEM_LIBS and n not in have}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    want = {only: WANT[only]} if only else dict(WANT)
    OUT.mkdir(parents=True, exist_ok=True)
    LIBS.mkdir(parents=True, exist_ok=True)
    if _LOCAL_INDEX:
        print("用本地索引 " + _LOCAL_INDEX)
        raw = io.open(_LOCAL_INDEX, encoding="utf-8").read()
    else:
        print("拉 Termux 包索引 …")
        raw = fetch(INDEX).decode("utf-8", "replace")
    pkgs = parse_packages(raw)

    fetched = set()
    for pkg, files in want.items():
        meta = pkgs.get(pkg)
        if not meta:
            print("  !! 索引里没有包 " + pkg + "，跳过")
            continue
        print("  {} {} → {}".format(pkg, meta["Version"],
                                   meta["Filename"].rsplit("/", 1)[-1]))
        got = copy_from(open_deb(meta), files, pkg)
        fetched.add(pkg)
        if got:
            print("     + {} 个文件".format(got))

    # 传递依赖：扫一遍 DT_NEEDED，缺的库去索引里找是哪个包提供的
    for _ in range(3):
        missing = scan_missing()
        if not missing:
            break
        print("  还缺：" + " ".join(sorted(missing)))
        added = False
        for name in sorted(missing):
            for pkg, meta in pkgs.items():
                if pkg in fetched or "lib" not in meta.get("Filename", ""):
                    continue
                if not meta["Filename"].endswith("_aarch64.deb"):
                    continue
                try:
                    tf = open_deb(meta)
                except SystemExit:
                    continue
                hit = [n for n in tf.getnames()
                       if n.endswith("/lib/" + name)]
                if not hit:
                    continue
                print("  {} 提供 {}，抓".format(pkg, name))
                copy_from(tf, "ALL_LIBS", pkg)
                fetched.add(pkg)
                added = True
                break
            if added:
                break   # 重新扫一遍，避免一次改太多
        if not added:
            print("  ⚠️ 索引里找不到这些库的提供者：" + " ".join(sorted(missing)))
            break

    left = scan_missing()
    if left:
        print("⚠️ 仍缺：" + " ".join(sorted(left)))
    else:
        print("✅ DT_NEEDED 全部满足")
    lines = ["# adb for Android aarch64，来自 Termux 包。",
             "# 刷新：python3 tools/fetch-adb-android.py",
             "adb(文件在 jniLibs/arm64-v8a/libadb.so) 来自 android-tools " +
             pkgs.get("android-tools", {}).get("Version", "?")]
    for p in sorted(LIBS.iterdir()):
        h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        lines.append("{} {} {}".format(p.name, p.stat().st_size, h))
    (ROOT / "android-native" / "adb-bundle.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print("清单：android-native/adb-bundle.txt")


if __name__ == "__main__":
    main()
