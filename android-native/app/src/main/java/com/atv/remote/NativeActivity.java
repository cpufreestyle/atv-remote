package com.atv.remote;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.LinearLayout;
import android.widget.TextView;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.io.File;

/**
 * ATV Remote 原生版：App 内嵌 Python 引擎（Chaquopy），完全离线独立运行。
 * 启动 → 拉起内置 server.py（127.0.0.1:8300）→ 就绪后 WebView 加载遥控器界面。
 */
public class NativeActivity extends Activity {

    private static final String LOCAL_URL = "http://127.0.0.1:8300";

    private WebView webView;
    private LinearLayout bootPanel;
    private TextView bootStatus;

    @Override
    @SuppressLint("SetJavaScriptEnabled")
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_native);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        webView = findViewById(R.id.webview);
        bootPanel = findViewById(R.id.bootPanel);
        bootStatus = findViewById(R.id.bootStatus);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    setStatus("引擎连接中断，正在重试…");
                    retryLater();
                }
            }
        });

        bootEngine();
    }

    private void bootEngine() {
        setStatus("🚀 正在启动内置引擎…");
        new Thread(() -> {
            String err = null;
            try {
                Python py = Python.getInstance();
                PyObject boot = py.getModule("boot");
                // adb 本体（jniLibs 里的 libadb.so）与它依赖的几个库要在这之前备好。
                // 文件活放 Java 侧做：Chaquopy 的 Python 里 import com.chaquo.python
                // 在某些线程上会直接 ModuleNotFoundError，而 Java 本来就有这些路径。
                File libDir = extractAdbLibs();
                boot.callAttr("start_server", 8300,
                        getFilesDir().getAbsolutePath(),
                        getApplicationInfo().nativeLibraryDir,
                        libDir == null ? "" : libDir.getAbsolutePath());
            } catch (Throwable e) {
                err = String.valueOf(e);
            }
            if (err != null) {
                final String e = err;
                runOnUiThread(() -> setStatus("引擎启动失败: " + e));
                return;
            }
            // 轮询就绪（首次冷启动 python 初始化约需数秒）
            boolean up = false;
            for (int n = 0; n < 60 && !up; n++) {
                up = ping(LOCAL_URL + "/api/status");
                if (!up) try { Thread.sleep(500); } catch (InterruptedException ignored) {}
            }
            final boolean ok = up;
            runOnUiThread(() -> {
                if (ok) {
                    bootPanel.setVisibility(View.GONE);
                    webView.setVisibility(View.VISIBLE);
                    webView.loadUrl(LOCAL_URL);
                } else {
                    setStatus("⚠️ 引擎未就绪，请重启 App 重试");
                }
            });
        }).start();
    }

    /**
     * 把 assets/adb-libs 里的依赖库解压到 files/adb-libs。
     *
     * adb 是 DT_NEEDED 按文件名找库的（libz.so.1 这种带版本号的名字不能改），
     * 而 APK 的 jniLibs 只收 *.so，所以这些库走 assets，运行时解出来再用
     * LD_LIBRARY_PATH 指过去。返回解压目录，失败返回 null。
     */
    private File extractAdbLibs() {
        try {
            String[] names = getAssets().list("adb-libs");
            if (names == null || names.length == 0) return null;
            File dir = new File(getFilesDir(), "adb-libs");
            if (!dir.exists() && !dir.mkdirs()) return null;
            byte[] buf = new byte[8192];
            for (String n : names) {
                if ("MANIFEST.txt".equals(n)) continue;
                File out = new File(dir, n);
                try (java.io.InputStream in = getAssets().open("adb-libs/" + n);
                     java.io.FileOutputStream fos = new java.io.FileOutputStream(out)) {
                    int r;
                    while ((r = in.read(buf)) > 0) fos.write(buf, 0, r);
                }
                // noinspection ResultOfMethodCallIgnored
                out.setReadable(true, true);
            }
            return dir;
        } catch (IOException e) {
            return null;
        }
    }

    private void retryLater() {
        new Thread(() -> {
            for (int n = 0; n < 30; n++) {
                if (ping(LOCAL_URL + "/api/status")) break;
                try { Thread.sleep(1000); } catch (InterruptedException ignored) {}
            }
            runOnUiThread(() -> webView.reload());
        }).start();
    }

    private boolean ping(String url) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(800);
            c.setReadTimeout(800);
            int code = c.getResponseCode();
            c.disconnect();
            return code == 200;
        } catch (IOException e) {
            return false;
        }
    }

    private void setStatus(String msg) {
        bootStatus.setText(msg);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (webView.getVisibility() == View.VISIBLE) {
                if (webView.canGoBack()) {
                    webView.goBack();
                } else {
                    moveTaskToBack(true);
                }
                return true;
            }
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) webView.onResume();
        if (webView != null && webView.getVisibility() == View.VISIBLE) {
            webView.reload();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (webView != null) webView.onPause();
    }
}
