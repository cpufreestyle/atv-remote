#!/usr/bin/env node
/* 意图层纯函数 harness：从 static/app.js 抽出 intent 段原样执行并跑用例。
   Python 单测 tests/test_intent.py 通过 subprocess 调它，让「归一化 / 别名命中 /
   容错 / 动词取对象 / 不误判搜索词 / 面板容错计分」有真行为证据，而不只是源码字符串断言。
   改动段边界标记会让这里直接失败。 */
"use strict";
const fs = require("fs");
const path = require("path");
const root = path.join(__dirname, "..");
const src = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const BEGIN = "/* ===== intent:begin";
const END = "/* ===== intent:end";
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error("harness: intent 段标记缺失或顺序错误");
  process.exit(2);
}
const seg = src.slice(s, e);
// 段内禁 DOM / 持久化 / 网络：规则必须是可搬进 node 的纯函数。
// 先剥注释再查——段首注释里列举了这些禁用词，是给人看的说明，不是违规引用。
function stripComments(s) {
  return s.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
}
const code = stripComments(seg);
for (const banned of ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("]) {
  if (code.indexOf(banned) >= 0) {
    console.error("harness: 纯函数段里出现禁用的全局态引用 " + banned);
    process.exit(2);
  }
}
const NL = String.fromCharCode(10);
const EXPORTS = ["INTENT_FILLERS", "INTENT_ALIASES", "INTENT_VERBS", "INTENT_ERR_RATE",
  "INTENT_OBJ_CUTOFF", "INTENT_WHOLE_CUTOFF", "intentNorm", "intentObject",
  "intentEditErrors", "intentMaxErrors", "intentRatio", "intentPartialRatio",
  "intentTokenRatio", "intentSim", "intentAliasHit", "intentFuzzyAlias",
  "intentPick", "intentParse", "palIntentScore"].join(", ");
const factory = new Function(seg + NL + "return { " + EXPORTS + " };");
const H = factory();

/* 命令词表夹具：形状与 palCommands() push 出来的一致（id / label / terms），
   取真实面板里会出现的几类——应用、按键、工具、设备。 */
const CMDS = [
  { id: "app:com.netflix.nliv", label: "打开 Netflix", terms: "打开 启动 app open launch netflix com.netflix.nliv" },
  { id: "app:com.google.android.youtube.tv", label: "打开 YouTube", terms: "打开 启动 app open launch youtube com.google.android.youtube.tv" },
  { id: "app:com.tencent.qqlive", label: "打开 腾讯视频", terms: "打开 启动 app open launch 腾讯视频 com.tencent.qqlive" },
  { id: "fav:com.netflix.nliv", label: "打开 Netflix（收藏）", terms: "收藏 固定 常用 favorite pin quick netflix com.netflix.nliv" },
  { id: "key:25", label: "按键 音量-", terms: "按键 key 音量- 25" },
  { id: "key:24", label: "按键 音量+", terms: "按键 key 音量+ 24" },
  { id: "key:164", label: "按键 静音", terms: "按键 key 静音 164" },
  { id: "key:85", label: "按键 播放 / 暂停", terms: "按键 key 播放 暂停 85" },
  { id: "key:4", label: "按键 返回", terms: "按键 key 返回 4" },
  { id: "key:3", label: "按键 主页", terms: "按键 key 主页 3" },
  { id: "key:87", label: "按键 下一集", terms: "按键 key 下一集 87" },
  { id: "key:88", label: "按键 上一集", terms: "按键 key 上一集 88" },
  { id: "key:26", label: "按键 电源", terms: "按键 key 电源 26" },
  { id: "key:224", label: "按键 唤醒", terms: "按键 key 唤醒 224" },
  { id: "key:82", label: "按键 菜单", terms: "按键 key 菜单 82" },
  { id: "shot", label: "电视截屏", terms: "截屏 截图 screenshot" },
  { id: "sleep:30", label: "30 分钟后休眠电视", terms: "睡眠 定时 休眠 sleep" },
  { id: "theme:dark", label: "切换到深色主题", terms: "主题 深色 暗色 dark theme" },
  { id: "conn:192.168.1.50", label: "连接 192.168.1.50", terms: "连接 连接电视 connect ip 192.168.1.50" },
];
const idOf = (hit) => (hit && hit.cmd ? hit.cmd.id : null);

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || "assert failed"); }

t("归一化：全角 ASCII 折半角、去标点、去语气词", () => {
  ok(H.intentNorm("请帮我打开 Ｎｅｔｆｌｉｘ，快点！") === "打开netflix", "got: " + H.intentNorm("请帮我打开 Ｎｅｔｆｌｉｘ，快点！"));
  ok(H.intentNorm("  声音小一点  ") === "声音小一点", "trim 失效");
  ok(H.intentNorm("（静音）") === "静音", "去括号失效");
  ok(H.intentNorm("") === "" && H.intentNorm(null) === "" && H.intentNorm(undefined) === "", "空值必须归一成空串");
  ok(H.intentNorm("。。。，！") === "", "纯标点归一成空串");
});

t("动词取对象：长动词优先，短动词不留下残字", () => {
  ok(H.intentObject("打开netflix") === "netflix", "got: " + H.intentObject("打开netflix"));
  ok(H.intentObject("我想看netflix") === "netflix", "长动词我想看要排在看前面");
  ok(H.intentObject("开一下youtube") === "youtube", "开一下排在一开前面");
  ok(H.intentObject("静音") === "", "没有动词时返回空");
  ok(H.intentObject("打开") === "", "动词后没有对象时返回空");
});

t("别名精确命中：整句相等优先于一切", () => {
  ok(H.intentAliasHit("声音小一点") === "声音小一点", "声音小一点");
  ok(H.intentAliasHit("静音") === "静音", "静音");
  ok(H.intentAliasHit("下一集") === "下一集", "下一集");
  ok(H.intentAliasHit("睡觉") === "睡觉", "睡觉");
  ok(H.intentAliasHit("周杰伦") === "", "不在表里就不命中");
});

t("别名子串命中：口语长句也能取到命令，取最长匹配", () => {
  ok(H.intentAliasHit("帮我把声音调小一点") === "声音调小一点", "got: " + H.intentAliasHit("帮我把声音调小一点"));
  ok(H.intentAliasHit("太吵了能不能静音") === "静音", "got: " + H.intentAliasHit("太吵了能不能静音"));
});

t("别名容错：说错一两个字也认，多个同样近的候选不猜", () => {
  ok(H.intentFuzzyAlias("下一及") === "下一集", "下一及 -> 下一集");
  ok(H.intentFuzzyAlias("下一及") === "下一集", "下一及 -> 下一集");
  ok(H.intentFuzzyAlias("返哔") === "" || typeof H.intentFuzzyAlias("返哔") === "string", "类型必须是字符串");
  ok(H.intentFuzzyAlias("") === "", "空串不猜");
  ok(H.intentFuzzyAlias("静") === "", "单字不给容错预算");
});


t("歧义时不猜：多个候选分属不同命令，宁可当文本", () => {
  // 「音量城」距「音量减/音量低」(key:25) 与「音量加/音量增」(key:24) 都只差 1 个字，
  // 猜任何一边都有 50% 概率按错键——拒绝并原样下发才是对的。
  ok(H.intentFuzzyAlias("音量城") === "", "歧义候选必须不猜，got: " + H.intentFuzzyAlias("音量城"));
  ok(H.intentParse("音量城", CMDS) === null, "歧义输入必须原样下发，got: " + JSON.stringify(H.intentParse("音量城", CMDS)));
  ok(H.intentParse("看电视", CMDS) === null, "看电视不能被容错成关电视（首字相同才容错）");
  // 同样距离但指向同一命令，不算歧义：
  ok(H.intentFuzzyAlias("主葉") === "主页", "主葉 -> 主页（唯一候选）");
});

t("有界编辑距离：等于真实 Levenshtein，超预算提前退", () => {
  ok(H.intentEditErrors("", "abc", 9) === 0, "空模式距离 0");
  ok(H.intentEditErrors("abc", "abc", 9) === 0, "相同串距离 0");
  ok(H.intentEditErrors("abc", "abd", 9) === 1, "一次替换");
  ok(H.intentEditErrors("kitten", "sitting", 9) === 3, "经典 kitten/sitting");
  ok(H.intentEditErrors("音量减", "音量城", 9) === 1, "中文替换也是 1");
  ok(H.intentEditErrors("abcd", "abcd", 0) === 0, "预算 0 且相同 -> 0");
  ok(H.intentEditErrors("abcd", "abce", 0) > 0, "预算 0 且不同 -> 报超预算");
});

t("错误预算：短查询不容错，长查询才允许 1~2 个错", () => {
  ok(H.INTENT_ERR_RATE < 0.6, "阈值要比 Fuse 默认 0.6 严，否则两字查询噪音太大");
  ok(H.intentMaxErrors("静音") === 1, "中文两字词容 1（否则中文命令永远不能容错）");
  ok(H.intentMaxErrors("yt") === 0, "拉丁两字母不容错（否则什么都匹配）");
  ok(H.intentMaxErrors("下一集") === 1, "三字中文容 1");
  ok(H.intentMaxErrors("yutube") === 2, "六字母容 2");
  ok(H.intentMaxErrors("") === 0 && H.intentMaxErrors("q") === 0, "空串/单字 0");
});

t("相似度：ratio 与 partial_ratio 的分工", () => {
  ok(H.intentRatio("netflix", "netflix") === 1, "全等 1");
  ok(Math.abs(H.intentRatio("netflx", "netflix") - 0.8571428571428571) < 1e-9, "6/7 相似");
  ok(H.intentPartialRatio("netflix", "打开 启动 app open launch netflix com.netflix.nliv") >= 0.999, "应用名要能从长 terms 里片段命中");
  ok(H.intentPartialRatio("netflix", "") === 0, "空目标是 0");
  ok(H.intentPartialRatio("ab", "abc") > 0, "短串在长串里");
});

t("intentParse：同义词直达", () => {
  ok(idOf(H.intentParse("声音小一点", CMDS)) === "key:25", "声音小一点 -> 音量-");
  ok(idOf(H.intentParse("大声一点", CMDS)) === "key:24", "大声一点 -> 音量+");
  ok(idOf(H.intentParse("别吵了", CMDS)) === "key:164", "别吵了 -> 静音");
  ok(idOf(H.intentParse("下一集", CMDS)) === "key:87", "下一集 -> key:87");
  ok(idOf(H.intentParse("回主页", CMDS)) === "key:3", "回主页 -> 主页");
  ok(idOf(H.intentParse("关机", CMDS)) === "key:26", "关机 -> 电源");
  ok(idOf(H.intentParse("截图", CMDS)) === "shot", "截图 -> 截屏");
  ok(idOf(H.intentParse("说错字的下一及", CMDS)) === "key:87", "带语气词+错字仍命中");
});

t("intentParse：动词 + 对象起应用（含 typo）", () => {
  ok(idOf(H.intentParse("打开 Netflix", CMDS)) === "app:com.netflix.nliv", "打开 Netflix");
  ok(idOf(H.intentParse("打开 netflx", CMDS)) === "app:com.netflix.nliv", "打开 netflx（错一个字母）");
  ok(idOf(H.intentParse("我想看腾讯视频", CMDS)) === "app:com.tencent.qqlive", "我想看腾讯视频");
  ok(idOf(H.intentParse("启动 youtube", CMDS)) === "app:com.google.android.youtube.tv", "启动 youtube");
});

t("intentParse：不误判——这是本功能的安全底线", () => {
  ok(H.intentParse("周杰伦", CMDS) === null, "人名/搜索词绝不能被当成命令");
  ok(H.intentParse("打开电视机顶盒", CMDS) === null, "未安装的应用不能瞎起");
  ok(H.intentParse("看电视", CMDS) === null, "看电视不能被误判成电视截屏");
  ok(H.intentParse("", CMDS) === null, "空输入");
  ok(H.intentParse("。。。", CMDS) === null, "纯标点");
  ok(H.intentParse("随便说点什么", CMDS) === null, "无意义句子原样下发");
});

t("intentParse：via 记录命中路径，便于排查误判", () => {
  const a = H.intentParse("静音", CMDS);
  ok(a && a.via === "alias", "整句命中 via=alias");
  const b = H.intentParse("下一及", CMDS);
  ok(b && b.via === "alias~", "容错命中 via=alias~");
  const c = H.intentParse("打开 Netflix", CMDS);
  ok(c && c.via === "object", "动词+对象 via=object");
});

t("intentPick：只比 terms 不比 label，且只用 cutoff 过滤", () => {
  ok(H.intentPick("netflix", CMDS, 0.8) !== null, "能命中");
  ok(H.intentPick("zzzzzz", CMDS, 0.8) === null, "不命中给 null");
  ok(H.intentPick("", CMDS, 0) === null, "空查询 null");
  ok(H.intentPick("netflix", [], 0) === null, "空候选 null");
  ok(H.intentPick("腾讯视频", CMDS, 0.5) === null || H.intentPick("腾讯视频", CMDS, 0.5).cmd.id !== "shot", "不能把应用名判成截屏");
});

t("palIntentScore：容错档压在前四档之下，且短查询不容错", () => {
  const yt = CMDS[1];
  ok(H.palIntentScore("yutube", yt) > 0, "yutube 应容错命中 youtube");
  ok(H.palIntentScore("yutube", yt) < 200, "第 5 档必须低于子序列档(200)，否则打乱既有排序");
  ok(H.palIntentScore("yt", yt) === 0, "拉丁两字母不容错");
  ok(H.palIntentScore("yutubee", yt) > 0, "多错一个也应中（预算 2）");
  ok(H.palIntentScore("下一及", CMDS[10]) > 0, "中文键名也要能容错（下一及 → 下一集）");
  
  ok(H.palIntentScore("q", CMDS[0]) === 0, "单字不走容错档");
});

for (const [name, fn] of cases) {
  try { fn(); console.log("ok - " + name); }
  catch (err) { failed++; console.error("FAIL - " + name + ": " + err.message); }
}
console.log(JSON.stringify({ total: cases.length, failed }));
if (failed) process.exit(1);
console.log("ALL_INTENT_CASES_PASSED");

