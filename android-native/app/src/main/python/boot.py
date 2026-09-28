"""ATV Remote 原生版引导：App 内启动内置遥控服务（Chaquopy 环境）"""
import os
import sys
import threading
import traceback


def _install_crypto_shim():
    """Chaquopy 下 cryptography 的 Rust 扩展类不可被继承，
    而 chacha20poly1305_reuseable(0.0.4) 恰好用了继承 → 注入组合式 shim。
    cryptography 官方 aead 本身就是可复用实现，性能无损。"""
    try:
        import chacha20poly1305_reuseable  # noqa: F401  原生可用则不注入
        return
    except Exception:
        pass
    # 降级构建（-PnoAppletv，chaquo.com 不通时）没有 cryptography：这个 shim 本来
    # 只是为 pyatv 兜底的，没 pyatv 就不需要，安静跳过。早先下面这行 import 没保护，
    # cryptography 缺失会直接把整个引擎带崩（ModuleNotFoundError 冒到 _run 外）。
    try:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    except Exception:
        return

    class ChaCha20Poly1305Reusable:
        def __init__(self, key):
            self._aead = ChaCha20Poly1305(bytes(key))

        def encrypt(self, nonce, data, associated_data=None):
            return self._aead.encrypt(nonce, data, associated_data)

        def decrypt(self, nonce, data, associated_data=None):
            return self._aead.decrypt(nonce, data, associated_data)

    import types
    m = types.ModuleType("chacha20poly1305_reuseable")
    m.ChaCha20Poly1305Reusable = ChaCha20Poly1305Reusable
    m.__version__ = "0.0.4-shim"
    sys.modules["chacha20poly1305_reuseable"] = m


def start_server(port=8300, files=None, nativelib=None, adb_libs=None):
    """在后台线程启动 server.py（状态文件写入 App 私有目录）。

    files / nativelib / adb_libs 由 NativeActivity 从 Java 侧传进来。不绕
    `from com.chaquo.python import Android` 是因为那个 import 在后台线程上会
    直接 ModuleNotFoundError（真机/模拟器都复现过），而 Java 侧本来就有这些路径。
    """
    if not files:
        # 兜底（比如从 Python 侧直接调）：拿不到就退回 home，至少服务能起
        try:
            from com.chaquo.python import Android
            ctx = Android.applicationContext()
            files = str(ctx.getFilesDir().getPath())
            nativelib = nativelib or str(ctx.getApplicationInfo().nativeLibraryDir)
        except Exception:
            files = files or os.path.expanduser("~")
            nativelib = nativelib or ""
    adb_libs = adb_libs or ""
    # adb 本体在 jniLibs/arm64-v8a/libadb.so，装机时由 PackageManager 解到
    # nativeLibraryDir——整个 Android 上基本只有那个目录（app_lib_file）允许
    # execve；Android 10+ 起 App 私有目录一律 noexec，解到 files/ 再跑会 EACCES。
    os.environ["ATV_STATE"] = files + "/state.json"
    # 标记「服务跑在手机 App 里」。页面据此隐藏「把遥控器装到手机」那张卡片——
    # 手机上都装好了，而且 APK 文件本身不在包里，点下载只会拿到 404「APK 不存在」。
    os.environ["ATV_EMBEDDED"] = "1"
    adb = os.path.join(nativelib, "libadb.so") if nativelib else ""
    if adb and os.access(adb, os.X_OK):
        # 依赖库在 files/adb-libs（assets 解出来的）；动态链接器只认 LD_LIBRARY_PATH，
        # 而子进程会继承这个环境变量，所以 adb 一启动就能找到它们
        parts = [p for p in (adb_libs, os.environ.get("LD_LIBRARY_PATH")) if p]
        if parts:
            os.environ["LD_LIBRARY_PATH"] = ":".join(parts)
        os.environ.setdefault("ADB_PATH", adb)
    else:
        # 拿不到（非 arm64 设备 / 解压失败）：指个不存在的路径，
        # 让服务端优雅降级，而不是崩掉
        os.environ.setdefault("ADB_PATH", files + "/no-adb")

    def _run():
        try:
            _install_crypto_shim()
            sys.argv = ["server.py", "--host", "127.0.0.1", "--port", str(port), "--no-open"]
            import server
            server.main()
        except Exception:
            traceback.print_exc()

    t = threading.Thread(target=_run, name="atv-server", daemon=True)
    t.start()
    return True
