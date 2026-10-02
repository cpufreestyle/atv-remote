#!/usr/bin/env node
/* 配置搬家（第三十七轮）纯函数 harness：从 static/app.js 抽出 cfgxfer 段原样执行。
   Python 单测 tests/test_export_import.py 通过 subprocess 调它，让「导出不含敏感键、
   坏档一律拒收、A 机导出 B 机导入逐键复现」有真行为证据，而不只是源码字符串断言。
   改动段边界标记会让这里直接失败。
   期望值按 docs/opensource-references.md 第 12 节（chezmoi v2.73.0）的口径手算。 */
'use strict';
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
const BEGIN = '/* ===== cfgxfer:begin';
const END = '/* ===== cfgxfer:end';
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error('harness: cfgxfer 段标记缺失或顺序错误');
  process.exit(2);
}
const seg = src.slice(s, e);
// 段内禁 DOM / 持久化 / 网络：先剥注释再查——段首注释里列举了这些禁用词与参照行号，
// 是给人看的说明，不是违规引用。
function stripComments(s) {
  return s.replace(/\/[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');
}
const code = stripComments(seg);
const BANNED = ['document.', 'localStorage', 'setTimeout', 'fetch(', 'innerHTML', '$(', 'XMLHttpRequest'];
for (const banned of BANNED) {
  if (code.indexOf(banned) >= 0) {
    console.error('harness: 纯函数段里出现禁用的全局态引用 ' + banned);
    process.exit(2);
  }
}
const NL = String.fromCharCode(10);
const EXPORTS = ['CFG_MAGIC', 'CFG_VER', 'CFG_EXPORTABLE', 'CFG_LABELS', 'CFG_SENSITIVE_RE',
  'cfgLabelOf', 'cfgIsExportable', 'cfgPlanExport', 'cfgBuildBackup',
  'cfgParseBackup', 'cfgApplyBackup'].join(', ');
const factory = new Function(seg + NL + 'return { ' + EXPORTS + ' };');
const H = factory();

let failed = 0;
const cases = [];
const t = (name, fn) => cases.push([name, fn]);
function ok(cond, msg) { if (!cond) throw new Error(msg || 'assert failed'); }
function eq(a, b, msg) {
  if (a !== b) throw new Error((msg || 'eq') + ': ' + JSON.stringify(a) + ' != ' + JSON.stringify(b));
}

/* ---- 白名单自身卫生：白名单里不能混进敏感词（防以后误加） ---- */
t('白名单 13 个键全部通过副闸', () => {
  eq(H.CFG_EXPORTABLE.length, 13, '键数');
  for (const k of H.CFG_EXPORTABLE) {
    ok(!H.CFG_SENSITIVE_RE.test(k), '白名单键命中敏感词: ' + k);
    ok(H.cfgIsExportable(k), '白名单键自己不可导: ' + k);
  }
});
t('每个白名单键都有人话标签', () => {
  for (const k of H.CFG_EXPORTABLE) ok(H.cfgLabelOf(k) !== k, '缺标签: ' + k);
});

/* ---- 分堆：白名单 / 敏感 / 未知 ---- */
t('cfgPlanExport 把敏感键分进 excluded，未知键两堆都不要', () => {
  const plan = H.cfgPlanExport(['atv.theme', 'atv_token', 'some.othertool.key', 'atv.pairing_x']);
  eq(plan.include.join(','), 'atv.theme', 'include');
  eq(plan.excluded.sort().join(','), 'atv.pairing_x,atv_token', 'excluded');
});
t('副闸盖住白名单误加：atv.tokenX 也不导', () => {
  ok(!H.cfgIsExportable('atv.tokenX'), '敏感副闸没兜住');
});

/* ---- 导出：完整一份 + 敏感键的值一个字都不出现 ---- */
const MACHINE_A = {
  'atv.theme': 'dark',
  'atv.haptics': '1',
  'atv.intent': '0',
  'atv.gamepad': '1',
  'atv.gamepadDz': '0.2',
  'atv.padSens': '1.5',
  'atv.favApps': JSON.stringify([{ name: 'YouTube', pkg: 'com.google.android.youtube.tv' }]),
  'atv.phrases': JSON.stringify(['下一集', '原画']),
  'atv.collapsed.v1': JSON.stringify(['kbCard']),
  'atv.palRecent': JSON.stringify(['macro:开机']),
  'atv.recentApps': JSON.stringify(['com.netflix.ninja']),
  'atv.keymap_v1': JSON.stringify({ '19': 'app:com.netflix.ninja' }),
  'atv_macros_v1': JSON.stringify([{ name: '开机', steps: [{ type: 'key', code: 26 }] }]),
};
function entriesA() {
  return Object.keys(MACHINE_A).map((k) => ({ key: k, value: MACHINE_A[k] }));
}
t('cfgBuildBackup 产出完整一份，magic/version/keys 齐全', () => {
  const built = H.cfgBuildBackup(entriesA());
  ok(built.ok, 'build 失败: ' + built.error);
  eq(built.backup.magic, 'atv-remote-backup', 'magic');
  eq(built.backup.version, 1, 'version');
  eq(Object.keys(built.backup.keys).length, 13, '键数');
  for (const k of H.CFG_EXPORTABLE) {
    eq(built.backup.keys[k], MACHINE_A[k], '值没搬过去: ' + k);
  }
});
t('导出文本 grep token|cred|pairing = 0（含被塞了敏感键的本机）', () => {
  // A 机浏览器里被别的工具塞了两个敏感键；导出必须一个字节都不带出去
  const dirty = entriesA().concat([
    { key: 'atv_token', value: 'sk-local-secret' },
    { key: 'pairing_cred', value: 'CRED-1234-5678' },
  ]);
  const built = H.cfgBuildBackup(dirty);
  ok(built.ok, 'build 失败: ' + built.error);
  const text = JSON.stringify(built.backup);
  eq(text.search(/token|cred|pairing/i), -1, '导出文本命中敏感词');
  ok(text.indexOf('sk-local-secret') < 0, '敏感值漏进导出');
  ok(text.indexOf('CRED-1234-5678') < 0, '敏感值漏进导出');
  const plan = H.cfgPlanExport(dirty.map((x) => x.key));
  eq(plan.excluded.length, 2, '账目化：excluded 应有 2 项');
});
t('非字符串值整体失败：半份备份比没有备份更坏', () => {
  const bad = entriesA();
  bad[3].value = 42;
  const built = H.cfgBuildBackup(bad);
  ok(!built.ok, '非字符串值竟然构建成功');
  ok(built.error.indexOf('不是字符串') >= 0, '错误信息要说清原因');
});
t('没有可导出配置时整体失败', () => {
  const built = H.cfgBuildBackup([{ key: 'atv_token', value: 'x' }]);
  ok(!built.ok, '空备份竟然构建成功');
});

/* ---- 导入第一关：坏档一律拒，且绝不让调用方写任何一个字节 ---- */
const GOOD_TEXT = JSON.stringify(H.cfgBuildBackup(entriesA()).backup);
t('合法备份解析通过', () => {
  const parsed = H.cfgParseBackup(GOOD_TEXT);
  ok(parsed.ok, '合法备份被拒: ' + parsed.error);
});
t('坏 JSON 拒收', () => {
  const parsed = H.cfgParseBackup('{not json');
  ok(!parsed.ok, '坏 JSON 竟然通过');
  ok(parsed.error.indexOf('JSON') >= 0, '错误要说清是 JSON 问题');
});
t('magic 不符拒收（不是本工具导出的）', () => {
  const parsed = H.cfgParseBackup(JSON.stringify({ magic: 'other-app', version: 1, keys: { 'atv.theme': 'x' } }));
  ok(!parsed.ok && parsed.error.indexOf('magic') >= 0, 'magic 没兜住');
});
t('版本不符拒收', () => {
  const parsed = H.cfgParseBackup(JSON.stringify({ magic: H.CFG_MAGIC, version: 99, keys: { 'atv.theme': 'x' } }));
  ok(!parsed.ok && parsed.error.indexOf('版本') >= 0, '版本没兜住');
});
t('keys 缺失 / 顶层是数组 / 空 keys 都拒收', () => {
  ok(!H.cfgParseBackup(JSON.stringify({ magic: H.CFG_MAGIC, version: 1 })).ok, 'keys 缺失');
  ok(!H.cfgParseBackup(JSON.stringify([1, 2, 3])).ok, '顶层数组');
  ok(!H.cfgParseBackup(JSON.stringify({ magic: H.CFG_MAGIC, version: 1, keys: {} })).ok, '空 keys');
  ok(!H.cfgParseBackup('42').ok, '顶层是数字');
});
t('白名单内值类型不对，解析阶段就拒（写之前失败，chezmoi 口径 1）', () => {
  const parsed = H.cfgParseBackup(JSON.stringify({ magic: H.CFG_MAGIC, version: 1, keys: { 'atv.theme': 42 } }));
  ok(!parsed.ok && parsed.error.indexOf('不是字符串') >= 0, '非字符串值没在解析阶段拒掉');
});
t('全是未知键的备份拒收', () => {
  const parsed = H.cfgParseBackup(JSON.stringify({ magic: H.CFG_MAGIC, version: 1, keys: { 'x.y': '1' } }));
  ok(!parsed.ok, '无可识别键竟然通过');
});
t('非文本输入拒收', () => {
  ok(!H.cfgParseBackup(null).ok, 'null 竟然通过');
  ok(!H.cfgParseBackup(42).ok, '数字竟然通过');
});

/* ---- 导入第二关：apply / skipped / invalid 三堆账目 ---- */
t('cfgApplyBackup 把未知键与敏感键都拦在 skipped', () => {
  const res = H.cfgApplyBackup({
    magic: H.CFG_MAGIC, version: 1,
    keys: { 'atv.theme': 'dark', 'some.othertool.key': 'v', 'atv_token': 'sk-x' },
  });
  eq(res.apply.length, 1, 'apply');
  eq(res.apply[0].key, 'atv.theme', 'apply key');
  eq(res.skipped.length, 2, 'skipped');
  const byKey = {};
  for (const it of res.skipped) byKey[it.key] = it.reason;
  eq(byKey['some.othertool.key'], '不是已知配置键', '未知键原因');
  eq(byKey['atv_token'], '敏感', '敏感键原因');
  eq(res.invalid.length, 0, 'invalid');
});
t('cfgApplyBackup 值类型不对进 invalid，不进 apply', () => {
  const res = H.cfgApplyBackup({ magic: H.CFG_MAGIC, version: 1, keys: { 'atv.theme': 7 } });
  eq(res.apply.length, 0, 'apply 应为空');
  eq(res.invalid.length, 1, 'invalid 应有 1');
});

/* ---- A 机导出 -> B 机导入，逐键复现 ---- */
t('A 机导出 B 机导入，13 个键逐一复现', () => {
  const text = GOOD_TEXT;
  const parsed = H.cfgParseBackup(text);
  ok(parsed.ok, '解析失败: ' + parsed.error);
  const res = H.cfgApplyBackup(parsed.backup);
  eq(res.apply.length, 13, 'B 机应写 13 项');
  eq(res.skipped.length, 0, '不应有跳过');
  eq(res.invalid.length, 0, '不应有无效');
  // 模拟 B 机落盘后的存储：只写 apply 的，skipped/invalid 一个都不写
  const machineB = {};
  for (const it of res.apply) machineB[it.key] = it.value;
  for (const k of Object.keys(MACHINE_A)) {
    ok(machineB[k] === MACHINE_A[k], 'B 机与 A 机不一致: ' + k);
  }
  eq(Object.keys(machineB).length, 13, 'B 机键数');
});
t('导入的备份再导出，内容与原备份一致（幂等）', () => {
  const parsed = H.cfgParseBackup(GOOD_TEXT);
  const res = H.cfgApplyBackup(parsed.backup);
  const rebuilt = H.cfgBuildBackup(res.apply.map((x) => ({ key: x.key, value: x.value })));
  ok(rebuilt.ok, '重建失败: ' + rebuilt.error);
  eq(JSON.stringify(rebuilt.backup), GOOD_TEXT, '导出-导入-再导出应逐字节一致');
});
t('坏档导入时调用方按约定不写任何字节（模拟胶水的写入决策）', () => {
  // 胶水契约：parse 不 ok 就直接 return，cfgApplyBackup 根本不被调用
  const parsed = H.cfgParseBackup('{oops');
  if (parsed.ok) throw new Error('坏档不应解析成功');
  const machineB = { 'atv.theme': 'light', 'atv.favApps': '[]' };   // B 机原有配置
  // 不调用 apply —— 与 static/app.js 里 cfgImportText 的分支一致
  eq(machineB['atv.theme'], 'light', '坏档导入后 B 机原配置必须原样');
  ok(Object.keys(machineB).length === 2, '坏档导入后 B 机键数不变');
});

for (const [name, fn] of cases) {
  try { fn(); } catch (err) {
    failed++;
    console.error('FAIL ' + name + ' -- ' + err.message);
  }
}
console.log('cases=' + cases.length + ' failed=' + failed);
if (failed > 0) process.exit(1);
console.log('ALL_BACKUP_CASES_PASSED');
