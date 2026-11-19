# -*- coding: utf-8 -*-
"""事件板块自治：重大新事件自动建卡 + 双轨归档。

输入：
  --news   llm_news_pipeline.py 的输出（含 level/card_id/is_dup 的逐条判定）
  --events events_daily.json（现有卡）
输出：更新后的 events_daily.json（新增 cards / archive / incubator_log）

三道建卡门槛（严防垃圾小事件）：
  ① 24h 内同一全新主题 ≥3 条 S级/核心A级 且 card_id=none 的快讯
  ② 市场定价验证（Polymarket 活跃合约 / 核心资产异动）—— 无 API 时用"≥3条A+S级"代理
  ③ LLM 冷启动生成完整卡配置（标题/emoji/Scope/初始气泡/倒金字塔首版简介）

双轨归档：
  轨道一 终局：archived=true → 移入 archive
  轨道二 休眠：连续7天 facts 空且赔率平稳 → dormant=true（新进展自动唤醒）
  清理：归档超14天 → 移出展示 JSON

安全：LLM 只用输入事实，不编造；key 从 llm_pool 环境变量读，不打印。
"""
import os, sys, json, time, argparse, re
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_pool import chat, chat_heavy, AllModelsFailed

MAX_ACTIVE = 6
DORMANT_DAYS = 7
ARCHIVE_CLEAN_DAYS = 14

# v2【铁律1：原因vs结果白名单隔离】纯行情波动/单点数据 → 禁止独立建卡。
# 只有「有后续阶段演变的持续性事件」才配建卡；价格结果是事件的下游，不是事件本身。
_NEG_MARKET = re.compile(
    r'(?:大涨|大跌|暴涨|暴跌|涨超|跌超|涨幅|跌幅|反弹|回调|跳水|拉升|冲高|回落|'
    r'失守|重回|站上|逼近|跌破|突破|创新高|创新低|新高|新低|历史新高|'
    r'收涨|收跌|盘前涨|盘前跌|盘中涨|盘中跌|持续冲高|持续走高)'
    r'\s*\d*(?:\.\d+)?\s*%?')
_NEG_DATA_POINT = re.compile(
    r'(?:非农|CPI|PPI|GDP|零售销售|失业率|贸易帐|财报|季报|年报|业绩)'
    r'[^。]{0,12}(?:公布|发布|出炉|数据|超预期|不及预期)')
# 持续性事件的「原因」特征词——命中这些才说明是博弈进程，不是价格结果
_POS_PROCESS = re.compile(
    r'战争|冲突|开战|停火|停战|谈判|会谈|峰会|会晤| Summit|关税|贸易战|制裁|'
    r'封锁|海峡|加息周期|降息周期|利率决议|大选|选举|弹劾|政变|协议|条约|'
    r'制裁清单|出口管制|军事|部署|撤离|签署|生效|重启|破裂')

# v2【铁律3：ticker 白名单】新卡的关联资产必须从 U.get() 支持的代码里选，
# LLM 不许编造。这里列项目已支持的主流 ticker（与 assets/js/universe.js 对齐）。
TICKER_WHITELIST = [
    'BRENT', 'WTI', 'GOLD', 'SILVER', 'US10Y', 'US2Y', 'DXY', 'USDCNH',
    'HS300', 'HSI', 'SPX', 'NDX', 'NVDA', 'AAPL', 'TSLA', 'BTC',
]

def is_pure_market_noise(title):
    """纯行情/单点数据判定：命中负面清单且无持续性事件特征 → True（禁止建卡）。"""
    neg = bool(_NEG_MARKET.search(title) or _NEG_DATA_POINT.search(title))
    pos = bool(_POS_PROCESS.search(title))
    return neg and not pos


INCUBATOR_SYS = """你是事件追踪编辑。判断一组快讯是否构成一个值得单独建卡的新事件。

【输入】若干条"未归属任何现有卡"的高优快讯（S级或核心A级）。
【任务】输出严格 JSON（不要 markdown、不要解释）：
{
  "is_new_event": true/false,
  "theme": "该事件的统一主题名(≤15字)",
  "title": "卡片标题(≤14字，如 某某会谈/某某政策)",
  "icon": "单个最贴切的 emoji(如 🌐🛢️🏛️🕊️⚔️📉)",
  "core_subject": "核心追踪主体(一句话)",
  "stage_focus": ["重点阶段要素1","2","3","4"],
  "exclude_noise": ["要排除的噪音1","2","3"],
  "status": "当前阶段气泡(4~8字)",
  "lead": "倒金字塔首句≤45字，直接给当前阶段定性+最核心结果",
  "rest": "后续句展开，与lead拼成40~160字简介；只用输入事实，绝不编造",
  "tickers": ["布伦特原油BRENT","黄金GOLD","美债10Y US10Y"] 或 [],
  "newsKeys": ["高区分度关键词1","关键词2","关键词3"],
  "reason": "判定理由≤30字"
}
· tickers：只从这些代码里选最多3个：BRENT/WTI/GOLD/SILVER/US10Y/US2Y/DXY/USDCNH/HS300/HSI/SPX/NDX/NVDA/AAPL/TSLA/BTC；没有直接关联的填 []，绝不编造代码。
· newsKeys：3~5个用于匹配后续快讯的关键词，必须是该事件主题的高区分度词。
【严格·is_new_event 只在全部满足时才为 true】
  1) 这些快讯确实在说同一个**有明确事件主体和持续进展**的重大事件；
  2) 与"现有活跃卡主题"明显不同——若属于现有事件(如原油涨跌↔美伊/霍尔木兹卡、利率↔美联储卡)，必须 is_new_event=false，那是现有卡的进展；
  3) 不是**纯行情波动播报**——「原油大跌5%」「白银大涨」「某股跌幅收窄」这类只有价格没有事件驱动的不建卡，它们属于行情不属于事件追踪。
若是零散新闻拼凑、现有事件延续、纯行情涨跌，一律 is_new_event=false。
所有事实必须来自输入快讯，不许编造数字/人名/协议。今天是 2026-11-18。"""

def _fmt_news(news):
    return '\n'.join('- [%s] %s' % (n.get('level', '?'), n.get('title', '')) for n in news[:20])

def cluster_candidates(candidates):
    """把游离高优快讯按主题聚类（关键词主题桶），返回 {主题: [快讯]}。
    只有同主题≥3条才有资格进建卡判断——防零散新闻拼凑误建卡。"""
    THEMES = {
        '原油/能源': ['原油', '布伦特', 'WTI', '油价', 'OPEC', '天然气', '能源'],
        '贵金属': ['黄金', '白银', '金价', '银价', '贵金属'],
        '伊朗/中东': ['伊朗', '霍尔木兹', '胡塞', '中东', '以色列', '沙特'],
        '俄乌': ['俄乌', '乌克兰', '俄罗斯', '普京', '泽连斯基', '停火'],
        '美联储/利率': ['美联储', 'FOMC', '加息', '降息', '利率', '鲍威尔', '沃什'],
        '关税/贸易': ['关税', '贸易战', '进出口', '制裁', '出口管制'],
        '科技股': ['英特尔', '英伟达', '苹果', '微软', '特斯拉', '谷歌', '芯片', '半导体'],
        'APEC/峰会': ['APEC', '峰会', '领导人会议', 'G20', 'G7'],
    }
    groups = defaultdict(list)
    for it in candidates:
        t = it.get('title', '')
        hit = None
        for theme, kws in THEMES.items():
            if any(k.lower() in t.lower() for k in kws):
                hit = theme
                break
        groups[hit or '其他'].append(it)
    return groups

def try_incubate(candidates, existing_themes, verbose=True):
    """对一组候选游离快讯尝试建卡。返回 (card_config|None, channel, reason)"""
    prompt = ('未归属高优快讯（过去24h）：\n' + _fmt_news(candidates) + '\n\n'
              '现有活跃卡主题（属于这些主题的进展一律不得建新卡）：\n'
              + '\n'.join('- ' + str(t) for t in existing_themes) + '\n\n'
              '请严格按系统规则判断是否构成新的重大事件并输出卡片配置 JSON。')
    try:
        text, ch = chat_heavy([{'role': 'system', 'content': INCUBATOR_SYS},
                         {'role': 'user', 'content': prompt}], max_tokens=2000, timeout=100, verbose=verbose)
    except AllModelsFailed:
        return None, 'local-rule', 'LLM全挂，跳过建卡'
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return None, ch, 'JSON解析失败'
    raw = re.sub(r',\s*([}\]])', r'\1', m.group(0))
    try:
        d = json.loads(raw)
    except Exception:
        return None, ch, 'JSON解析失败'
    if not d.get('is_new_event'):
        return None, ch, d.get('reason', '判定为无新事件')
    # 【铁律2·严格模式A】Polymarket 硬门槛：必须有活跃合约才允许建卡。
    # 用主题名 + 卡片标题 + 快讯高频词作搜索关键词（GHA 直连 gamma-api，带429退避）。
    try:
        from polymarket_gate import find_active_contract
        kws = [str(d.get('theme', '')), str(d.get('title', ''))]
        kws += [n.get('title', '')[:10] for n in candidates[:2]]
        kws = [k for k in kws if k][:4]
        poly = find_active_contract(kws, verbose=verbose)
    except Exception as e:
        poly = None
        if verbose: print('[incubate] Polymarket 门槛异常(%s)→拒绝建卡' % type(e).__name__)
    if not poly:
        return None, ch + '(no-poly-contract)', '无Polymarket活跃合约(严格模式拒绝建卡)'
    # 字段校验 + 兜底
    # v2【铁律4】lead 首句按语义点截断到≤45字（照抄 evolve_events 的方案，不硬切词）
    lead = str(d.get('lead', '')).strip()
    if len(lead) > 45:
        cut = None
        for sep in ['，', '；', '：', '、', ' ']:
            idx = lead.rfind(sep, 0, 46)
            if idx > 15:
                cut = lead[:idx]
                break
        lead = (cut or lead[:45]).rstrip('，；、： ') + '…'
    if lead and not lead.endswith(('。', '！', '？', '…')):
        lead += '。'
    rest = str(d.get('rest', '')).strip()
    # v2【铁律3】tickers 从白名单过滤，杜绝 LLM 编造代码
    raw_tickers = d.get('tickers', [])
    tickers = []
    if isinstance(raw_tickers, list):
        for t in raw_tickers:
            code = re.sub(r'[^A-Za-z0-9]', '', str(t)).upper()
            # 从字符串里抠白名单代码（如「布伦特原油BRENT」→ BRENT）
            for w in TICKER_WHITELIST:
                if w in code and w not in tickers:
                    tickers.append(w)
            if code in TICKER_WHITELIST and code not in tickers:
                tickers.append(code)
        tickers = tickers[:3]
    # v2【铁律3】全要素校验：缺核心要素 → 拒绝空壳卡
    missing = []
    if not str(d.get('core_subject', '')).strip(): missing.append('core_subject')
    if not (isinstance(d.get('stage_focus'), list) and d['stage_focus']): missing.append('stage_focus')
    if not (isinstance(d.get('newsKeys'), list) and d['newsKeys']): missing.append('newsKeys')
    if not lead: missing.append('lead')
    if missing:
        return None, ch, '空壳卡要素缺失: ' + ','.join(missing)
    card = {
        'id': 'auto_' + re.sub(r'[^a-z0-9]+', '', str(d.get('theme', 'new')).lower())[:10] + str(int(time.time()))[-4:],
        'icon': str(d.get('icon', '📌'))[:4],
        'title': str(d.get('title', d.get('theme', '新事件')))[:16],
        'cat': '自动发现',
        'status': str(d.get('status', '新事件'))[:10],
        'statusCls': 'watch',
        'scope': {
            'core_subject': str(d.get('core_subject', ''))[:100],
            'stage_focus': d.get('stage_focus', [])[:5] if isinstance(d.get('stage_focus'), list) else [],
            'exclude_noise': d.get('exclude_noise', [])[:5] if isinstance(d.get('exclude_noise'), list) else [],
        },
        'lead': lead, 'rest': rest, 'summary': (lead + rest).strip(),
        'progress_line': '当前态势：' + str(d.get('status', '新事件')),
        'facts': [n.get('title', '') for n in candidates[:5]],
        # v2【铁律3】全要素：关联ticker + newsKeys + pred占位(第三步由gamma-api填)
        'tickers': tickers,
        'pred': {   # 铁律2：Polymarket 硬门槛命中后填入真实合约四元组
            'q': poly.get('slug', ''), 'event': poly.get('events', ''),
            'market': (poly.get('outcomes') or [{}])[0].get('market_question', ''),
            'outcome': (poly.get('outcomes') or [{}])[0].get('outcome', 'Yes'),
        },
        'polymarket': {'title': poly.get('title'), 'volume': poly.get('volume'),
                       'slug': poly.get('slug'), 'matched_at': time.strftime('%Y-%m-%d %H:%M')},
        'newsKeys': [str(x) for x in d.get('newsKeys', [])[:6]],
        'newsExcl': [str(x) for x in d.get('exclude_noise', [])[:4]],
        'trigger_news': [{'title': n.get('title', ''), 'level': n.get('level', '?'),
                          'idx': n.get('idx')} for n in candidates[:5]],
        'noisy_dropped': [], 'archived': False, 'dormant': False,
        'auto_created': True, 'created_at': time.strftime('%Y-%m-%d %H:%M'),
        'last_active_date': time.strftime('%Y-%m-%d'),
        'model_channel': ch,
    }
    return card, ch, d.get('reason', '')

def apply_archive_policy(events, verbose=True):
    """双轨归档 + 清理。原地修改 events。返回动作日志列表。"""
    logs = []
    cards = events.get('cards', {})
    archive = events.setdefault('archive', {})
    today = time.strftime('%Y-%m-%d')

    # 轨道一：终局归档
    for cid in list(cards.keys()):
        c = cards[cid]
        if c.get('archived') and not c.get('_auto_archived'):
            c['_auto_archived'] = True
            c['archived_at'] = today
            logs.append({'action': 'archive', 'card': cid, 'reason': '终局完结'})
            if verbose: print('[archive]', cid, '终局归档')

    # 轨道二：7天无进展休眠（用 last_active_date 或 facts 空判断）
    for cid, c in cards.items():
        if c.get('archived'): continue
        last = c.get('last_active_date', '')
        facts = c.get('facts') or []
        if not facts and last:
            try:
                from datetime import datetime, timedelta
                gap = (datetime.strptime(today, '%Y-%m-%d') - datetime.strptime(last, '%Y-%m-%d')).days
                if gap >= DORMANT_DAYS and not c.get('dormant'):
                    c['dormant'] = True
                    logs.append({'action': 'dormant', 'card': cid, 'reason': '%d天无进展' % gap})
                    if verbose: print('[dormant]', cid, gap, '天无进展')
            except Exception:
                pass
        elif facts and c.get('dormant'):
            # 唤醒
            c['dormant'] = False
            c['last_active_date'] = today
            logs.append({'action': 'wake', 'card': cid, 'reason': '出现新进展'})
            if verbose: print('[wake]', cid, '有新进展，唤醒')

    # 清理：归档超14天移出展示
    for cid in list(cards.keys()):
        c = cards[cid]
        ad = c.get('archived_at', '')
        if c.get('archived') and ad:
            try:
                from datetime import datetime
                gap = (datetime.strptime(today, '%Y-%m-%d') - datetime.strptime(ad, '%Y-%m-%d')).days
                if gap >= ARCHIVE_CLEAN_DAYS:
                    archive[cid] = cards.pop(cid)
                    logs.append({'action': 'clean', 'card': cid, 'reason': '归档超14天'})
                    if verbose: print('[clean]', cid, '归档超14天，移出展示')
            except Exception:
                pass
    return logs

def heat(c):
    """热度分：有效事实数 + 非休眠 + 非归档"""
    return len(c.get('facts') or []) + (0 if c.get('dormant') else 1) + (0 if c.get('archived') else 1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--news', default='E:/WorkBuddy工作区/insum-cloud/llm_news_full.json')
    ap.add_argument('--events', default='E:/WorkBuddy工作区/2026-11-03-23-29-44/data/events_daily.json')
    ap.add_argument('--out', default='E:/WorkBuddy工作区/2026-11-03-23-29-44/data/events_daily.json')
    ap.add_argument('--min-high', type=int, default=3, help='建卡所需高优游离快讯数')
    ap.add_argument('--window-hours', type=int, default=24, help='建卡扫描窗口(小时)，默认过去24h')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    events = json.load(open(a.events, encoding='utf-8'))
    news_data = json.load(open(a.news, encoding='utf-8')) if os.path.exists(a.news) else {}
    results = news_data.get('results', [])
    news_by_idx = {it['title_idx']: it for it in results} if isinstance(results, list) else {}

    # 筛游离高优快讯（card_id=none 且 S/核心A，未去重）
    # v447：扫描窗口=过去 window-hours 小时；源条目带 ts 时按窗口过滤，
    #        无 ts 的条目保留（分级产物多为当日快照，不误杀）。
    raw = news_data.get('sample', [])
    candidates = []
    cutoff = time.time() - a.window_hours * 3600
    for it in results:
        idx = it.get('title_idx')
        if it.get('card_id') != 'none' or it.get('is_dup'): continue
        if it.get('level') not in ('S', 'A'): continue
        src_item = news_by_idx.get(idx, {}) or {}
        if not src_item and isinstance(raw, list) and idx < len(raw): src_item = raw[idx] if isinstance(raw[idx], dict) else {}
        # 时间过滤：有 ts 才过滤（支持秒/毫秒/字符串）
        ts = src_item.get('ts') or src_item.get('timestamp') or it.get('ts')
        if ts:
            try:
                tv = float(ts)
                if tv > 1e11: tv /= 1000
                if tv < cutoff: continue
            except (ValueError, TypeError):
                pass
        title = src_item.get('title') or (raw[idx] if isinstance(raw, list) and idx < len(raw) else '')
        if isinstance(title, dict): title = title.get('title', '')
        candidates.append({'level': it['level'], 'title': title, 'idx': idx})
    print('游离高优快讯(过去%dh):' % a.window_hours, len(candidates))

    # 双轨归档（先做，腾位置）
    logs = apply_archive_policy(events)

    # 建卡决策日志
    ilog = events.setdefault('incubator_log', [])
    existing_themes = [c.get('title', '') or c.get('scope', {}).get('core_subject', '')
                       for c in events.get('cards', {}).values()]

    # 按主题聚类：只有同主题≥min_high条才进建卡判断（防零散新闻拼凑误建卡）
    groups = cluster_candidates(candidates)
    active_n = len([c for c in events['cards'].values() if not c.get('archived')])
    print('主题聚类:', {k: len(v) for k, v in groups.items()})
    qualified = {k: v for k, v in groups.items() if len(v) >= a.min_high and k != '其他'}
    if not qualified:
        ilog.append({'date': time.strftime('%Y-%m-%d'), 'n_candidates': len(candidates),
                     'decision': 'skip', 'reason': '无主题聚集≥%d条(聚类:%s)' % (a.min_high, {k: len(v) for k, v in groups.items()})})
        print('[incubate] 跳过：无主题聚集≥%d条' % a.min_high)
    elif active_n >= MAX_ACTIVE:
        ilog.append({'date': time.strftime('%Y-%m-%d'), 'n_candidates': len(candidates),
                     'decision': 'skip', 'reason': '活跃卡已满(%d/%d)' % (active_n, MAX_ACTIVE)})
        print('[incubate] 跳过：活跃卡已满 %d/%d' % (active_n, MAX_ACTIVE))
    else:
        for theme, cluster in qualified.items():
            # v2【铁律1硬拦截】整组都是纯行情/单点数据 → 连 LLM 都不用调，直接驳回
            neg_titles = [n['title'] for n in cluster if is_pure_market_noise(n['title'])]
            # ≥50% 是纯行情/单点数据 → 整组驳回（原油组 3/4、贵金属组 3/4 都在此拦下）
            if len(neg_titles) / len(cluster) >= 0.5:
                ilog.append({'date': time.strftime('%Y-%m-%d'), 'theme': theme,
                             'n_candidates': len(cluster), 'decision': 'rejected',
                             'reason': '纯行情/单点数据占比%d%%(负面清单硬拦截)' % round(len(neg_titles)/len(cluster)*100),
                             'rejected_titles': neg_titles[:3]})
                print('[incubate] 主题[%s]驳回：纯行情占比%d%%(负面清单)'
                      % (theme, round(len(neg_titles)/len(cluster)*100)))
                continue
            card, ch, reason = try_incubate(cluster, existing_themes)
            ilog.append({'date': time.strftime('%Y-%m-%d'), 'theme': theme, 'n_candidates': len(cluster),
                         'decision': 'created' if card else 'rejected', 'reason': reason, 'channel': ch})
            if card:
                events['cards'][card['id']] = card
                active_n += 1
                print('[incubate] 新卡:', card['title'], '| 主题', theme, '|', ch)
            else:
                print('[incubate] 主题[%s]未建卡: %s' % (theme, reason))
            if active_n >= MAX_ACTIVE:
                print('[incubate] 已达卡上限，停止建卡')
                break

    events['archive_policy_log'] = logs
    events['generated'] = time.strftime('%Y-%m-%d %H:%M')
    if not a.dry_run:
        json.dump(events, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print('已写', a.out)
    else:
        print('[dry-run] 未写文件')

if __name__ == '__main__':
    main()
