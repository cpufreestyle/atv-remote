#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Android TV 媒体状态解析：dumpsys media_session / media volume / dumpsys audio。

从 server.py 外提的**纯函数**：不读全局、不碰 adb、不被测试 monkeypatch，
输入是一段文本或数字、输出是 dict / 元组 / 命令列表。TTL 缓存与
now_playing() / volume() / volume_set() 仍留在 server.py —— 它们要调
adb.shell 并读写缓存字典，搬走会让「改状态必须失效缓存」这条红线变成隐形的。

server.py 用 `from atv_media import ...` 再导出，所以 server.parse_* /
server.STREAM_MUSIC 这类既有引用点（含 tests/）继续可用。
"""

import re

# ---------------- 正在播放（Now Playing） ----------------
# 学习源：Google TV 官方遥控 / Kodi Remote —— 遥控器顶部常驻「正在播放」信息条，
# 用户不用切回电视画面就知道片名和进度。数据源是 dumpsys media_session：唯一
# 不依赖 App 主动配合的系统级接口。
# 各家 ROM 的 dump 格式有差异（state=PLAYING / state=3 / PlaybackState {state=2…}、
# pos= 与 position= 混用、duration 单位为 ms），所以解析器刻意宽松：认不出的字段
# 就缺省，前端据此隐藏卡片，绝不因为格式变化把整个遥控器搞挂。
MEDIA_STATE_NAMES = {
    0: "none", 1: "stopped", 2: "paused", 3: "playing",
    4: "fast_forwarding", 5: "rewinding", 6: "buffering",
    7: "error", 8: "connecting", 9: "skipping_prev", 10: "skipping_next",
}


def parse_media_session(text):
    """从 dumpsys media_session 输出抠「正在播放」。字段取不到就缺省（≠ 出错）：
    app / title / artist / duration(ms) / position(ms) / playing / empty"""
    t = text or ""
    r = {"playing": False, "title": "", "artist": "", "app": "",
         "duration": 0, "position": 0, "empty": True}

    def first(pat):
        m = re.search(pat, t, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else ""

    # 会话栈是按活跃度排序的，取首个记录 = 当前前台会话；每个字段也只取首个命中
    r["app"] = (first(r"^\s*packageName\s*=\s*(\S+)")
                or first(r"^\s*pkg\s*=\s*(\S+)"))
    r["title"] = (first(r"^\s*title\s*=\s*(.+)$")
                  or first(r"^\s*displayTitle\s*=\s*(.+)$"))
    r["artist"] = first(r"^\s*artist\s*=\s*(.+)$")
    dur = first(r"(?<![A-Za-z])duration\s*=\s*(\d+)")
    pos = first(r"\b(?:pos|position)\s*=\s*(\d+)")
    state = (first(r"\bstate\s*=\s*([A-Za-z0-9_]+)")
             or first(r"\bmState\s*=\s*(\d+)"))
    if dur.isdigit():
        r["duration"] = int(dur)
    if pos.isdigit():
        r["position"] = int(pos)
    if state:
        if state.isdigit():
            r["playing"] = MEDIA_STATE_NAMES.get(int(state)) == "playing"
        else:
            r["playing"] = state.upper() == "PLAYING"
    if r["title"] or r["app"] or r["playing"]:
        r["empty"] = False
    return r


# ---------------- 音量（两条读通道） ----------------
# 按音量键后手机端要立刻有反馈（学 Google TV 官方遥控的本地 OSD）：电视自己也会弹
# 系统音量条，但 adb 发键是「发完就走」，手机端一片安静——用户不知道键送到没有。
# 两条读通道，都不保证存在，认不出就安静降级（supported: False），绝不抛：
#   1. `media volume --stream 3 --get`（新系统：volume is 7 in range [0..15]）
#   2. `dumpsys audio` 的 STREAM_MUSIC 段（Current: 7，老 ROM 兜底）
#   `muted` 三态：True/False 只有 dumpsys audio 读得出来；`media volume --get`
#   不带静音位，走这条通道时 muted=None（「不知道」）。前端据此保留本地乐观值——
#   否则按静音后 220ms 的真值校正会把「静音」抹成格数，反馈等于没有。
STREAM_MUSIC = 3                      # Android 媒体流固定 3（音乐/视频/游戏都走它）


def parse_media_volume(text) -> dict:
    """`media volume --get` 输出 → {"level", "max"}；认不出返回 {}。"""
    m = re.search(r"volume\s+is\s+(\d+)\s+in\s+range\s*\[0\.\.(\d+)\]",
                  text or "", re.IGNORECASE)
    return {"level": int(m.group(1)), "max": int(m.group(2))} if m else {}


def parse_dumpsys_audio(text) -> dict:
    """dumpsys audio 的 STREAM_MUSIC 段 → {"level", "max"?, "muted"?}。

    段头 `- STREAM_MUSIC:`（各 ROM 缩进不一），块内 `Current: 7, Latest: 7`；
    部分 ROM 还带 `Max: 15` / `Muted: true`（或 `Mute count: 1`）。字段缺省就缺，
    别抛——这条通道本身就是给新系统 `media` 命令兜底的。"""
    m = re.search(r"-\s*STREAM_MUSIC\s*:(.*?)(?=\n\s*-\s*STREAM_[A-Z]|\Z)",
                  text or "", re.DOTALL | re.IGNORECASE)
    if not m:
        return {}
    seg = m.group(1)
    cur = re.search(r"\bCurrent\s*:\s*(\d+)", seg)
    if not cur:
        return {}
    r = {"level": int(cur.group(1))}
    mx = re.search(r"\bMax\s*:\s*(\d+)", seg)
    if mx:
        r["max"] = int(mx.group(1))
    if re.search(r"\bMuted\s*[:=]\s*true\b", seg, re.IGNORECASE) or \
            re.search(r"\bMute\s+count\s*:\s*[1-9]", seg):
        r["muted"] = True
    return r


# ---------------- 音量精确设置（按格） ----------------
# 学习源：androidtv 0.0.75（MIT，Copyright (c) 2020 Jeff Irion，commit 343b74e）——
# Home Assistant 遥控 Android TV 的协议库。此前本项目音量链路是「按键 + 只读 OSD」，
# 想一步调到某一格只能连按；按格设置的完整答案 androidtv 已经踩过一遍，三条口径：
#   1) constants.py:145/148 两条 set 命令：旧系统
#      `media volume --show --stream 3 --set N`，Android 11+ 是
#      `cmd media_session volume --show --stream 3 --set N`。它按 sw_version 二选一
#      （basetv.py:280-284）；我们不多花一次 build.prop 查询，改成有序降级：读通道
#      已在用的 `media volume` 先试，抛 AdbError 再试 `cmd media_session volume`。
#   2) basetv_async.py:830 的夹取公式 int(min(max(round(x), 0.0), max_volume))：
#      先 round 再夹。只夹不舍会把 7.6 卡成 7 再跳 8，只舍不夹会放出 16 / -1，
#      电视侧命令直接报错。max 也按不可信输入处理（读通道挂掉时前端兜底 15）。
#   3) max_volume 拿不到就先去 volume() 读一次，仍拿不到直接放弃（basetv_async.py:825-828）。
#      这一条落在 server.volume_set() 里：读通道全废时 POST 报错，让前端退回音量键——
#      24/25 在任何 ROM 上都认，这才是老设备上的降级方向，不是猜一个格数发出去。
VOLUME_MAX_FALLBACK = 15   # 读不到 max 时的兜底格数（STREAM_MUSIC 常见上限）


def _vol_finite_int(value):
    """把入参 round 成整数；非数字 / NaN / inf 一律返回 None（调用方按「不可信」处理）。

    JSON body 里的 level 理论上只会是数字，但网关/脚本可能塞进任意类型；
    int(float("inf")) 抛的是 OverflowError 而不是 ValueError，单独兜住。
    舍入口径是**半点向上**（int(x + 0.5)）：Python3 内建 round 是银行家舍入
    （8.5→8），前端 Math.round 是 8.5→9，直接用内建 round 会造出「滑条停在 9、
    电视设成 8」的错位；直接 int() 截断则会把 7.6 卡成 7（先 round 后夹的另一半）。"""
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if num != num or num == float("inf") or num == float("-inf"):
        return None
    return int(num + 0.5) if num >= 0 else -int(-num + 0.5)


def clamp_volume_level(level, max_level):
    """把目标格数 round 后夹到 [0, max_level]，返回 (level, max)。

    round 与 clamp 的顺序照抄 androidtv basetv_async.py:830 的
    int(min(max(round(...), 0.0), max_volume))。max_level 不可信（缺省 / 0 /
    非数字）时退到 VOLUME_MAX_FALLBACK——滑条的 max 来自上一次回读，可能是旧值。
    level 不可信时按 0 处理（不抛，让调用方按业务决定）。"""
    top = _vol_finite_int(max_level)
    if top is None or top <= 0:
        top = VOLUME_MAX_FALLBACK
    lvl = _vol_finite_int(level)
    if lvl is None:
        # NaN / inf / 非数字：合法 JSON 到不了这里，但绝不猜一个格数发出去
        lvl = 0
    return max(0, min(lvl, top)), top


def volume_set_cmd(level: int):
    """设置音量的两条候选命令（有序降级链）。学 androidtv constants.py:145/148：
    `media volume` 是读通道已在用的同款命令先试，Android 11+ 的
    `cmd media_session volume` 兜底。"""
    return [
        "media volume --show --stream %d --set %d" % (STREAM_MUSIC, level),
        "cmd media_session volume --show --stream %d --set %d" % (STREAM_MUSIC, level),
    ]
