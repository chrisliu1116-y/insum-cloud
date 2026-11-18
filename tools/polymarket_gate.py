# -*- coding: utf-8 -*-
"""Polymarket 活跃合约硬门槛（严格模式 A：宁缺毋滥）。

用户 2026-11-18 拍板：
  · 自动建卡必须有 Polymarket 活跃合约匹配（必要非充分条件），否则一律不建。
  · GHA 云端直连 gamma-api（无 CORS 限制），带 429 退避重试，不走浏览器 r.jina.ai 代理。
  · 真金白银开盘口 = 全球公认的持续大事件，天然过滤纯行情波动/口水新闻。

用法：
  from polymarket_gate import find_active_contract
  hit = find_active_contract(['俄乌', '停火', '乌克兰'], min_volume=100000)
  # hit = {title, volume, events, outcomes, ...} 或 None
"""
import os, json, time, urllib.request, urllib.error, urllib.parse

GAMMA = 'https://gamma-api.polymarket.com'
DEFAULT_MIN_VOLUME = 100000   # 10 万美元成交量门槛（低于此视为冷门/口水）
RETRIES = 3

def _get(path, timeout=20):
    url = GAMMA + path
    for i in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; MacroPulseBot/1.0)',
                'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < RETRIES - 1:
                wait = 2 ** i
                print('[poly] 429, 退避 %ds 后重试(%d/%d)' % (wait, i + 1, RETRIES))
                time.sleep(wait)
                continue
            raise
        except Exception:
            if i < RETRIES - 1:
                time.sleep(1)
                continue
            raise

def search_events(query, limit=8):
    """gamma-api public-search，返回 events 列表。"""
    path = '/public-search?q=' + urllib.parse.quote(query) + '&limit_per_type=%d' % limit
    d = _get(path)
    return d.get('events') or []

def _volume_of(ev):
    v = ev.get('volume') or ev.get('volumeNum') or 0
    try: return float(v)
    except (TypeError, ValueError): return 0.0

def _liquidity_of(ev):
    v = ev.get('liquidity') or ev.get('liquidityNum') or 0
    try: return float(v)
    except (TypeError, ValueError): return 0.0

def find_active_contract(keywords, min_volume=DEFAULT_MIN_VOLUME, verbose=True):
    """用关键词搜活跃合约，返回成交量达标的最高_volume事件四元组；无则 None。
    keywords: 主题关键词列表（如 ['俄乌','停火']），逐个搜，命中任一即返回。
    """
    best = None
    for kw in keywords:
        try:
            evs = search_events(kw)
        except Exception as e:
            if verbose: print('[poly] 搜索「%s」失败: %s' % (kw, type(e).__name__))
            continue
        for ev in evs:
            vol = _volume_of(ev)
            if vol < min_volume:
                continue
            if best is None or vol > _volume_of(best):
                best = ev
        if best is not None and verbose:
            print('[poly] 「%s」命中活跃合约: %s (vol=$%.0f)'
                  % (kw, str(best.get('title'))[:40], _volume_of(best)))
            break
    if best is None:
        if verbose: print('[poly] 无成交量≥$%d 的活跃合约 → 拒绝建卡' % min_volume)
        return None
    # 提取合约四元组（供前端 predict.js 用）
    markets = best.get('markets') or []
    q = best.get('slug') or ''
    out = {
        'title': best.get('title', ''),
        'slug': q,
        'volume': _volume_of(best),
        'liquidity': _liquidity_of(best),
        'events': best.get('title', ''),
        'outcomes': [],
    }
    # 从 markets 里挑一个 outcome 作为 Yes 合约
    for m in markets[:5]:
        oc = m.get('outcomes') or []
        if isinstance(oc, str):
            try: oc = json.loads(oc)
            except Exception: oc = []
        prices = m.get('outcomePrices') or []
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except Exception: prices = []
        for j, name in enumerate(oc):
            if str(name).lower() in ('yes', 'no') and j < len(prices):
                out['outcomes'].append({
                    'market_question': m.get('question') or best.get('title', ''),
                    'outcome': str(name),
                    'price': float(prices[j]) if prices[j] else 0,
                    'market_id': m.get('id'),
                })
        if out['outcomes']:
            break
    return out

if __name__ == '__main__':
    # 冒烟测试：Fed 应有活跃合约；纯行情词应无
    for kws in [['fed decision december', '美联储12月'], ['原油大跌', '白银创新高']]:
        r = find_active_contract(kws, verbose=True)
        print('  结果:', (r['title'][:40] if r else None))
