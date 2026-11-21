# -*- coding: utf-8 -*-
"""Phase 1 只读探测（GHA 云端跑，本机不跑真实外部请求）

① Polymarket Gamma API 直连稳定性/延迟/字段结构
② Top30 盘口 24h 成交额分布（校准门槛）
③ CF 新闻池(/api/news)新鲜度
④ 【附带】6 指数日K真实起点（供方案A sse.json.indices 使用）

只读，不改任何生产数据/建卡逻辑。输出 phase1_probe.json。
"""
import os, sys, json, time, urllib.request, urllib.error, statistics
from datetime import datetime, timezone, timedelta

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'phase1_probe.json')
GAMMA = 'https://gamma-api.polymarket.com/events'
SITE = 'https://macro-pulse.app.workbuddy.host/api/news'
UA = {'User-Agent': 'Mozilla/5.0 (compatible; MacroPulseProbe/1.0)', 'Accept': 'application/json'}

# 方案A需要的6指数（secid 来自 universe.js）
INDICES = {
    'SH': ('上证指数', '1.000001', 'sh000001'),
    'SZ': ('深证成指', '0.399001', 'sz399001'),
    'SZ50': ('上证50', '1.000016', 'sh000016'),
    'HS300': ('沪深300', '1.000300', 'sh000300'),
    'CYB': ('创业板指', '0.399006', 'sz399006'),
    'KC50': ('科创50', '1.000688', 'sh000688'),
}
EM_KLINE = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'


def bjnow():
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def get_json(url, timeout=15):
    t0 = time.time()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        ms = round((time.time() - t0) * 1000)
        return json.loads(data.decode('utf-8')), ms, r.status


def probe_gamma():
    """① + ② Gamma API：3 次探测稳定性 + Top30 成交额分布"""
    url = (GAMMA + '?closed=false&order=volume24hr&ascending=false'
           '&limit=30&select=id,title,slug,volume24hr,volume,liquidity,endDate,markets,closed')
    runs = []
    last = None
    for i in range(3):
        try:
            d, ms, st = get_json(url, timeout=20)
            n = len(d) if isinstance(d, list) else -1
            runs.append({'ok': True, 'http': st, 'ms': ms, 'n': n})
            last = d
        except Exception as e:
            runs.append({'ok': False, 'err': type(e).__name__ + ':' + str(e)[:120]})
        if i < 2:
            time.sleep(1)
    result = {'url': url, 'runs': runs, 'stable': sum(1 for r in runs if r['ok']), 'fields': [], 'top30': []}
    if not last or not isinstance(last, list):
        return result
    if last:
        result['fields'] = sorted(last[0].keys())
    vols = []
    for e in last:
        v = e.get('volume24hr')
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = 0.0
        vols.append(v)
        result['top30'].append({
            'title': (e.get('title') or '')[:90],
            'slug': e.get('slug', ''),
            'vol24h': round(v),
            'endDate': e.get('endDate', ''),
            'n_markets': len(e.get('markets') or []),
        })
    result['vol_stats'] = {
        'min': round(min(vols)), 'p25': round(statistics.quantiles(vols, n=4)[0]) if len(vols) >= 4 else None,
        'median': round(statistics.median(vols)), 'p75': round(statistics.quantiles(vols, n=4)[2]) if len(vols) >= 4 else None,
        'max': round(max(vols)),
    }
    # 门槛候选：各档位能留下几个
    result['threshold_buckets'] = {
        str(t): sum(1 for v in vols if v >= t)
        for t in (10000, 50000, 100000, 500000, 1000000, 5000000)
    }
    return result


def probe_cf_news():
    """③ CF 新闻池新鲜度"""
    out = {'url': SITE}
    try:
        d, ms, st = get_json(SITE, timeout=20)
        items = d.get('items') if isinstance(d, dict) else (d if isinstance(d, list) else [])
        out.update({'ok': True, 'http': st, 'ms': ms, 'n_items': len(items) if items else 0})
        if items:
            ts = []
            for it in items[:50]:
                v = it.get('ts') or it.get('timestamp') or it.get('time')
                if v:
                    try:
                        f = float(v)
                        if f > 1e11:
                            f /= 1000
                        ts.append(f)
                    except (ValueError, TypeError):
                        pass
            if ts:
                now = time.time()
                out['newest_age_hours'] = round((now - max(ts)) / 3600, 1)
                out['oldest_age_hours'] = round((now - min(ts)) / 3600, 1)
            out['sample_titles'] = [str(it.get('title', ''))[:60] for it in items[:3]]
    except Exception as e:
        out.update({'ok': False, 'err': type(e).__name__ + ':' + str(e)[:120]})
    return out


def probe_index_starts():
    """④ 6 指数真实起点（东财 kline，取最早一根）"""
    out = {}
    for key, (name, secid, _) in INDICES.items():
        params = ('?secid=%s&klt=101&fqt=1&lmt=10000&end=20500101&beg=19900101'
                  '&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57' % secid)
        rec = {'name': name, 'secid': secid}
        try:
            d, ms, st = get_json(EM_KLINE + params, timeout=25)
            kl = ((d or {}).get('data') or {}).get('klines') or []
            rec.update({'ok': bool(kl), 'ms': ms, 'bars': len(kl),
                        'first': kl[0].split(',')[0] if kl else None,
                        'last': kl[-1].split(',')[0] if kl else None,
                        'last_close': kl[-1].split(',')[2] if kl else None})
        except Exception as e:
            rec.update({'ok': False, 'err': type(e).__name__ + ':' + str(e)[:120]})
        out[key] = rec
        time.sleep(0.5)
    return out


def main():
    print('[Phase1] start', bjnow(), flush=True)
    report = {'generated': bjnow(), 'note': 'GHA 只读探测，不改生产'}
    print('[Phase1] ① Gamma API...', flush=True)
    report['gamma'] = probe_gamma()
    print('[Phase1] gamma stable', report['gamma']['stable'], '/3', flush=True)
    print('[Phase1] ② CF 新闻池...', flush=True)
    report['cf_news'] = probe_cf_news()
    print('[Phase1] cf ok', report['cf_news'].get('ok'), flush=True)
    print('[Phase1] ③ 指数起点...', flush=True)
    report['indices'] = probe_index_starts()
    for k, v in report['indices'].items():
        print('  ', k, v.get('first'), v.get('bars'), v.get('ok'), flush=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('[Phase1] wrote', OUT, flush=True)


if __name__ == '__main__':
    main()
