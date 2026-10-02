#!/usr/bin/env node
/* 手柄当 D-pad（第三十五轮规则段 + 第三十六轮按钮接线）纯函数 harness：从 static/app.js
   抽出 gamepad 段原样执行。Python 单测 tests/test_gamepad.py 通过 subprocess 调它——
   轴翻方向键、以及一帧翻成哪些键的规则因此有真行为证据，不只是源码字符串断言。
   改动 app.js 里段边界标记会让这里直接失败。
   期望值按 docs/opensource-references.md 第 10 节（xorg / qjoypad）与第 11 节（xboxdrv）的
   出处手算，不是抄一遍实现。 */
'use strict';
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
const BEGIN = '/* ===== gamepad:begin';
const END = '/* ===== gamepad:end';
const s = src.indexOf(BEGIN), e = src.indexOf(END);
if (s < 0 || e < 0 || e < s) {
  console.error('harness: gamepad 段标记缺失或顺序错误');
  process.exit(2);
}
const seg = src.slice(s, e);
const BANNED = ['document.', 'localStorage', 'fetch(', '$(', 'innerHTML', 'setInterval', 'navigator.'];
for (let i = 0; i < BANNED.length; i++) {
  if (seg.indexOf(BANNED[i]) >= 0) {
    console.error('harness: 纯函数段里出现 ' + BANNED[i]);
    process.exit(2);
  }
}
const NL = String.fromCharCode(10);
const factory = new Function(seg + NL +
  'return { gpNum: gpNum, gpClampDeadzone: gpClampDeadzone, gpResidual: gpResidual,' +
  ' gpDirOf: gpDirOf, gpStep: gpStep, gpAxisEvents: gpAxisEvents, gpArbitrate: gpArbitrate,' +
  ' gpPwmCycle: gpPwmCycle, gpRepeatMs: gpRepeatMs, gpNormPad: gpNormPad,' +
  ' gpBtnDir: gpBtnDir, gpDirKey: gpDirKey, gpFrame: gpFrame, gpNextFireMs: gpNextFireMs,' +
  ' DEAD: GP_DEADZONE_DEFAULT, PHASE: GP_PWM_MIN_PHASE_MS, HOLDMS: GP_PWM_HOLD_MS,' +
  ' RMIN: GP_REPEAT_MIN_MS, RMAX: GP_REPEAT_MAX_MS, FIRSTMS: GP_REPEAT_FIRST_MS };');
const H = factory();

let failed = 0;
const cases = [];
function t(name, fn) { cases.push([name, fn]); }
function ok(cond, msg) { if (!cond) throw new Error(msg || 'assert failed'); }
function near(a, b, tol, msg) {
  if (!(Math.abs(a - b) <= tol)) throw new Error((msg || 'near') + ': ' + a + ' != ' + b);
}

const D = 0.15;   /* = GP_DEADZONE_DEFAULT，xorg 5000/32768 = 0.1526 */

/* ---- 死区夹取：只在调皮皮时兜底，不收 0 ---- */
t('gpClampDeadzone 把非正数与 NaN 拉回默认', () => {
  ok(H.gpClampDeadzone(0) === D, '0 不该把死区关掉');
  ok(H.gpClampDeadzone(-3) === D, '负数同理');
  ok(H.gpClampDeadzone(NaN) === D, 'NaN 同理');
  ok(H.gpClampDeadzone(undefined) === D, '缺省同理');
});
t('gpClampDeadzone 夹在 0.02..0.5 并对齐两位小数', () => {
  ok(H.gpClampDeadzone(0.02) === 0.02, '下界原样');
  ok(H.gpClampDeadzone(0.001) === 0.02, '低于下界抬回去');
  ok(H.gpClampDeadzone(0.9) === 0.5, '高于上界压回来');
  ok(H.gpClampDeadzone(0.1526) === 0.15, '换基后取两位');
});

/* ---- 死区 + 重标度：(|v|-dz)/(1-dz) ---- */
t('gpResidual 死区内一律 0', () => {
  ok(H.gpResidual(0, D) === 0, '正中心');
  ok(H.gpResidual(0.14, D) === 0, '差一点顶出');
  ok(H.gpResidual(-0.14, D) === 0, '负方向同罪');
  ok(H.gpResidual(0.14999, D) === 0, '小于才算死区');
});
t('gpResidual 死区外重标度回 0..1', () => {
  near(H.gpResidual(0.575, D), 0.5, 1e-9, '半推正好在正中间');
  near(H.gpResidual(1, D), 1.0, 1e-9, '满推顶到 1');
  near(H.gpResidual(0.5, D), 0.4117647058823529, 1e-12, '即 0.35 除以 0.85');
  ok(H.gpResidual(3, D) === 1, '越界夹住，不给 NaN 下游');
  near(H.gpResidual(-0.575, D), 0.5, 1e-9, '左右对称');
});
t('gpNum 把一切非数当 0', () => {
  ok(H.gpNum(NaN) === 0 && H.gpNum('x') === 0, '字符串也当 0');
  ok(H.gpNum(Infinity) === 0 && H.gpNum(null) === 0, 'Infinity 与 null');
  ok(H.gpNum(0.25) === 0.25, '正常值原样');
});

/* ---- 方向判定与单步边沿 ---- */
t('gpDirOf 阈值等于死区算顶出', () => {
  ok(H.gpDirOf(D, D) === 1, '驱动判死区用小于号，等于不算在死区内');
  ok(H.gpDirOf(-D, D) === -1, '负方向同理');
  ok(H.gpDirOf(0.1499, D) === 0, '差一丝就还不算');
});
t('gpStep 只在方向真的变了时置 changed', () => {
  const a = H.gpStep(0.4, D, 1);
  ok(a.dir === 1 && a.changed === false, '一直按着就不该重发');
  const b = H.gpStep(0, D, 1);
  ok(b.dir === 0 && b.prev === 1 && b.changed === true, '回中是一次边沿');
  const c = H.gpStep(-0.4, D, 1);
  ok(c.dir === -1 && c.prev === 1 && c.changed === true, '跳到反方向也是边沿');
  ok(H.gpStep(0.4, D, 'x').prev === 0, 'held 不是正负一就当从未按过');
});

/* ---- 边沿触发：qjoypad jsevent 的没变就 return ---- */
t('gpAxisEvents 稳定段只出两头两个事件', () => {
  const ev = H.gpAxisEvents([0, 0, 0.3, 0.3, 0.2, 0], D);
  ok(ev.length === 2, '按住期间一行都不该出，实际 ' + ev.length);
  ok(ev[0].i === 2 && ev[0].dir === 1 && ev[0].press === true, '顶出即按下');
  ok(ev[1].i === 5 && ev[1].dir === 1 && ev[1].press === false, '回中即松开');
});
t('gpAxisEvents 跳过死区时先松后按（qjoypad 在这里会卡住）', () => {
  const ev = H.gpAxisEvents([0, 0.5, -0.5, 0], D);
  ok(ev.length === 4, '跳变一次要摊成松加按两条，实际 ' + ev.length);
  ok(ev[0].i === 1 && ev[0].dir === 1 && ev[0].press === true, '先按正');
  ok(ev[1].i === 2 && ev[1].dir === 1 && ev[1].press === false, '再松正');
  ok(ev[2].i === 2 && ev[2].dir === -1 && ev[2].press === true, '同一个采样里按负');
  ok(ev[3].i === 3 && ev[3].dir === -1 && ev[3].press === false, '最后松负');
});
t('gpAxisEvents 全程无关事件', () => {
  ok(H.gpAxisEvents([0, 0.1, -0.1, 0.14], D).length === 0, '全在死区内');
  ok(H.gpAxisEvents(null, D).length === 0, '非数组不炸');
  ok(H.gpAxisEvents([0.5], D).length === 1, '单采样也能出一行');
});

/* ---- 双轴抢一个 D-pad：迟滞换轴 ---- */
t('gpArbitrate 都没顶出时不占轴', () => {
  const a = H.gpArbitrate(0, 0, D, null);
  ok(a.axis === null && a.dir === 0, '空的');
  const b = H.gpArbitrate(0, 0, D, 'x');
  ok(b.axis === null && b.dir === 0, '持着中的轴回中后就该放手');
  const c = H.gpArbitrate(0, 0.1, D, 'y');
  ok(c.axis === null && c.dir === 0, 'y 只剩一点残差也不算');
});
t('gpArbitrate 首次按残差大的那根', () => {
  const a = H.gpArbitrate(0.5, 0, D, null);
  ok(a.axis === 'x' && a.dir === 1, '只有 x 顶出');
  const b = H.gpArbitrate(0.2, 0.8, D, null);
  ok(b.axis === 'y' && b.dir === 1, 'y 残差大');
  const c = H.gpArbitrate(-0.4, 0, D, null);
  ok(c.axis === 'x' && c.dir === -1, '负方向也要报对');
});
t('gpArbitrate 对手要高出余量才抢得走', () => {
  const a = H.gpArbitrate(0.5, 0.5, D, 'x');
  ok(a.axis === 'x' && a.dir === 1, '一样大就留在原轴上，别抖');
  const b = H.gpArbitrate(0.5, 0.58, D, 'x');
  ok(b.axis === 'x', '0.58 折成残差 0.506，没到 0.412 乘 1.25，抢不走');
  const c = H.gpArbitrate(0.2, 0.8, D, 'x');
  ok(c.axis === 'y' && c.dir === 1, '明显压过去才换');
});
t('gpArbitrate 当前轴回中时无条件让位', () => {
  const a = H.gpArbitrate(0, 0.5, D, 'x');
  ok(a.axis === 'y' && a.dir === 1, '不要求余量，否则键卡住');
  const b = H.gpArbitrate(0, 0, D, 'x');
  ok(b.axis === null && b.dir === 0, '两边都回中就全松');
  const c = H.gpArbitrate(-0.4, 0, D, 'y');
  ok(c.axis === 'x' && c.dir === -1, '从 y 让到 x 的负向');
});

/* ---- 占空比：50ms 下限与 600ms 截止，逐字照 xorg jstk_axis.c:518-551 ---- */
t('gpPwmCycle 两相 50ms 下限', () => {
  const c0 = H.gpPwmCycle(D + 0.5 * (1 - D), D);   /* 残差正好 0.5 */
  near(c0.onMs, 50, 1e-9, '残差一半时两相都正好 50ms');
  near(c0.offMs, 50, 1e-9, '同上');
  ok(c0.hold === null, '没到截止，正常翻转');
  const c1 = H.gpPwmCycle(D + 0.3 * (1 - D), D);   /* 残差 0.3 */
  near(c1.onMs, 50, 1e-9, '小的一相永远是 50ms');
  near(c1.offMs, 114.51612903225806, 1e-9, '0.71 除 0.31 再乘 50');
  const c2 = H.gpPwmCycle(D + 0.7 * (1 - D), D);   /* 残差 0.7，对称 */
  near(c2.offMs, 50, 1e-9, '翻过来小的是 off');
  near(c2.onMs, 114.51612903225806, 1e-9, '一样对称');
});
t('gpPwmCycle 600ms 截止判 up 与 down', () => {
  const up = H.gpPwmCycle(0, D);
  near(up.offMs, 5050, 1e-9, '回中：off 相 5050ms');
  near(up.onMs, 50, 1e-9, 'on 相 50ms');
  ok(up.hold === 'up', '超 600ms 就当一直松着');
  const down = H.gpPwmCycle(1, D);
  near(down.onMs, 5050, 1e-9, '满推：on 相 5050ms');
  ok(down.hold === 'down', '超 600ms 就当一直按着');
  const soft = H.gpPwmCycle(D + 0.95 * (1 - D), D);
  near(soft.onMs, 800, 1e-9, '残差 0.95 时 on 相 800ms');
  ok(soft.hold === 'down', '刚过截止');
  const firm = H.gpPwmCycle(D + 0.9 * (1 - D), D);
  near(firm.onMs, 413.6363636363636, 1e-9, '残差 0.9 时还没过截止');
  ok(firm.hold === null, '0.91 除 0.11 再乘 50');
});
t('gpRepeatMs 残差越大越密，死区内不给数', () => {
  ok(H.gpRepeatMs(0, D) === null, '死区内不该重复');
  ok(H.gpRepeatMs(0.1, D) === null, '没顶出同理');
  ok(H.gpRepeatMs(1, D) === H.RMIN, '满推最快');
  ok(H.gpRepeatMs(D + 0.5 * (1 - D), D) === 330, '残差一半：600 减 540 乘 0.5');
  ok(H.gpRepeatMs(D + 0.1 * (1 - D), D) === 546, '残差 0.1：600 减 54');
  ok(H.gpRepeatMs(-1, D) === H.RMIN, '负方向同速');
  ok(H.gpRepeatMs(3, D) === H.RMIN, '越界夹住');
});

/* ---- 一帧归一：摇杆 NaN 与缺键不许变成假输入（第三十六轮，xboxdrv §11） ---- */
t('gpNormPad 缺轴缺键一律兜底成 0 与 false', () => {
  const a = H.gpNormPad(null);
  ok(a.x === 0 && a.y === 0, '整段缺失时轴给 0');
  ok(!a.up && !a.down && !a.left && !a.right && !a.a && !a.back, '键全松');
  const b = H.gpNormPad({});
  ok(b.x === 0 && b.y === 0 && !b.a, '空对象同理');
});
t('gpNormPad NaN 轴与稀疏按钮数组不当按下', () => {
  const bt = [];
  bt[0] = { pressed: true };
  bt[12] = { pressed: true };
  const p = H.gpNormPad({ axes: [NaN, 'x'], buttons: bt });
  ok(p.x === 0 && p.y === 0, 'NaN 与字符串当 0');
  ok(p.a === true, '0 号键 = A');
  ok(p.up === true && !p.down && !p.left && !p.right, '12..15 = 十字键，缺省即松');
  ok(H.gpNormPad({ axes: [0.5, -0.5], buttons: [] }).up === false, '空按钮数组全松');
});

/* ---- 斜按消斜：竖直胜（four_way_restrictor 平手判 X 清零照搬） ---- */
t('gpBtnDir 斜按时竖直胜，平手也判 Y', () => {
  ok(H.gpBtnDir(false, false, false, false) === null, '一个没按');
  ok(H.gpBtnDir(true, false, false, true) === 'up', '上 + 右 判上');
  ok(H.gpBtnDir(true, true, true, false) === 'up', '上 + 下 + 左 也判上');
  ok(H.gpBtnDir(false, true, true, true) === 'down', '不占上就判下');
  ok(H.gpBtnDir(false, false, true, true) === 'left', '只剩水平判左');
  ok(H.gpBtnDir(undefined, undefined, undefined, undefined) === null, '缺省不是按下');
});

/* ---- 方向 -> 键码：乱值给 0，别把 undefined 喂给 keyevent ---- */
t('gpDirKey 四向给 Android 键码，乱值给 0', () => {
  ok(H.gpDirKey('up') === 19, '上');
  ok(H.gpDirKey('down') === 20, '下');
  ok(H.gpDirKey('left') === 21, '左');
  ok(H.gpDirKey('right') === 22, '右');
  ok(H.gpDirKey('diag') === 0, '未知方向');
  ok(H.gpDirKey(null) === 0, 'null 不是 number');
  ok(H.gpDirKey('constructor') === 0, '原型链上的键也不是自定义键');
});

/* ---- 单一出口：物理十字键按着时摇杆整帧让位（uinput_options dpad_as_button） ---- */
t('gpFrame 十字键按着时摇杆整帧让位', () => {
  const a = H.gpFrame({ x: 0.9, y: 0, up: true }, D, 'x');
  ok(a.dir === 'up' && a.axis === null && a.val === 0, '摇杆推到 right 也不作数');
  const b = H.gpFrame({ x: 0, y: -0.9, down: true }, D, null);
  ok(b.dir === 'down' && b.axis === null, '竖直同理');
  const c = H.gpFrame({ x: 0, y: 0, left: true, right: true }, D, 'y');
  ok(c.dir === 'left', '左右同时按判左');
});
t('gpFrame 摇杆四向进 D-pad，回中一个键都不发', () => {
  const r = H.gpFrame({ x: 0.5, y: 0 }, D, null);
  ok(r.dir === 'right' && r.axis === 'x' && r.val === 0.5, '右：val 是原始轴值');
  const d = H.gpFrame({ x: 0, y: 0.5 }, D, null);
  ok(d.dir === 'down' && d.axis === 'y' && d.val === 0.5, '下');
  const l = H.gpFrame({ x: -0.5, y: 0 }, D, 'x');
  ok(l.dir === 'left' && l.axis === 'x' && l.val === -0.5, '左，符号要留着');
  const u = H.gpFrame({ x: 0, y: -0.5 }, D, 'y');
  ok(u.dir === 'up' && u.axis === 'y', '上');
  const z = H.gpFrame({ x: 0, y: 0 }, D, 'x');
  ok(z.dir === null && z.axis === null && z.val === 0, '回中停发');
  ok(H.gpFrame(null, D, 'x').dir === null, '快照缺失也不炸');
});

/* ---- 连发节拍：首发不计延时，之后每 rate 一拍 ---- */
t('gpNextFireMs 首发不等延时，之后交给 rate', () => {
  ok(H.gpNextFireMs(0, 120) === 300, '刚按下算的是首发间隔，不并入延时');
  ok(H.gpNextFireMs(299, 120) === 1, '延时没走完就倒数');
  ok(H.gpNextFireMs(300, 120) === 120, '走完延时换 rate');
  ok(H.gpNextFireMs(1000, 120) === 120, '之后每 120ms 一拍，不累计');
  ok(H.gpNextFireMs(-50, 120) === 300, '负耗时当 0');
});
t('gpNextFireMs 速率非法退回 GP_REPEAT_MIN_MS', () => {
  ok(H.gpNextFireMs(300, NaN) === H.RMIN, 'NaN 退下限');
  ok(H.gpNextFireMs(300, 0) === H.RMIN, '0 退下限');
  ok(H.gpNextFireMs(300, -8) === H.RMIN, '负数退下限');
  ok(H.gpNextFireMs(300, 'x') === H.RMIN, '字符串退下限');
  ok(H.gpNextFireMs(300, 2000) === 2000, '拖到最重反而最慢，不自欺');
});

for (let i = 0; i < cases.length; i++) {
  const name = cases[i][0], fn = cases[i][1];
  try {
    fn();
    console.log(JSON.stringify({ name: name, ok: true }));
  } catch (err) {
    failed++;
    console.log(JSON.stringify({ name: name, ok: false, err: String(err && err.message) }));
  }
}
console.log(JSON.stringify({ total: cases.length, failed: failed }));
if (failed > 0) process.exit(1);
console.log('ALL_GAMEPAD_CASES_PASSED');
