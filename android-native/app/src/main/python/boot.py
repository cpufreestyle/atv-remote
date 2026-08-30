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
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

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


def start_server(port=8300):
    """在后台线程启动 server.py（状态文件写入 App 私有目录）"""
    try:
        from com.chaquo.python import Android
        files = str(Android.applicationContext().getFilesDir().getPath())
    except Exception:
        files = os.path.expanduser("~")
    os.environ["ATV_STATE"] = files + "/state.json"
    # App 沙箱无 adb 二进制：指向不存在的路径，服务端会优雅禁用 Android TV 功能
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
