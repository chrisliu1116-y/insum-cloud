# -*- coding: utf-8 -*-
"""Phase 2 影子模式：Polymarket 主动雷达（只读，不写生产卡）

链路（用户 2026-11-21 拍板）：
  Gamma API 直连(24h成交额>=40万美元)
    → 四大财经白名单硬过滤(中英双语,剔除体育/娱乐/纯币价/纯股价押注)
    → CF __news 池 48h 交叉验证(含 B 级 >=2 条)
    → 语义复核仅用智谱(严禁 Gemini)
    → 输出影子报告到 JSON + 日志，绝不写生产卡片/前端推送。

红线：
  · 语义复核只调 llm_pool._zhipu()，代码里绝不出现 chat()/chat_heavy()/_gemini()
  · 影子模式：本脚本只写 --out 指定的报告文件，不改 events_daily.json
"""
import os, sys, json, time, re, argparse, urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from llm_pool import _zhipu                      # v2: 只用智谱，绝不走 chat()(会轮换Gemini)

GAMMA = 'https://gamma-api.polymarket.com/events'
EDGE_NEWS = 'https://macropulse-api.pages.dev/__news'   # 复用 build_news_map.py 的链路
UA = {'User-Agent': 'mp-evolve', 'Accept': 'application/json'}      # CF __news 用
GAMMA_UA = {'User-Agent': 'mp-evolve'}   # Gamma API 对 Accept:application/json 会回 406（实测），只带 UA
VOL_THRESHOLD = 400000        # 用户拍板：40万美元宽初筛（45万的 Nvidia 事件不能被50万漏掉）

# 四大财经白名单（中英双语关键词）——事件驱动型盘口才放行
_WL = {
    'macro_central': [  # 宏观/央行
        'fed', 'fomc', 'rate decision', 'interest rate', 'inflation', 'cpi', 'powell',
        'recession', 'treasury', 'dollar', '央行', '加息', '降息', '利率', '通胀', '联储',
        '美联储', '货币政策', '经济衰退', '国债收益率',
    ],
    'geo_energy': [     # 地缘/能源
        'war', 'ceasefire', 'conflict', 'invasion', 'strike', 'sanction', 'blockade',
        'strait', 'oil', 'opec', 'iran', 'israel', 'russia', 'ukraine', 'china', 'taiwan',
        '战争', '停火', '冲突', '制裁', '封锁', '海峡', '能源', '原油', '石油', '地缘',
        '伊朗', '以色列', '俄乌', '台海',
    ],
    'tech_major': [     # 科技巨头重大变故
        'ceo', 'resign', 'step down', 'appoint', 'merger', 'acquisition', 'antitrust',
        'nvidia', 'tesla', 'apple', 'microsoft', 'openai', 'meta', 'google',
        'CEO', '辞职', '卸任', '接任', '任命', '并购', '收购', '反垄断',
        '英伟达', '特斯拉', '苹果', '微软', ' OpenAI', '谷歌',
    ],
    'policy': [         # 重大政经政策
        'election', 'president', 'policy', 'tariff', 'trade deal', 'legislation', 'bill',
        'government shutdown', 'clarity act', 'signed into law',
        '大选', '选举', '总统', '政策', '关税', '贸易', '法案', '签署', '政府停摆',
    ],
}
# 硬剔除：体育/娱乐/纯币价/纯股价押注
_BLACK = [
    r'\bvs\.?\b', r'\bv\.?s\.?\b', 'nba', 'wnba', 'nfl', 'mlb', 'nhl', 'soccer', 'football',
    'basketball', r'game \d', 'super bowl', 'world series',
    '体育', '篮球', '足球', '棒球', '比赛', '胜负',
    'oscar', 'grammy', 'emmy', 'movie', 'album', 'celebrity',
    '奥斯卡', '格莱美', '电影', '专辑', '明星', '娱乐',
    r'bitcoin (price|above|hit)', r'btc (price|above)', r'price will bitcoin',
    r'price of (bitcoin|eth)', r'ethereum price',
    # 「纯股价押注」指「某股涨到X美元」这类数字盘口，不是「某人回任CEO」事件
    r'(stock|shares?) (price|above|hit)', r'price will (nvda|tsla|aapl)',
    '比特币价格', '币价', '股价涨', '股价跌', '涨到', '跌到',
]
_BLACK_RE = [re.compile(x, re.I) for x in _BLACK]


def bjnow():
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def get_json(url, headers=UA, timeout=25, retries=3):
    """带重试：Gamma API 偶发 SSL EOF / RemoteDisconnected（Phase1 实测过）。
    失败时指数退避 1s/2s/4s，避免把偶发断连当成「无候选」。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(2 ** i)
    raise last


def whitelist(title):
    """返回命中的白名单类别；命中黑名单或不属于4类 → None"""
    t = title or ''
    for rx in _BLACK_RE:
        if rx.search(t):
            return None
    for cat, kws in _WL.items():
        for kw in kws:
            if kw.lower() in t.lower():
                return cat
    return None


def fetch_polymarket():
    # limit=60 实测触发 HTTP 406（Gamma 上限约 50），用 30（Phase1 已验证稳定）
    url = (GAMMA + '?closed=false&order=volume24hr&ascending=false'
           '&limit=30&select=id,title,slug,volume24hr,endDate,markets,closed')
    d = get_json(url, headers=GAMMA_UA)
    out = []
    for e in (d if isinstance(d, list) else []):
        try:
            v = float(e.get('volume24hr') or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v < VOL_THRESHOLD:
            continue
        out.append({'id': e.get('id'), 'title': e.get('title', ''), 'slug': e.get('slug', ''),
                    'vol24h': round(v), 'endDate': e.get('endDate', ''),
                    'n_markets': len(e.get('markets') or [])})
    return out


def fetch_cf_items(hours=48):
    """复用 build_news_map.py 现成链路：CF /__news，不碰 /api/news"""
    d = get_json(EDGE_NEWS)
    items = d.get('items', []) or []
    cutoff = time.time() * 1000 - hours * 3600 * 1000
    recent = []
    for it in items:
        ts = it.get('ts') or 0
        if isinstance(ts, str):
            try: ts = float(ts)
            except ValueError: continue
        if ts and ts >= cutoff:
            recent.append(it)
    return recent, d.get('generated', '')


def cross_validate(ev_title, cf_items, cat):
    """CF 池 48h 交叉验证：核心实体反查，含 B 级 >=2 条。"""
    # 从事件标题抽核心实体词（去停用词，取英文 >=4 字符 / 中文 >=2 字）
    stop = {'will', 'the', 'before', 'by', 'end', 'named', 'next', 'decision', 'price',
            'above', 'this', 'that', 'what', 'who', 'how', 'and', 'for', 'from', 'into'}
    words = re.findall(r'[A-Za-z]{4,}|[\u4e00-\u9fff]{2,}', ev_title)
    words = [w for w in words if w.lower() not in stop]
    # 用白名单类别词补充（提高召回）
    words += [kw for kw in _WL.get(cat, []) if re.search(r'[A-Za-z]{4,}|[\u4e00-\u9fff]{2,}', kw)][:4]
    words = list(dict.fromkeys(words))[:8]
    if not words:
        return {'pass': False, 'reason': '无核心实体词', 'hits': []}
    hits = []
    for it in cf_items:
        title = str(it.get('title') or '')
        if it.get('level') not in ('S', 'A', 'B'):
            continue
        if any(w.lower() in title.lower() for w in words):
            hits.append({'level': it.get('level'), 'title': title[:80], 'ts': it.get('ts')})
    return {'pass': len(hits) >= 2, 'reason': 'CF 48h命中%d条(需>=2)' % len(hits),
            'entities': words, 'hits': hits[:5]}


def zhipu_review(ev_title, cat, cf_summary):
    """语义复核——只用智谱，严禁 Gemini。失败返回 None（影子模式不阻断）。"""
    try:
        fn = _zhipu('glm-4-flash-250414')
        prompt = (
            '判断这条预测市场事件是否属于「值得宏观事件追踪卡持续跟踪的事件驱动型议题」。\n'
            '只回答 JSON: {"keep":true/false,"reason":"15字内"}\n'
            '放行：宏观/央行决策、地缘/能源冲突、科技巨头重大人事/并购/反垄断、重大政经政策。\n'
            '拒绝：体育赛事、娱乐、纯加密货币价格押注、纯股价涨跌押注、一次性数据点。\n'
            '事件: ' + ev_title + '\n类别: ' + cat + '\n国内新闻佐证: ' + cf_summary[:300]
        )
        text, ch = fn([{'role': 'user', 'content': prompt}], 120, 25)
        assert ch.startswith('zhipu/'), '禁止非智谱通道'
        m = re.search(r'\{[\s\S]*?\}', text)
        return json.loads(m.group(0)) if m else {'keep': None, 'reason': '解析失败:' + text[:60]}
    except Exception as e:
        return {'keep': None, 'reason': '智谱复核失败:' + type(e).__name__ + ':' + str(e)[:80]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(HERE, 'polymarket_radar_shadow.json'))
    ap.add_argument('--no-llm', action='store_true', help='跳过智谱复核(纯规则影子)')
    a = ap.parse_args()

    report = {'generated': bjnow(), 'mode': 'SHADOW(不写生产)', 'vol_threshold': VOL_THRESHOLD}
    print('[radar] 拉 Polymarket Top30 ...', flush=True)
    try:
        poly = fetch_polymarket()
    except Exception as e:
        report['error'] = 'Gamma失败:' + type(e).__name__ + ':' + str(e)[:120]
        poly = []
    print('[radar] >=40万美元盘口', len(poly), flush=True)
    if not poly and 'error' not in report:
        report['error'] = 'Gamma 返回但0个>=40万盘口(疑似字段/排序异常，需排查)'

    print('[radar] 拉 CF __news ...', flush=True)
    try:
        cf_items, cf_gen = fetch_cf_items(48)
    except Exception as e:
        cf_items, cf_gen = [], 'fail:' + type(e).__name__
    print('[radar] CF 48h条目', len(cf_items), 'generated', cf_gen, flush=True)
    report['cf'] = {'n_48h': len(cf_items), 'generated': cf_gen}

    candidates = []
    for e in poly:
        cat = whitelist(e['title'])
        if not cat:
            continue
        cv = cross_validate(e['title'], cf_items, cat)
        rec = dict(e, category=cat, cf=cv)
        if not a.no_llm and cv['pass']:
            rec['llm'] = zhipu_review(e['title'], cat, ' / '.join(h['title'] for h in cv['hits']))
        candidates.append(rec)
    candidates.sort(key=lambda x: -x['vol24h'])
    report['candidates'] = candidates
    report['stats'] = {
        'poly_ge_threshold': len(poly),
        'after_whitelist': len(candidates),
        'after_cf': sum(1 for c in candidates if c['cf']['pass']),
        'llm_keep': sum(1 for c in candidates if c.get('llm', {}).get('keep') is True),
    }
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('[radar] stats', report['stats'], flush=True)
    for c in candidates:
        print('  [%s] $%-9d %s | CF:%s %s' %
              (c['category'], c['vol24h'], c['title'][:60],
               'PASS' if c['cf']['pass'] else 'fail', c['cf']['reason']), flush=True)
    print('[radar] wrote', a.out, flush=True)


if __name__ == '__main__':
    main()
