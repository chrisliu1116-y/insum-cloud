# -*- coding: utf-8 -*-
"""Phase 3：Polymarket 候选 → 前端审批队列 + 确认后 Gemini 补全三段式

红线（用户 2026-11-21 拍板）：
  · 未确认前：只写 auto_ 骨架卡(summary 用标题占位)，绝不调 Gemini
  · 只在 CF /__card-confirm 回执里的 cid，才调 llm_pool.chat_heavy(含Gemini)生成三段式
  · 活跃卡 >5 时按 heat 末位淘汰归档（无保留例外）

流程：
  1. 读 polymarket_radar_shadow.json 的 genuine_new（净新增候选）
  2. 读 CF /__card-confirm 回执（前端点「加入」才写入）
  3. 骨架入队：genuine_new 里不在 events_daily.json 的 → 写 auto_ 骨架卡
  4. 确认补全：回执里已确认且是 auto_ 骨架的 → 调 Gemini 生成 lead/rest/summary/progress_line
  5. 末位淘汰：活跃卡>5 → 归档最冷门
  6. 写回 events_daily.json（绝不写 insum-cloud 的卡池）
"""
import os, sys, json, time, re, argparse, urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CONFIRM_URL = 'https://macropulse-api.pages.dev/__card-confirm'
MAX_ACTIVE = 5


def log(*a):
    print('[radar_publish]', *a, flush=True)


def bjnow():
    return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': 'mp-evolve'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def read_confirms():
    """读 CF 确认回执（前端点加入才写）。失败返回空 → 零 Gemini 调用。"""
    try:
        d = get_json(CONFIRM_URL)
        return {str(x.get('cid')) for x in (d.get('items') or []) if x and x.get('cid')}
    except Exception as e:
        log('读回执失败:', type(e).__name__, '→ 本次零确认、零 Gemini')
        return set()


def heat(c):
    return len(c.get('facts') or []) + (0 if c.get('dormant') else 1) + (0 if c.get('archived') else 1)


def cid_for(title):
    slug = re.sub(r'[^a-z0-9]+', '', title.lower())[:18]
    return 'auto_radar_' + slug


def make_skeleton(g):
    """骨架卡：只有标题/类别/实体/CF佐证，summary 用占位——不调 Gemini。"""
    cid = cid_for(g['title'])
    return {
        'id': cid, 'icon': '📡', 'title': g['title'][:16], 'cat': 'Polymarket雷达',
        'status': '待确认', 'statusCls': 'watch', 'hot': 2,
        'scope': {'core_subject': g['title'][:100],
                  'stage_focus': (g.get('entities') or [])[:5],
                  'exclude_noise': []},
        'summary': 'Polymarket 净新增候选（$%s，CF 已验证）。待确认后生成三段式。' % g.get('vol24h'),
        'progress_line': '当前态势：待确认（Polymarket 雷达候选）',
        'facts': [g['title']], 'newsKeys': (g.get('entities') or [])[:6],
        'pred': {}, 'polymarket': {'title': g['title'], 'volume': g.get('vol24h'),
                                   'matched_at': bjnow()},
        'auto_created': True, '_radar_pending': True, 'created_at': bjnow(),
        '_dormant': False, 'archived': False,
    }


def gemini_complete(card, g):
    """确认后才调：用 chat_heavy(含Gemini)生成三段式。失败保留骨架。"""
    try:
        from llm_pool import chat_heavy
        prompt = (
            '你是事件追踪编辑。为下面这个 Polymarket 事件生成事件追踪卡的三段式。\n'
            '只输出严格 JSON: {"lead":"本轮状态(20字内)","rest":"关键看点(30字内)",'
            '"status":"阶段(6字内)","summary":"一句话摘要(40字内)"}\n'
            '事件: ' + g['title'] + '\n24h成交额: $' + str(g.get('vol24h')) + '\n'
            '距落地: ' + str(g.get('days_left')) + '天\n'
            '实体: ' + ','.join(g.get('entities') or []) + '\n'
            '国内新闻佐证: ' + (g.get('cf_titles') or '')[:200]
        )
        text, ch = chat_heavy([{'role': 'user', 'content': prompt}], max_tokens=400, timeout=90)
        m = re.search(r'\{[\s\S]*?\}', text)
        if not m:
            log('Gemini 返回无法解析:', text[:80]); return card, ch
        d = json.loads(m.group(0))
        card['summary'] = str(d.get('summary') or '')[:80] or card['summary']
        card['progress_line'] = '当前态势：' + str(d.get('lead') or '')[:30]
        card['lead'] = str(d.get('lead') or '')[:40]
        card['rest'] = str(d.get('rest') or '')[:60]
        card['status'] = str(d.get('status') or card['status'])[:10]
        card['_radar_pending'] = False
        card['confirmed_at'] = bjnow()
        card['model_channel'] = ch
        log('Gemini 补全', card['id'], '→', card['summary'][:30])
        return card, ch
    except Exception as e:
        log('Gemini 补全失败(保留骨架):', type(e).__name__, str(e)[:100])
        return card, 'none'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--radar', default=os.path.join(HERE, 'polymarket_radar_shadow.json'))
    ap.add_argument('--events', default=os.path.join(HERE, '..', 'data', 'events_daily.json'))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    with open(a.radar, encoding='utf-8') as f:
        radar = json.load(f)
    with open(a.events, encoding='utf-8') as f:
        events = json.load(f)
    cards = events.setdefault('cards', {})

    # 给 genuine_new 补 CF 佐证标题（供 Gemini）
    all_cands = {c['title']: c for c in radar.get('candidates', [])}
    genuine = []
    for g in radar.get('genuine_new', []):
        src = all_cands.get(g['title'], {})
        g = dict(g)
        g['cf_titles'] = ' / '.join(h.get('title', '') for h in (src.get('cf') or {}).get('hits', []))
        genuine.append(g)

    confirms = read_confirms()
    log('净新增候选', len(genuine), 'CF确认回执', len(confirms))

    # 1. 骨架入队（不调 Gemini）
    added = 0
    for g in genuine:
        cid = cid_for(g['title'])
        if cid in cards or any(c.get('title') == g['title'] for c in cards.values()):
            continue
        cards[cid] = make_skeleton(g)
        added += 1
        log('骨架入队', cid, g['title'][:30])
    log('骨架新增', added)

    # 2. 确认补全（只对已确认的调 Gemini）
    completed = 0
    for cid in confirms:
        c = cards.get(cid)
        if not c or not c.get('_radar_pending'):
            continue
        g = next((x for x in genuine if cid_for(x['title']) == cid), None)
        if not g:
            continue
        cards[cid], _ = gemini_complete(c, g)
        completed += 1
    log('确认补全', completed, 'Gemini调用', completed)

    # 3. 末位淘汰（活跃>5 → 归档最冷门）
    active = [cid for cid, c in cards.items() if not c.get('archived')]
    archived = []
    if len(active) > MAX_ACTIVE:
        cold = sorted(active, key=lambda cid: heat(cards[cid]))
        for cid in cold[:len(active) - MAX_ACTIVE]:
            cards[cid]['archived'] = True
            cards[cid]['_auto_archived'] = True
            cards[cid]['archived_at'] = time.strftime('%Y-%m-%d')
            archived.append(cid)
            log('末位淘汰归档', cid)

    events['generated'] = bjnow()
    events['radar_publish'] = {'generated': bjnow(), 'skeleton_added': added,
                               'gemini_completed': completed, 'archived': archived}
    if not a.dry_run:
        with open(a.events, 'w', encoding='utf-8') as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        log('已写出', a.events)
    else:
        log('dry-run 未写文件')


if __name__ == '__main__':
    main()
