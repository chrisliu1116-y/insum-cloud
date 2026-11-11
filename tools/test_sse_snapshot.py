# -*- coding: utf-8 -*-
"""test_sse_snapshot.py — 上证日K生成的三条护栏。

跑法：python tools/test_sse_snapshot.py
不联网：monkeypatch 掉 fetch_index_bars 与 fetch_spot_bar_000001。

覆盖：
  ① 日K已含当日            → to_src=kline
  ② 日K缺当日 + 快照可用    → 用收盘快照补一根，to_src=kline+snapshot，meta.spot 可审计
  ③ 日K缺当日 + 快照也旧    → 拒绝写入（不产出半新半旧）
  ④ 日K缺当日 + 快照不可用  → 拒绝写入
  ⑤ 抓到个股（收盘 11.71）  → 拒绝写入（标的不对）
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import breadth_run as R  # noqa: E402

PASS = FAIL = 0


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

    orig_k, orig_s = R.fetch_index_bars, R.fetch_spot_bar_000001
    R.fetch_index_bars = lambda *a, **k: kline
    R.fetch_spot_bar_000001 = lambda: spot
    err = ''
    try:
        R.write_sse(spath, bpath)
        raised = False
    except RuntimeError as e:
        raised = True
        err = str(e)
    finally:
        R.fetch_index_bars, R.fetch_spot_bar_000001 = orig_k, orig_s

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

print('\n结果：%d 通过 / %d 失败' % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
