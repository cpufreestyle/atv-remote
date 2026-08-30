"""ATV Remote 原生版引导：App 内启动内置遥控服务（Chaquopy 环境）"""
import os
import sys
import threading
import traceback


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
        sys.argv = ["server.py", "--host", "127.0.0.1", "--port", str(port), "--no-open"]
        try:
            import server
            server.main()
        except Exception:
            traceback.print_exc()

    t = threading.Thread(target=_run, name="atv-server", daemon=True)
    t.start()
    return True
