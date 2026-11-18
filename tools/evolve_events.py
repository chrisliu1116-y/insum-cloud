# -*- coding: utf-8 -*-
"""事件卡片每日态势演进（GHA 云端跑，LLM 提纯三段式）。

流程：
  1) 内置 4 张卡的 Scope Schema（core_subject / stage_focus / exclude_noise）
  2) 读今日关联新闻（news.json，由抓取步骤产出）+ 昨日卡片状态（events_daily.json 或 events.js 基线）
  3) 每张卡：LLM 按 Scope 过滤噪音 → 提取 Verified Facts → 三段式重写
     （新气泡 status / 进度栏 progress_line / 简介 summary / archived / model_channel）
  4) 输出 events_daily.json（前端读它覆盖快照字段）

LLM 走 llm_pool 四通道降级；全挂则保留昨日卡片并标 channel=local-rule(未重写)。
凭据从环境变量读，不打印。
"""
import os, sys, json, time, argparse, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_pool import chat, AllModelsFailed, status as model_status

# 4 张卡的 Scope Schema（v2 演练定稿）
SCOPES = {
    'iran': {
        'title': '美伊冲突 · 霍尔木兹海峡', 'icon': '🛢️',
        'core_subject': '美伊围绕霍尔木兹海峡封锁与开放谈判的进程',
        'stage_focus': ['封锁/通航状态变化', '双方提案与回应', '斡旋方动作', '油轮安全事件', '核议题进展'],
        'exclude_noise': ['泛中东局势评论', '油价技术面分析', '其他地区冲突', '无新动作的旧闻复述'],
        'baseline_status': '僵持中',
    },
    'fed': {
        'title': '美联储政策路径', 'icon': '🏛️',
        'core_subject': '美联储 12 月 FOMC 利率决议的市场定价与官方表态',
        'stage_focus': ['利率决议/纪要/点阵图', 'FOMC 日程', '主席及票委表态', 'CME/Polymarket 概率变化', '关键经济数据'],
        'exclude_noise': ['欧央行/日银等其他央行', '泛市场评论', '股票个股新闻', '与利率无关的美股/科技新闻'],
        'baseline_status': '跟踪中',
    },
    'ukraine': {
        'title': '俄乌会谈', 'icon': '🕊️',
        'core_subject': '阿布扎比俄美乌三方会谈进程及其后续排期',
        'stage_focus': ['会谈场次与成果', '战俘/人道交换协议', '下轮会谈排期与地点', '参与方变化(欧洲上桌)', '核心分歧变化'],
        'exclude_noise': ['前线零星互袭无谈判关联', '各国立场表态无行动', '领导人其他行程', '市场评论'],
        'baseline_status': '跟踪中',
    },
    'apec': {
        'title': 'APEC 领导人会议', 'icon': '🌐',
        'core_subject': '2026 APEC 第33次领导人会议（深圳）进程与成果',
        'stage_focus': ['领导人抵离与会晤安排', '中俄美元首会晤等重大多边互动', '宣言/自贸区等成果文件', '经贸数智合作落地', '下届东道主交接'],
        'exclude_noise': ['泛APEC历史科普', '地方文旅/安保花絮', '与本次会议无关的双边旧闻', '无实质动作的官员泛论'],
        'baseline_status': '进行中',
    },
}

SYS = """你是金融终端的事件情报编辑。严格按以下规则，把给定新闻提纯成一张事件卡的头部数据。

【Scope Schema】core_subject=核心追踪主体；stage_focus=重点阶段要素；exclude_noise=明确排除的噪音。
只保留真正推动该事件阶段进展的事实，剔除噪音与擦边新闻。若无实质进展，facts 写 ["今日无实质进展"]。

【输出格式】严格 JSON（不要 markdown、不要解释）：
{
  "status": "4~6字阶段气泡，直接反映当前真实阶段(如 多国政要抵华/首轮结束·休会/12月加息几成定局)",
  "progress_line": "当前态势：阶段定性 + 最新核心动作(废除机械天数累加)",
  "summary": "80~110字三段式：①当前推进到哪个具体阶段 ②最新达成的会谈/动作/成果或卡在什么分歧 ③结合提供的赔率/行情收尾。三段用句号分隔，全文80~110字",
  "facts": ["有效事实1", "有效事实2"],
  "noisy_dropped": ["被剔除的噪音标题(最多3条)"],
  "archived": false
}
【归档】仅当事实确认事件彻底落幕(峰会闭幕/协议签署)或终局合约结算才 archived=true。
【赔率】用输入里给出的 Polymarket/行情数字，不要编造；没有就写"暂无明确终局定价"。
【时态准确·硬要求】严格按新闻原文时态和日期：新闻写"11月19日将举行"而今天是11月18日，就绝不能说成"已举行"，必须写"将于19日举行"或"明日举行"；"有望达成"不能说成"已签署"；"达成意向"不能说成"正式签署"。进行中的事件写明"进行中/待举行"，严禁把未来或进行中的事说成已完成——这是事实性错误。今天是2026-11-18。
【格式·硬要求】summary 是连续的一段话，用中文句号分隔三句，**绝不使用 ①②③ 或任何序号/列表符号**。
【字数·硬要求】summary 必须 80~110 个中文字符（含标点）。写完后自己数字数：少于80补细节，多于110删修饰词，必须落在区间内再输出。"""

def build_user_prompt(card, facts_text, odds_text, yesterday):
    sc = SCOPES[card]
    return (f"事件：{sc['title']}\n"
            f"core_subject：{sc['core_subject']}\n"
            f"stage_focus：{'、'.join(sc['stage_focus'])}\n"
            f"exclude_noise：{'、'.join(sc['exclude_noise'])}\n\n"
            f"昨日卡片：{yesterday}\n\n"
            f"今日关联新闻（标题列表）：\n{facts_text}\n\n"
            f"最新赔率/行情：{odds_text}\n\n"
            f"请输出该事件卡的新头部 JSON。")

def extract_json(text):
    """从 LLM 输出里抠出 JSON（容忍 ```json 包裹或前后文字）。"""
    text = text.strip()
    m = re.search(r'\{[\s\S]*\}', text)
    if not m: return None
    raw = m.group(0)
    # 去掉可能的尾随逗号
    raw = re.sub(r',\s*([}\]])', r'\1', raw)
    try: return json.loads(raw)
    except Exception: return None

def evolve(card, news, odds, yesterday, do_llm=True):
    sc = SCOPES[card]
    facts_text = '\n'.join('- ' + n for n in news[:30]) if news else '（今日无关联新闻）'
    prompt = build_user_prompt(card, facts_text, odds, yesterday)
    fallback = {
        'status': sc['baseline_status'],
        'progress_line': '当前态势：等待今日数据',
        'summary': yesterday or sc['core_subject'],
        'facts': [], 'noisy_dropped': [], 'archived': False,
        'model_channel': 'local-rule',
    }
    if not do_llm:
        return fallback
    try:
        text, ch = chat([{'role': 'system', 'content': SYS},
                         {'role': 'user', 'content': prompt}], max_tokens=700)
        out = extract_json(text)
        if not out:
            fallback['model_channel'] = ch + '(parse-failed)'
            return fallback
        out['model_channel'] = ch
        # 后处理：去序号标记（LLM 偶尔加 ①②③）
        summary = re.sub(r'[①②③④⑤]', '', out.get('summary', '')).strip()
        out['summary'] = summary
        # 字数不在区间(过短或过长)自动二次重写一次，仍不合格才标注
        slen = len(summary)
        if not (80 <= slen <= 110):
            direction = '补充具体细节' if slen < 80 else '删减修饰词'
            fix_prompt = (prompt + '\n\n【重写指令】你上次输出的简介只有 %d 字，'
                          '请%s，重写成严格 80~110 字的一段话（不用序号），只输出完整 JSON。' % (slen, direction))
            try:
                text2, ch2 = chat([{'role': 'system', 'content': SYS},
                                   {'role': 'user', 'content': fix_prompt}], max_tokens=700)
                out2 = extract_json(text2)
                if out2 and out2.get('summary'):
                    s2 = re.sub(r'[①②③④⑤]', '', out2['summary']).strip()
                    if 80 <= len(s2) <= 110:
                        out['summary'] = s2
                        out['model_channel'] = ch2 + '(rewritten)'
                        slen = len(s2)
            except Exception:
                pass
        out['_summary_len'] = slen
        if not (80 <= slen <= 110):
            out['_warn'] = 'summary %d chars (target 80-110)' % slen
        return out
    except AllModelsFailed as e:
        fallback['model_channel'] = 'local-rule(all-llm-failed)'
        return fallback

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--news', default='E:/WorkBuddy工作区/2026-11-03-23-29-44/data/events_news_today.json',
                    help='今日关联新闻 JSON: {card_id: [title,...]}')
    ap.add_argument('--prev', default='E:/WorkBuddy工作区/2026-11-03-23-29-44/data/events_daily.json')
    ap.add_argument('--out', default='E:/WorkBuddy工作区/2026-11-03-23-29-44/data/events_daily.json')
    ap.add_argument('--no-llm', action='store_true')
    a = ap.parse_args()

    news_map = {}
    if os.path.exists(a.news):
        try: news_map = json.load(open(a.news, encoding='utf-8'))
        except Exception: news_map = {}
    prev = {}
    if os.path.exists(a.prev):
        try: prev = json.load(open(a.prev, encoding='utf-8'))
        except Exception: prev = {}

    odds_default = {
        'iran': 'Polymarket：解除封锁37% / 通行正常8.5%',
        'fed': 'Polymarket：12月加息25bp约97%；CME 96.8%；美债10Y约4.3%',
        'ukraine': 'Polymarket：停火合约无明确终局定价',
        'apec': 'Polymarket：暂无对应终局合约；亚太概念受会议预期提振',
    }
    result = {'generated': time.strftime('%Y-%m-%d %H:%M'), 'cards': {},
              'model_channels': model_status()}
    for cid in SCOPES:
        yesterday = ''
        if cid in prev and isinstance(prev[cid], dict):
            yesterday = prev[cid].get('summary', '')
        if not yesterday:
            yesterday = SCOPES[cid]['core_subject']
        out = evolve(cid, news_map.get(cid, []), odds_default[cid], yesterday, not a.no_llm)
        result['cards'][cid] = out
        print('[%s] channel=%s status=%s summary_len=%s' % (
            cid, out.get('model_channel'), out.get('status'), out.get('_summary_len')))

    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print('已写', a.out)

if __name__ == '__main__':
    main()
