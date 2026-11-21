# -*- coding: utf-8 -*-
"""多指数 K 线增量更新（独立脚本，与 breadth 主跑批彻底解耦）

用户 2026-11-21 铁律：GHA 已有完整 sse.json 就从中裁切/增量补，
绝不为了生成指数数据重跑 breadth 主流程（5584 只全市场 + 招风控）。

策略：
  · 读已有 sse.json 的 indices（完整历史，一次生成后再不重抓）
  · 每个指数只发 1 个轻量请求(lmt=8)取最近几根，追加到旧数据尾部
  · 末日已存在则跳过（幂等）；某指数失败只跳过它，保留旧数据
  · 只在数据确实变新时才写文件 + 提交
"""
import os, sys, json, argparse
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from breadth_lib import P
except Exception:
    P = None
import urllib.request

SSE = os.path.join(HERE, '..', 'breadth', 'data', 'sse.json')
EM_KLINE = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
UA = {'User-Agent': 'Mozilla/5.0 (compatible; MacroPulseIdx/1.0)'}


def log(*a):
    print('[update_indices]', *a, flush=True)


def bjnow():
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def fetch_tail(secid, lmt=8):
    """只取最近 lmt 根（轻量）；走 breadth_lib 的代理 session（若可用）"""
    params = ('?secid=%s&klt=101&fqt=1&lmt=%d&end=20500101&beg=19900101'
              '&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57' % (secid, lmt))
    url = EM_KLINE + params
    if P is not None:
        try:
            d = P._get(EM_KLINE, params)   # 走既有 session/代理，遵守风控
        except Exception:
            d = None
    else:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as r:
                d = json.loads(r.read().decode('utf-8'))
        except Exception:
            d = None
    if not d:
        return []
    kl = ((d.get('data') or {}).get('klines') or [])
    out = []
    for line in kl:
        p = line.split(',')
        if len(p) < 7:
            continue
        try:
            out.append({'d': p[0][:10], 'o': float(p[1]), 'c': float(p[2]),
                        'h': float(p[3]), 'l': float(p[4])})
        except (ValueError, IndexError):
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sse', default=SSE)
    ap.add_argument('--lmt', type=int, default=8, help='每指数只取最近几根')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    with open(a.sse, encoding='utf-8') as f:
        d = json.load(f)
    indices = d.get('indices') or {}
    if not indices:
        log('无 indices（首次需先生成），退出')
        return 0
    changed = 0
    for key, obj in indices.items():
        sec = obj.get('secid')
        if not sec:
            log(key, '无 secid，跳过'); continue
        last = obj['dates'][-1] if obj.get('dates') else ''
        tail = fetch_tail(sec, a.lmt)
        new = [b for b in tail if b['d'] > last]
        if not new:
            log(key, '已是最新', last); continue
        log(key, '补', len(new), '根:', new[0]['d'], '~', new[-1]['d'])
        for b in new:
            obj['dates'].append(b['d'])
            obj['o'].append(round(b['o'], 2)); obj['h'].append(round(b['h'], 2))
            obj['l'].append(round(b['l'], 2)); obj['c'].append(round(b['c'], 2))
        obj['to'] = obj['dates'][-1]; obj['days'] = len(obj['dates'])
        changed += 1
    if changed and not a.dry_run:
        d['meta']['indices_updated'] = bjnow()
        with open(a.sse, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, separators=(',', ':'))
        log('已写出', a.sse, '更新指数', changed, '个')
    elif changed:
        log('dry-run: 将更新', changed, '个指数（未写）')
    else:
        log('无变化，不写文件')
    return 0


if __name__ == '__main__':
    main()
