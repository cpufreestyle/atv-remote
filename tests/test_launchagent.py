#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LaunchAgent 启动参数回归。

动机：用户明确要求「不要输入访问令牌」——手机/平板开 http://<Mac局域网IP>:8300 就该
能直接用。令牌本身是 server.py 的可选项，但 launchd 那份 plist 由 mac-install.sh 生成，
一旦谁把 --no-token 删了，局域网设备又会跳登录页，而这个问题在 Mac 本机完全看不出来
（回环永远免令牌）。

验三件事（只读仓库 + 标准库，不起真端口）：
  1) mac-install.sh 写进 plist 的启动参数里有 --no-token，且没有把令牌参数写死；
  2) resolve_token 的优先级没被顺手改坏：--no-token 即使 state.json 里有令牌也返回空；
  3) README 写明了这份 LaunchAgent 默认不鉴权，以及要收紧该怎么做。
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAC_INSTALL = ROOT / "mac-install.sh"
README = ROOT / "README.md"


def _plist_heredoc(sh: str) -> str:
    start = sh.find("cat > \"$PLIST\" << EOF")
    if start < 0:
        return ""
    body = sh[start:]
    end = body.find("\nEOF\n", 1)
    return body[: end + 5] if end > 0 else body


class LaunchAgentArgsTest(unittest.TestCase):
    """开机自启的那条命令必须不启用令牌。"""

    @classmethod
    def setUpClass(cls):
        cls.sh = MAC_INSTALL.read_text(encoding="utf-8")
        cls.plist_xml = _plist_heredoc(cls.sh)

    def test_heredoc_found(self):
        self.assertIn("ProgramArguments", self.plist_xml,
                      "mac-install.sh 的 plist 生成段没找到")

    def test_no_token_flag_present(self):
        self.assertIn("<string>--no-token</string>", self.plist_xml,
                      "LaunchAgent 必须带 --no-token，否则局域网设备又要输令牌")
        self.assertIn("<string>--no-open</string>", self.plist_xml)

    def test_no_hardcoded_token(self):
        self.assertNotIn("--token ", self.plist_xml,
                         "plist 里不该写死令牌")

    def test_plist_keeps_autostart_contract(self):
        for key in ("RunAtLoad", "KeepAlive", "WorkingDirectory", "ThrottleInterval"):
            self.assertIn(key, self.plist_xml)
        self.assertIn("<true/>", self.plist_xml)


class ResolveTokenSemanticsTest(unittest.TestCase):
    """--no-token 的语义：state.json 里有旧令牌也要置空。"""

    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server

    def test_no_token_wins_over_stored_token(self):
        srv = self.server
        got = srv.resolve_token(None, "0.0.0.0", True,
                               {"token": "old-token-value"})
        self.assertEqual(got, "", "--no-token 必须压过 state.json 里的旧令牌")

    def test_loopback_only_is_open_too(self):
        srv = self.server
        self.assertEqual(srv.resolve_token(None, "127.0.0.1", False, {}), "")

    def test_default_still_generates_token(self):
        srv = self.server
        st = {}
        got = srv.resolve_token(None, "0.0.0.0", False, st)
        self.assertTrue(got, "默认（不带 --no-token）仍应自动生成令牌")
        self.assertEqual(st.get("token"), got, "生成的令牌要落进 state（重启不变）")

    def test_no_token_means_empty_token_query(self):
        srv = self.server
        old = srv.AUTH_TOKEN
        try:
            srv.AUTH_TOKEN = ""
            self.assertEqual(srv.token_query(), "")
        finally:
            srv.AUTH_TOKEN = old


class DocsTest(unittest.TestCase):
    """文档要说清这份 LaunchAgent 默认不鉴权。"""

    def test_readme_documents_launchagent_no_token(self):
        text = README.read_text(encoding="utf-8")
        hits = [ln for ln in text.splitlines() if "--no-token" in ln]
        self.assertTrue(hits, "README 要提到 --no-token")
        self.assertTrue(any("mac-install" in ln or "LaunchAgent" in ln for ln in hits),
                        "README 要说清 LaunchAgent 默认不鉴权")

    def test_readme_keeps_reenable_path(self):
        text = README.read_text(encoding="utf-8")
        self.assertIn("--token", text, "要留下收紧回鉴权的路径")


if __name__ == "__main__":
    unittest.main()

