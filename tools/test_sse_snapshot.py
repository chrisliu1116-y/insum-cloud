# -*- coding: utf-8 -*-
"""test_sse_snapshot.py — 上证日K生成的三条护栏。

跑法：python tools/test_sse_snapshot.py
不联网：monkeypatch 掉 fetch_index_bars 与 fetch_spot_bar_000001。

覆盖：
  ① 日K已含当日                          → to_src=kline
  ② 日K缺当日 + 快照可用                  → 用收盘快照补一根，to_src=kline+snapshot，meta.spot 可审计
  ③ 日K缺当日 + 快照也旧（收盘非法）      → 拒绝写入（不产出半新半旧）
  ④ 日K缺当日 + 快照不可用                → 拒绝写入
  ⑤ 抓到个股（收盘 11.71）                → 拒绝写入（标的不对）
  ⑥ f86 跨日（快照日晚于交易日）+ 收盘有效 → 快照覆盖当日，仍 to_src=kline+snapshot
  ⑦ 快照日期滞后（早于交易日 1 天、同周）  → 快照补当日根，仍 to_src=kline+snapshot
  ⑧ 日K已含当日 + 快照完全不可用          → to_src=kline、无 spot，且有明确 log 说明原因
  ⑨ sse_is_valid()：旧版 kline（今天/无 spot）→ 无效；快照版 → 有效
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import breadth_run as R  # noqa: E402

PASS = FAIL = 0
# 最近一次 run_case 里 write_sse 打出的 log（monkeypatch R.log 收集），供 ⑧ 校验
LAST_LOGS = []


def ok(cond, name, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  OK   ' + name + (('  | ' + str(extra)) if extra != '' else ''))
    else:
        FAIL += 1
        print('  FAIL ' + name + (('  | ' + str(extra)) if extra != '' else ''))


SPOT_OK = {"d": "2026-11-11", "o": 3655.66, "c": 3680.64, "h": 3689.65,
           "l": 3650.31, "v": 4.33e8, "a": 7.36e11,
           "_ts": "2026-11-11 16:11:58", "_src": "spot"}

# ⑥ f86 跨日：行情源时间戳跳到次日凌晨，sp["d"] != breadth 交易日，但收盘值有效
SPOT_CROSS_DAY = {"d": "2026-11-12", "o": 3655.66, "c": 3675.30, "h": 3689.65,
                  "l": 3650.31, "v": 4.33e8, "a": 7.36e11,
                  "_ts": "2026-11-12 00:05:00", "_src": "spot"}

# ⑦ 快照日期滞后：f86 停在上一交易日（早于 breadth 交易日 1 天，仍同周），收盘有效
SPOT_LAG = {"d": "2026-11-10", "o": 3655.66, "c": 3680.64, "h": 3689.65,
            "l": 3650.31, "v": 4.33e8, "a": 7.36e11,
            "_ts": "2026-11-10 16:11:58", "_src": "spot"}


def bars_until(last_date, last_close=3680.64):
    """1990-12-19 起点（过护栏）+ 2026-11-10 + 可选当日。"""
    out = [{"d": "1990-12-19", "o": 96.05, "c": 99.98, "h": 99.98, "l": 96.05,
            "v": 1.0e5, "a": 1.0e7},
           {"d": "2026-11-10", "o": 3600.0, "c": 3610.0, "h": 3620.0, "l": 3590.0,
            "v": 4.0e8, "a": 7.0e11}]
    if last_date != "2026-11-10":
        out.append({"d": last_date, "o": 3655.66, "c": last_close, "h": 3689.65,
                    "l": 3650.31, "v": 4.33e8, "a": 7.36e11})
    return out


def run_case(tag, kline, spot, expect_days, expect_src, expect_raise=False):
    tmp = tempfile.mkdtemp()
    bpath = os.path.join(tmp, 'breadth.json')
    spath = os.path.join(tmp, 'sse.json')
    with open(bpath, 'w', encoding='utf-8') as f:
        json.dump({"meta": {}, "dates": ["2026-11-10", "2026-11-11"]}, f)

    orig_k, orig_s, orig_log = R.fetch_index_bars, R.fetch_spot_bar_000001, R.log
    R.fetch_index_bars = lambda *a, **k: kline
    R.fetch_spot_bar_000001 = lambda: spot
    LAST_LOGS.clear()
    R.log = lambda *a: LAST_LOGS.append(' '.join(str(x) for x in a))
    err = ''
    try:
        R.write_sse(spath, bpath)
        raised = False
    except RuntimeError as e:
        raised = True
        err = str(e)
    finally:
        R.fetch_index_bars, R.fetch_spot_bar_000001, R.log = orig_k, orig_s, orig_log

    if expect_raise:
        ok(raised, f'{tag}：拒绝写入', err if raised else '未抛错')
        return None
    ok(not raised, f'{tag}：正常写出', err)
    if raised:
        return None
    d = json.load(open(spath, encoding='utf-8'))
    ok(d['meta']['days'] == expect_days, f'{tag}：根数 {expect_days}', d['meta']['days'])
    ok(d['meta'].get('to_src') == expect_src, f'{tag}：to_src={expect_src}', d['meta'].get('to_src'))
    ok(d['dates'][-1] == '2026-11-11', f'{tag}：末日=2026-11-11', d['dates'][-1])
    return d


print('===== ① 日K已含当日（正常路径） =====')
d = run_case('kline-full', bars_until('2026-11-11'), None, 3, 'kline')
if d:
    ok('spot' not in d['meta'], '不写 spot 字段')
    ok(abs(d['c'][-1] - 3680.64) < 0.01, '末根收盘=3680.64', d['c'][-1])

print('===== ② 日K缺当日 + 快照可用（补根） =====')
d = run_case('snapshot-fill', bars_until('2026-11-10'), SPOT_OK, 3, 'kline+snapshot')
if d:
    ok(d['meta']['spot']['spot_ts'] == '2026-11-11 16:11:58',
       'meta.spot.spot_ts 可审计', d['meta']['spot']['spot_ts'])
    ok(abs(d['c'][-1] - 3680.64) < 0.01, '补进来的收盘=3680.64', d['c'][-1])
    ok(abs(d['o'][-1] - 3655.66) < 0.01, '补进来的开盘=3655.66', d['o'][-1])

print('===== ③ 日K缺当日 + 快照也是昨天（拒绝） =====')
run_case('snapshot-stale', bars_until('2026-11-10'),
         {"d": "2026-11-10", "o": 1, "c": 1, "h": 1, "l": 1, "v": 0, "a": 0,
          "_ts": "2026-11-10 15:00:00"}, 0, '', expect_raise=True)

print('===== ④ 日K缺当日 + 快照不可用（拒绝） =====')
run_case('snapshot-none', bars_until('2026-11-10'), None, 0, '', expect_raise=True)

print('===== ⑤ 抓到个股（收盘 11.71）→ 拒绝 =====')
run_case('wrong-symbol', bars_until('2026-11-11', last_close=11.71), None, 0, '',
         expect_raise=True)

print('===== ⑥ f86 跨日（快照日 11-12 晚于交易日）但收盘有效 → 仍 kline+snapshot =====')
d = run_case('snapshot-cross-day', bars_until('2026-11-11'), SPOT_CROSS_DAY, 3,
             'kline+snapshot')
if d:
    ok(d['dates'][-1] == '2026-11-11',
       '末日对齐 breadth 交易日 11-11（不是快照日 11-12）', d['dates'][-1])
    ok(abs(d['c'][-1] - 3675.30) < 0.01, '当日那根用收盘快照 3675.30 覆盖', d['c'][-1])
    ok(d['meta']['spot']['spot_d'] == '2026-11-12',
       'meta.spot.spot_d 保留快照原始日期（可审计）', d['meta']['spot']['spot_d'])

print('===== ⑦ 快照日期滞后（11-10 早于交易日 1 天、同周）但有效 → 仍 kline+snapshot =====')
d = run_case('snapshot-lag', bars_until('2026-11-10'), SPOT_LAG, 3, 'kline+snapshot')
if d:
    ok(d['dates'][-1] == '2026-11-11', '补进来的那根日期=交易日 11-11', d['dates'][-1])
    ok(abs(d['c'][-1] - 3680.64) < 0.01, '补进来的收盘=3680.64', d['c'][-1])
    ok(d['meta']['spot']['spot_d'] == '2026-11-10',
       'meta.spot.spot_d 保留滞后日期（可审计）', d['meta']['spot']['spot_d'])

print('===== ⑧ 快照完全不可用（日K已含当日）→ 明确 log + 不误标 to_src =====')
d = run_case('snapshot-unavailable', bars_until('2026-11-11'), None, 3, 'kline')
if d:
    ok('spot' not in d['meta'], '不写 spot 字段（不误标 kline+snapshot）')
    ok(any('收盘快照不可用' in x for x in LAST_LOGS),
       '有明确 log 说明快照不可用原因', LAST_LOGS)

print('===== ⑨ sse_is_valid()：旧版 kline（今天/无 spot）必须判无效 =====')


def sse_valid_check(meta, closes, today='2026-11-11'):
    """写一份临时 sse.json，返回 R.sse_is_valid(...) 的结果。"""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, 'sse.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({"meta": meta, "c": closes}, f)
        return R.sse_is_valid(p, today)


SNAP_META = {"to": "2026-11-11", "to_src": "kline+snapshot",
             "spot": {"spot_d": "2026-11-11", "spot_ts": "2026-11-11 16:11:58",
                      "spot_close": 3680.64}}

ok(sse_valid_check({"to": "2026-11-11", "to_src": "kline"},
                   [99.98, 3680.64]) is False,
   '旧版 kline（今天/收盘合法/无 spot）→ False（幂等闸触发补跑）')
ok(sse_valid_check(SNAP_META, [99.98, 3680.64]) is True,
   '快照版（含 meta.spot）→ True')
ok(sse_valid_check({"to": "2026-11-11", "to_src": "kline+snapshot"},
                   [99.98, 3680.64]) is True,
   '快照版（仅 to_src=kline+snapshot、无 spot）→ True')
ok(sse_valid_check(SNAP_META, [99.98, 3680.64], today='2026-11-10') is False,
   'meta.to != 传入 today → False')
ok(sse_valid_check(SNAP_META, [99.98, 11.71]) is False,
   '末收盘非指数点位量级 → False')
with tempfile.TemporaryDirectory() as _tmp:
    ok(R.sse_is_valid(os.path.join(_tmp, 'missing.json'), '2026-11-11') is False,
       'sse.json 缺失/损坏 → False')

print('\n结果：%d 通过 / %d 失败' % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
