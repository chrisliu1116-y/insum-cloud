# -*- coding: utf-8 -*-
"""test_tencent_sse_fallback.py — 东财失败时腾讯 fqkline 回退的可复跑自测。

跑法：python tools/test_tencent_sse_fallback.py
不联网：monkeypatch 掉 breadth_lib._get（模拟东财 push2his 返回空）、
        R.fetch_index_bars_tencent（返回构造的腾讯日K）、R.SSE_JSON（临时历史底稿）、
        R.fetch_spot_bar_000001（快照不可用）。
不写线上 sse.json：全部在 tempfile 临时目录里跑 write_sse()。

覆盖：
  ① 东财成功             → 走主路径，kline_source=eastmoney，腾讯函数零调用
  ② 东财失败 + 腾讯可用   → 回退拼接，通过 write_sse 全部护栏，
                            kline_source=tencent-fqkline，起点 1990 / 末日对齐 breadth
  ③ 东财失败 + 腾讯也空   → write_sse 拒写（RuntimeError），不静默降级
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import breadth_run as R  # noqa: E402
import breadth_lib as B  # noqa: E402

PASS = FAIL = 0
TX_CALLS = []


def ok(cond, name, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  OK   ' + name + (('  | ' + str(extra)) if extra != '' else ''))
    else:
        FAIL += 1
        print('  FAIL ' + name + (('  | ' + str(extra)) if extra != '' else ''))


# 腾讯近端日K（模拟 fqkline 解析后的 bars，含 breadth 交易日 2026-11-11 那根）
TX_BARS = [
    {"d": "2026-11-09", "o": 3659.02, "c": 3660.79, "h": 3670.0, "l": 3650.0, "v": 4.0e8, "a": 0.0},
    {"d": "2026-11-10", "o": 3682.04, "c": 3663.72, "h": 3690.0, "l": 3648.0, "v": 4.1e8, "a": 0.0},
    {"d": "2026-11-11", "o": 3655.66, "c": 3680.64, "h": 3689.65, "l": 3650.31, "v": 4.33e8, "a": 0.0},
]

# 东财主路径返回的原始 klines（起点 1990-12-19 + 末日 11-11）
EM_KLINES = [
    "1990-12-19,96.05,99.98,99.98,96.05,100000,10000000",
    "2026-11-10,3682.04,3663.72,3690.00,3648.00,410000000,700000000000",
    "2026-11-11,3655.66,3680.64,3689.65,3650.31,433000000,736000000000",
]


def write_history(path):
    """临时历史底稿：起点 1990-12-19，末日 2026-11-10（昨天）。"""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({
            "meta": {"to": "2026-11-10"},
            "dates": ["1990-12-19", "2026-11-09", "2026-11-10"],
            "o": [96.05, 3659.02, 3682.04],
            "h": [99.98, 3670.0, 3690.0],
            "l": [96.05, 3650.0, 3648.0],
            "c": [99.98, 3660.79, 3663.72],
        }, f)


def run(em_data, tx_bars):
    """在临时目录里跑一次 write_sse，返回 (payload 或 None, 异常字符串, 是否抛错)。"""
    global TX_CALLS
    with tempfile.TemporaryDirectory() as tmp:
        bpath = os.path.join(tmp, 'breadth.json')
        spath = os.path.join(tmp, 'sse.json')
        hpath = os.path.join(tmp, 'sse_history.json')
        with open(bpath, 'w', encoding='utf-8') as f:
            json.dump({"meta": {}, "dates": ["2026-11-10", "2026-11-11"]}, f)
        write_history(hpath)

        orig_get = B._get
        orig_tx = R.fetch_index_bars_tencent
        orig_spot = R.fetch_spot_bar_000001
        orig_hist = R.SSE_JSON
        orig_src = R._SSE_KLINE_SOURCE
        TX_CALLS = []
        try:
            def fake_tx(*a, **k):
                TX_CALLS.append(1)
                return tx_bars

            B._get = lambda *a, **k: em_data            # 东财：None=失败 / dict=成功
            R.fetch_index_bars_tencent = fake_tx
            R.fetch_spot_bar_000001 = lambda: None       # 快照不可用
            R.SSE_JSON = hpath                           # 拼接底稿指向临时文件
            R._SSE_KLINE_SOURCE = "eastmoney"            # 每次重置，保证可复跑
            err = ''
            try:
                R.write_sse(spath, bpath)
                payload = json.load(open(spath, encoding='utf-8'))
                raised = False
            except RuntimeError as e:
                payload = None
                raised = True
                err = str(e)
            return payload, err, raised
        finally:
            B._get, R.fetch_index_bars_tencent = orig_get, orig_tx
            R.fetch_spot_bar_000001, R.SSE_JSON = orig_spot, orig_hist
            R._SSE_KLINE_SOURCE = orig_src


print('===== ① 东财成功：走主路径，腾讯零调用 =====')
payload, err, raised = run({"data": {"klines": EM_KLINES}}, TX_BARS)
ok(not raised, '① 正常写出', err)
if payload:
    ok(payload['meta'].get('kline_source') == 'eastmoney',
       '① kline_source=eastmoney', payload['meta'].get('kline_source'))
    ok(payload['meta'].get('source') == 'eastmoney 1.000001',
       '① source 仍为 eastmoney', payload['meta'].get('source'))
    ok(payload['meta']['days'] == 3, '① 根数=3（未拼接历史）', payload['meta']['days'])
    ok(payload['dates'][0] == '1990-12-19', '① 起点 1990-12-19', payload['dates'][0])
ok(len(TX_CALLS) == 0, '① 腾讯函数零调用（主路径未触碰备用源）', TX_CALLS)

print('===== ② 东财失败 + 腾讯可用：回退拼接，通过 write_sse 全部护栏 =====')
payload, err, raised = run(None, TX_BARS)
ok(not raised, '② 正常写出（未因起点/末日护栏拒写）', err)
if payload:
    m = payload['meta']
    ok(m.get('kline_source') == 'tencent-fqkline',
       '② kline_source=tencent-fqkline', m.get('kline_source'))
    ok('tencent' in m.get('source', ''), '② meta.source 标注腾讯', m.get('source'))
    ok(m.get('to_src') == 'kline', '② to_src 仍是既有语义 kline', m.get('to_src'))
    ok(m.get('to') == '2026-11-11', '② meta.to == breadth 交易日', m.get('to'))
    ok(payload['dates'][0] == '1990-12-19',
       '② 起点护栏通过（拼接后仍 1990-12-19）', payload['dates'][0])
    ok(payload['dates'][-1] == '2026-11-11',
       '② 末日护栏通过（对齐 breadth）', payload['dates'][-1])
    ok(m['days'] == 4, '② 拼接后 4 根（历史 3 + 腾讯新日 1）', m['days'])
    ok(abs(payload['c'][-1] - 3680.64) < 0.01,
       '② 末根收盘取腾讯当天值 3680.64', payload['c'][-1])
ok(len(TX_CALLS) == 1, '② 腾讯函数被调用 1 次', TX_CALLS)

print('===== ③ 东财失败 + 腾讯也空：拒写，不静默降级 =====')
payload, err, raised = run(None, [])
ok(raised, '③ write_sse 抛 RuntimeError 拒写', err)
ok('抓取为空' in err, '③ 拒写原因=日K抓取为空', err)
ok(payload is None, '③ 未写出 sse.json')
ok(len(TX_CALLS) == 1, '③ 腾讯函数被调用 1 次后空手返回', TX_CALLS)

print('\n结果：%d 通过 / %d 失败' % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
