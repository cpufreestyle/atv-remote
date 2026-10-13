#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""派发契约：把「哪个路径由哪段代码应答」钉成可比对的三元组。

为什么需要它：server.py 是 2600+ 行的单文件，GET 用一串 if 链手写派发、POST 走 ROUTES 字典。
拆包时最容易出的事故不是写错逻辑，而是**某个分支被整段落下**——而 603 个测试里若没人打那个
路径，它照样全绿。所以拆分前后必须跑同一个抽取器，与下面这份冻结基线逐条相等。

三元组是 (path, method, status, 该分支里调用到的模块级函数名指纹)，不只比 path 列表：
只比 rule 会漏掉「视图函数被换掉 / Content-Type 退化」这类静默改变。

基线来源：拆分前从磁盘上的 server.py 用本文件同一套抽取逻辑导出，逐条抄在下面。
真要改派发（加端点、换视图）就同时改这份冻结表，让 diff 里看得见。
"""

import ast
import unittest

import server

SERVER_PY = "server.py"


def _source():
    """带 with 打开：基线输出里不该混 ResourceWarning（check.sh 的噪音就是要被消掉的东西）"""
    with open(SERVER_PY, encoding="utf-8") as f:
        return f.read()

EXPECTED_DISPATCH = [
    ('/', 'GET', 200, ()),
    ('/api/android/pair', 'POST', 200, ('handle_android_pair',)),
    ('/api/android/scan', 'POST', 200, ('handle_android_scan',)),
    ('/api/atv/apps', 'POST', 200, ('handle_atv_apps',)),
    ('/api/atv/connect', 'POST', 200, ('handle_atv_connect',)),
    ('/api/atv/disconnect', 'POST', 200, ('handle_atv_disconnect',)),
    ('/api/atv/forget', 'POST', 200, ('handle_atv_forget',)),
    ('/api/atv/pair', 'POST', 200, ('handle_atv_pair',)),
    ('/api/atv/scan', 'POST', 200, ('handle_atv_scan',)),
    ('/api/cmd', 'POST', 200, ('handle_cmd',)),
    ('/api/connect', 'POST', 200, ('handle_connect',)),
    ('/api/diagnostics', 'GET', 200, ('make_diagnostics',)),
    ('/api/disconnect', 'POST', 200, ('handle_disconnect',)),
    ('/api/forget', 'POST', 200, ('handle_forget',)),
    ('/api/ime', 'POST', 200, ('handle_ime',)),
    ('/api/macros', 'GET', 200, ('handle_macros',)),
    ('/api/nowplaying', 'GET', 200, ('now_playing',)),
    ('/api/perf', 'GET', 200, ('perf_snapshot',)),
    ('/api/qr.svg', 'GET', 200, ()),
    ('/api/screenshot', 'GET', 200, ()),
    ('/api/setup', 'GET', 200, ('lan_ip',)),
    ('/api/status', 'GET', 200, ('make_status',)),
    ('/api/switch', 'POST', 200, ('handle_switch',)),
    ('/api/volume', 'GET', 200, ('volume',)),
    ('/api/volume', 'POST', 200, ('handle_volume_set',)),
    ('/api/wol', 'GET', 200, ('handle_wol',)),
    ('/api/wol', 'POST', 200, ('handle_wol',)),
    ('/app.apk', 'GET', 200, ()),
    ('/bundle.tgz', 'GET', 200, ('cached_bundle',)),
    ('/index.html', 'GET', 200, ()),
    ('/install', 'GET', 200, ('token_query',)),
    ('prefix:/static/', 'GET', 200, ('static_ctype',)),
]


def _local_module_names():
    """本地模块名（server.py / atv_*.py），用来认出「从本地模块再导进来的函数」"""
    return {name.rsplit(".", 1)[0] for name in server.BUNDLE_PY}


def _top_level_names(tree):
    """server.py 命名空间里的**本地函数名**：本文件定义的 + 从本地模块再导出的。

    外提纯函数时指纹不该变：`/static/` 那条仍然调用 static_ctype()，只是它搬到了
    atv_assets.py。只数 FunctionDef 的话，每次搬家都会伪装成「派发变了」。
    只认本地模块：把 Path / html_escape 这类 stdlib 名字也收进来的话，指纹会被
    与派发无关的调用污染（刚才就多出过 ('html_escape',)）。"""
    local = _local_module_names()
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in local:
            for a in node.names:
                names.add(a.asname or a.name)
    return names


def _handler_method(tree, name):
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Handler"), None)
    if cls is None:
        raise AssertionError("server.py 里没有 Handler 类：派发结构变了，抽取器要一起改")
    fn = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert fn is not None, "Handler 里没有 %s()" % name
    return fn


def _first_send(node):
    """分支里第一个 _send(...)：状态码 + 可选的 Content-Type 字面量"""
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if name == "_send" and n.args:
                code = n.args[0].value if isinstance(n.args[0], ast.Constant) else "?"
                ctype = n.args[2].value if len(n.args) > 2 and isinstance(n.args[2], ast.Constant) else None
                return code, ctype
    return None, None


def _calls_in(node, names):
    """分支体里调用到的「模块级函数名」，含 adb.run(...) 这种以模块级名字为根的调用"""
    out = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        root = None
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            root = f.value.id
        elif isinstance(f, ast.Name):
            root = f.id
        if root in names:
            out.add(root)
    return tuple(sorted(out))


def _path_literals(test):
    """把 `path == "/x"` / `path in ("/", "/index.html")` / `path.startswith("/static/")` 归一化"""
    if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "path"):
        if not (isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute)
                and test.func.attr == "startswith" and isinstance(test.func.value, ast.Name)
                and test.func.value.id == "path"):
            return []
        return ["prefix:" + test.args[0].value]
    op = test.ops[0]
    if isinstance(op, ast.Eq):
        return [test.comparators[0].value]
    if isinstance(op, ast.In) and isinstance(test.comparators[0], (ast.Tuple, ast.List)):
        return [e.value for e in test.comparators[0].elts]
    return []


def actual_dispatch():
    src = _source()
    tree = ast.parse(src)
    names = _top_level_names(tree)
    serve = _handler_method(tree, "_serve")

    rows = []
    for node in sorted((n for n in ast.walk(serve) if isinstance(n, ast.If)), key=lambda n: n.lineno):
        code, _ctype = _first_send(node)
        if code is None:          # 鉴权早退一类的 if，不产生响应
            continue
        for path in _path_literals(node.test):
            rows.append((path, "GET", code, _calls_in(node, names)))

    for path, fn in server.ROUTES.items():
        rows.append((path, "POST", 200, (fn.__name__,)))
    return sorted(rows)


class RouteContractTest(unittest.TestCase):

    def test_extractor_is_not_vacuous(self):
        """抽取器只会「找到多少算多少」：它一旦因为结构变化而返回空表，逐条比对就会假绿。
        刚才写这个测试时就真的抽到过 0 条（分支链在 try 里，按顶层语句找不到）。"""
        rows = actual_dispatch()
        self.assertGreaterEqual(len(rows), len(EXPECTED_DISPATCH),
                                "抽取结果只有 %d 条，少于冻结基线的 %d 条：抽取器或派发结构变了"
                                % (len(rows), len(EXPECTED_DISPATCH)))

    def test_dispatch_identical_to_frozen_baseline(self):
        self.assertEqual(actual_dispatch(), sorted(EXPECTED_DISPATCH))

    def test_get_and_head_share_one_dispatcher(self):
        """HEAD 必须与 GET 同一条派发链，否则「只回头不回体」的能力会在拆分时丢掉
        （丢掉的表象是手机/浏览器点下载没反应，而直接敲 URL 是好的）。"""
        tree = ast.parse(_source())
        for name in ("do_GET", "do_HEAD"):
            body = ast.dump(_handler_method(tree, name))
            self.assertIn("_serve", body, "%s() 不再走 _serve()" % name)

    def test_404_fallback_still_exists(self):
        """两条链都必须保留兜底 404，否则未匹配路径会掉进异常分支变成 500。
        注意 _serve 的 404 在 try 尾部、不包在 if 里，所以这里扫全部 _send 而不是只看分支。"""
        tree = ast.parse(_source())
        for name in ("_serve", "do_POST"):
            codes = set()
            for n in ast.walk(_handler_method(tree, name)):
                if isinstance(n, ast.Call):
                    f = n.func
                    nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                    if nm == "_send" and n.args and isinstance(n.args[0], ast.Constant):
                        codes.add(n.args[0].value)
            self.assertIn(404, codes, "%s() 的 404 兜底不见了（现有状态码：%s）" % (name, sorted(codes)))

    def test_reexported_local_functions_still_count(self):
        """纯函数搬到 atv_media.py 之后仍要能被认出来，否则「搬家」会把指纹安静地清空；
        反过来 stdlib 的名字（html_escape / Path）不该进来——它们与派发无关。"""
        names = _top_level_names(ast.parse(_source()))
        for fn in ("parse_media_session", "clamp_volume_level", "volume_set_cmd"):
            self.assertIn(fn, names, "%s 已外提，抽取器认不出再导出的名字" % fn)
        self.assertNotIn("html_escape", names)
        self.assertNotIn("Path", names)

    def test_no_duplicate_route(self):
        rows = actual_dispatch()
        seen = [r for r in rows if r[1] == "POST"]
        self.assertEqual(len(seen), len(set(seen)), "ROUTES 里有重复/覆盖的端点")
        self.assertEqual(len(set(server.ROUTES)), len(seen),
                         "server.ROUTES 的键数与抽取到的 POST 数不一致")


if __name__ == "__main__":
    unittest.main()
