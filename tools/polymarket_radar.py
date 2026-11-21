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
DISTANT_DAYS = 180            # 超半年=远期盘口标记；不硬切（huang 的 2028-01 endDate 也要保留），
                              # 只打 distant 标记交智谱复核「是否无近期催化」。
# CF 交叉验证的宽泛词停词表（v2 收紧）：这些词命中 CF 新闻池不算「国内真在关注该事件」
# ——如 president/election 会命中「萨尔瓦多总统/乌克兰总统」等完全无关新闻。
GENERIC_STOP = {
    'president', 'presidential', 'election', 'nominee', 'policy', 'tariff', 'trade',
    'war', 'conflict', 'strike', 'ceasefire', 'continues', 'through', 'signed',
    'decision', 'rate', 'interest', 'inflation', 'recession', 'treasury', 'dollar',
    'resign', 'appoint', 'merger', 'acquisition', 'antitrust', 'next', 'named',
    'before', 'will', 'end', 'price', 'above', 'this', 'that', 'what', 'who',
    'how', 'and', 'for', 'from', 'into', 'signed', 'law', 'act', 'december',
}
# 只保留「专名/具体实体」类白名单词用于 CF 匹配（机构/人名/地名/法案名），
# 不使用 election/president/war 等通用类别词。
ENTITY_WHITELIST = {
    'fomc', 'fed', 'powell', 'nvidia', 'tesla', 'openai', 'microsoft', 'apple', 'google',
    'iran', 'israel', 'ukraine', 'russia', 'taiwan', 'china', 'opec', 'hormuz',
    'clarity act', 'jensen', 'huang', 'musk', 'trump', 'putin', 'zelensky',
}

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


# 已有卡的英文别名映射（中英双语去重）：Polymarket 盘口多为英文标题，
# 而卡 title/core_subject 是中文，必须先映射才能判重。
CARD_EN_ALIASES = {
    'fed':      ['fed', 'fomc', 'federal reserve', 'powell', 'interest rate', 'rate decision'],
    'iran':     ['iran', 'israel', 'hormuz', 'iranian', 'israeli', 'ceasefire'],
    'ukraine':  ['ukraine', 'russia', 'putin', 'zelensky', 'ukrainian', 'russian'],
    'apec':     ['apec', 'asia pacific', '领导人会议'],
    'huang':    ['huang', 'jensen', 'nvidia', 'rejoins nvidia'],
}


def load_existing_cards():
    """复用已打通的双池去重：events.js(人工卡) + events_daily.json(动态卡)。
    返回 [{id,title,keys}]。keys 含英文别名，确保英文盘口能判重。只标注不写回。"""
    cards = []
    try:
        from event_incubator import load_h5_event_themes
        h5 = load_h5_event_themes()                # events.js 5张(含 huang)
        for c in h5:
            c = dict(c)
            c['keys'] = list(c.get('keys') or []) + CARD_EN_ALIASES.get(c.get('id'), [])
            cards.append(c)
    except Exception as e:
        print('[radar] 读 events.js 卡失败:', type(e).__name__, flush=True)
    # 动态卡池 events_daily.json（title / scope.core_subject / facts 主题）
    for p in (r'E:/WorkBuddy工作区/2026-11-03-23-29-44/data/events_daily.json',
              os.path.join(HERE, '..', 'data', 'events_daily.json')):
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
            for cid, c in (d.get('cards') or {}).items():
                sc = c.get('scope') or {}
                keys = ([sc.get('core_subject', '')] if isinstance(sc, dict) else [])
                keys = [k for k in keys if k] + CARD_EN_ALIASES.get(cid, [])
                cards.append({'id': cid, 'title': c.get('title') or '', 'keys': keys})
            break
        except Exception:
            continue
    return cards


def match_existing(ev_title, entities, existing_cards):
    """判断候选盘口是否已被现有卡覆盖。用实体词双池匹配，返回 (bool, 匹配到的卡)。"""
    t = (ev_title or '').lower()
    for card in existing_cards:
        # 标题实体重合
        for w in entities:
            if len(w) >= 3 and w.lower() in t and w.lower() in (card.get('title') or '').lower():
                return True, card['id'] + ':' + card.get('title', '')[:20]
        # newsKeys 命中（events.js 的关键词，如 '英伟达'/'NVIDIA'/'Jensen'）
        for kw in (card.get('keys') or []):
            kw = str(kw).strip()
            if len(kw) >= 3 and (kw.lower() in t or any(kw.lower() in w.lower() for w in entities)):
                return True, card['id'] + ':' + kw[:20]
    return False, None


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
        # 远期盘口标记（不硬过滤）：endDate 距今超半年 → distant=true，
        # 交智谱复核「是否有近期实质催化」；避免误杀 huang(2028-01 endDate 但持续有新闻)。
        ed = e.get('endDate', '')
        distant = False
        days_left = None
        try:
            edt = datetime.fromisoformat(ed.replace('Z', '+00:00'))
            days_left = (edt - datetime.now(timezone.utc)).days
            distant = days_left > DISTANT_DAYS
        except Exception:
            pass
        out.append({'id': e.get('id'), 'title': e.get('title', ''), 'slug': e.get('slug', ''),
                    'vol24h': round(v), 'endDate': ed, 'days_left': days_left, 'distant': distant,
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
    """CF 池 48h 交叉验证 v2（收紧）：只用具体实体词匹配，含 B 级 >=2 条。

    v2 改动：① 删掉「用白名单类别词补充」——它是 president/election/war 假命中的根源
    （会命中「萨尔瓦多总统/乌克兰总统」等无关新闻）；② 加 GENERIC_STOP 过滤宽泛词；
    ③ 国家/地区大类词(china/taiwan/russia…)单独命中不算，必须搭配至少一个
    具体实体（人名/机构/法案），避免「China hog farming」这种假阳性。
    """
    # 从事件标题抽实体词：英文>=4字符 / 中文>=2字
    words = re.findall(r'[A-Za-z]{4,}|[\u4e00-\u9fff]{2,}', ev_title)
    words = [w for w in words if w.lower() not in GENERIC_STOP]
    # 补充：标题里的专名白名单词（如 "Fed Decision" 的 fed 只有3字母，正则漏掉）
    for kw in ENTITY_WHITELIST:
        if re.search(r'\b' + re.escape(kw) + r'\b', ev_title, re.I) and kw not in words:
            words.append(kw)
    words = list(dict.fromkeys(words))[:10]
    if not words:
        return {'pass': False, 'reason': '无具体实体词(已排除宽泛词)', 'hits': [], 'entities': []}
    # 国家/地区大类词：单独命中不算数，需搭配具体实体
    broad = {'china', 'taiwan', 'russia', 'ukraine', 'israel', 'iran', 'american', 'french'}
    specific = [w for w in words if w.lower() not in broad]
    hits = []
    for it in cf_items:
        title = str(it.get('title') or '')
        low = title.lower()
        if it.get('level') not in ('S', 'A', 'B'):
            continue
        matched = [w for w in words if w.lower() in low]
        if not matched:
            continue
        # 若命中的全是 broad 大类词且没有任何 specific 实体命中 → 视为假阳性跳过
        if not any(w.lower() in low for w in specific):
            continue
        hits.append({'level': it.get('level'), 'title': title[:80], 'ts': it.get('ts'),
                     'matched': matched[:3]})
    return {'pass': len(hits) >= 2, 'reason': 'CF48h具体实体命中%d条(需>=2)' % len(hits),
            'entities': words, 'hits': hits[:5]}


def zhipu_review(ev_title, cat, cf_summary, days_left=None):
    """语义复核——只用智谱，严禁 Gemini。失败返回 None（影子模式不阻断）。"""
    try:
        fn = _zhipu('glm-4-flash-250414')
        prompt = (
            '判断这条预测市场事件是否属于「值得宏观事件追踪卡持续跟踪的事件驱动型议题」。\n'
            '只回答 JSON: {"keep":true/false,"reason":"15字内"}\n'
            '放行：宏观/央行决策、地缘/能源冲突、科技巨头重大人事/并购/反垄断、重大政经政策。\n'
            '拒绝：体育赛事、娱乐、纯加密货币价格押注、纯股价涨跌押注、一次性数据点。\n'
            '特别拒绝【远期无催化】：距离落地超半年(如2028年大选/提名)且国内新闻佐证稀薄、'
            '无近期重磅催化的长周期议题；但若虽远期却持续有具体实体新闻(如某公司高管动向事件)则可放行。\n'
            '事件: ' + ev_title + '\n类别: ' + cat + '\n距落地天数: ' +
            ('未知' if days_left is None else str(days_left)) + '\n国内新闻佐证: ' + cf_summary[:300]
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

    existing_cards = load_existing_cards()
    print('[radar] 双池已有卡', len(existing_cards), '张', flush=True)

    candidates = []
    for e in poly:
        cat = whitelist(e['title'])
        if not cat:
            continue
        cv = cross_validate(e['title'], cf_items, cat)
        rec = dict(e, category=cat, cf=cv)
        # 双池去重：标注是否已被现有卡覆盖（影子模式只标注，不改卡）
        dup, matched = match_existing(e['title'], cv.get('entities') or [], existing_cards)
        rec['existing'] = dup
        rec['matched_card'] = matched
        if not a.no_llm and cv['pass']:
            rec['llm'] = zhipu_review(e['title'], cat, ' / '.join(h['title'] for h in cv['hits']),
                                      e.get('days_left'))
        candidates.append(rec)
    candidates.sort(key=lambda x: -x['vol24h'])
    report['candidates'] = candidates
    report['existing_cards'] = [{'id': c['id'], 'title': c.get('title', '')} for c in existing_cards]
    # 拆分「已有卡跳过」和「真正净新增」——方便观察新卡净产出率
    genuine_new = [c for c in candidates
                   if not c['existing'] and c['cf']['pass'] and c.get('llm', {}).get('keep') is True]
    report['genuine_new'] = [{'title': c['title'], 'vol24h': c['vol24h'], 'category': c['category'],
                              'days_left': c.get('days_left'), 'entities': c['cf'].get('entities')}
                             for c in genuine_new]
    report['stats'] = {
        'poly_ge_threshold': len(poly),
        'after_whitelist': len(candidates),
        'after_cf': sum(1 for c in candidates if c['cf']['pass']),
        'distant_flagged': sum(1 for c in candidates if c.get('distant')),
        'llm_keep': sum(1 for c in candidates if c.get('llm', {}).get('keep') is True),
        'existing_skipped': sum(1 for c in candidates if c['existing']),
        'genuine_new': len(genuine_new),      # 净新增候选（去重+CF+智谱全过）
    }
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('[radar] stats', report['stats'], flush=True)
    for c in candidates:
        flags = []
        if c.get('distant'): flags.append('远期%d天' % (c.get('days_left') or 0))
        if c['existing']: flags.append('已有卡:' + str(c['matched_card']))
        print('  [%s] $%-9d %s | CF:%s %s%s' %
              (c['category'], c['vol24h'], c['title'][:52],
               'PASS' if c['cf']['pass'] else 'fail', c['cf']['reason'],
               (' | ' + ' '.join(flags)) if flags else ''), flush=True)
    print('[radar] 净新增候选', len(genuine_new), '个:',
          [c['title'][:40] for c in genuine_new], flush=True)
    print('[radar] wrote', a.out, flush=True)


if __name__ == '__main__':
    main()
