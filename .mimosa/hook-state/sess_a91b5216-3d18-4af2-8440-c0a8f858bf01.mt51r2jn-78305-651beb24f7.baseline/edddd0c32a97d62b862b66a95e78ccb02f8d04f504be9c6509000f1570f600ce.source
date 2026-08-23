package com.atv.remote;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.IOException;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * ATV Remote 壳：
 *  - 独立模式：通过 Termux RUN_COMMAND 拉起手机内引擎（~/atv-remote/start.sh），加载 127.0.0.1:8300
 *  - 电脑模式：连接 Mac 上的 server.py（--host 0.0.0.0）
 */
public class MainActivity extends Activity {

    private static final String TERMUX_PKG = "com.termux";
    private static final String TERMUX_HOME = "/data/data/com.termux/files/home";
    private static final String LOCAL_URL = "http://127.0.0.1:8300";

    private WebView webView;
    private EditText urlInput;
    private LinearLayout setupPanel;
    private TextView statusLine;
    private String currentUrl;

    @Override
    @SuppressLint("SetJavaScriptEnabled")
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        webView = findViewById(R.id.webview);
        urlInput = findViewById(R.id.urlInput);
        setupPanel = findViewById(R.id.setupPanel);
        statusLine = findViewById(R.id.statusLine);
        Button connectBtn = findViewById(R.id.connectBtn);
        Button changeBtn = findViewById(R.id.changeBtn);
        Button standaloneBtn = findViewById(R.id.standaloneBtn);

        SharedPreferences prefs = getSharedPreferences("atv", MODE_PRIVATE);
        String saved = prefs.getString("server", "");
        urlInput.setText(saved.isEmpty() || LOCAL_URL.equals(saved)
                ? getString(R.string.default_url) : saved);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    toast("无法连接服务器（引擎未启动或地址不对）");
                }
            }
        });

        connectBtn.setOnClickListener(v -> connect(urlInput.getText().toString().trim(), true));
        changeBtn.setOnClickListener(v -> showSetup());
        standaloneBtn.setOnClickListener(v -> startStandalone());

        if (!saved.isEmpty()) {
            if (LOCAL_URL.equals(saved)) {
                startStandalone();   // 上次用的独立模式：自动拉起引擎
            } else {
                connect(saved, false);
            }
        }
    }

    /* ---------------- 独立模式（Termux 引擎） ---------------- */

    private boolean termuxInstalled() {
        try {
            getPackageManager().getPackageInfo(TERMUX_PKG, 0);
            return true;
        } catch (PackageManager.NameNotFoundException e) {
            return false;
        }
    }

    private void startStandalone() {
        if (!termuxInstalled()) {
            statusLine.setText(R.string.need_termux);
            toast("未检测到 Termux，请先按说明安装（见电脑上的 ~/atv-remote/README）");
            return;
        }
        statusLine.setText(R.string.starting_engine);
        try {
            Intent i = new Intent("com.termux.RUN_COMMAND");
            i.setClassName(TERMUX_PKG, "com.termux.app.RunCommandService");
            i.putExtra("com.termux.RUN_COMMAND_PATH", TERMUX_HOME + "/atv-remote/start.sh");
            i.putExtra("com.termux.RUN_COMMAND_WORKDIR", TERMUX_HOME + "/atv-remote");
            i.putExtra("com.termux.RUN_COMMAND_BACKGROUND", true);
            startService(i);
        } catch (Exception e) {
            toast("拉起 Termux 失败: " + e.getMessage());
        }
        // 轮询本地端口就绪（首次冷启动 python 可能要几秒）
        new Thread(() -> {
            boolean up = false;
            for (int n = 0; n < 40 && !up; n++) {   // 最多约 20 秒
                up = ping(LOCAL_URL + "/api/status");
                if (!up) try { Thread.sleep(500); } catch (InterruptedException ignored) {}
            }
            final boolean ok = up;
            runOnUiThread(() -> {
                if (ok) {
                    saveServer(LOCAL_URL);
                    openWeb(LOCAL_URL);
                } else {
                    statusLine.setText(R.string.engine_failed);
                    toast("手机引擎未就绪：打开 Termux 手动运行 ~/atv-remote/start.sh 看报错");
                }
            });
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

    /* ---------------- 电脑模式 ---------------- */

    private void connect(String url, boolean save) {
        if (url.isEmpty()) { toast("请输入服务器地址"); return; }
        if (!url.startsWith("http")) url = "http://" + url;
        if (save) saveServer(url);
        openWeb(url);
    }

    private void openWeb(String url) {
        currentUrl = url;
        statusLine.setText("");
        setupPanel.setVisibility(View.GONE);
        webView.setVisibility(View.VISIBLE);
        webView.loadUrl(url);
    }

    private void showSetup() {
        webView.setVisibility(View.GONE);
        setupPanel.setVisibility(View.VISIBLE);
        webView.loadUrl("about:blank");
    }

    private void saveServer(String url) {
        getSharedPreferences("atv", MODE_PRIVATE).edit().putString("server", url).apply();
    }

    private void toast(String msg) {
        Toast.makeText(this, msg, Toast.LENGTH_LONG).show();
    }

    /* ---------------- 系统 ---------------- */

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            if (webView.getVisibility() == View.VISIBLE) {
                if (webView.canGoBack()) {
                    webView.goBack();
                } else {
                    showSetup();
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
        // 设置页回来时刷新（引擎/服务器可能刚启动）
        if (currentUrl != null && webView.getVisibility() == View.VISIBLE) {
            webView.reload();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (webView != null) webView.onPause();
    }
}
