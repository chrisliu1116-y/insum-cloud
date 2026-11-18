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
  "lead": "首句：≤45字，直接给当前阶段定性+最核心实质结果(例：谈判僵持：伊朗两日计划待美方回应)",
  "rest": "后续句：展开分歧卡点/细节/赔率，与lead拼成完整简介",
  "facts": ["有效事实1", "有效事实2"],
  "noisy_dropped": ["被剔除的噪音标题(最多3条)"],
  "archived": false
}
【倒金字塔·首句铁律】首页卡片列表只显示简介前两行(约42~45字)。lead 字段必须≤45字，是一个短判断句，直接给「当前阶段定性+最核心实质结果」。参考写法(照此风格)：
  · "12月加息几成定局：CME定价升至96.8%"
  · "战俘交换达成共识，下轮会谈日期仍未定"
  · "APEC今日开幕，19日中俄美元首将历史首会"
lead 绝不能塞进多个事实、绝不能超45字。rest 字段再展开分歧卡点、细节背景、关键人物的表态与倾向。代码会把 lead+rest 拼成完整 summary。绝不把最重要结果埋在后面。
【弹性长度·按信息密度自适应】全文 40~160 字，不要死卡固定字数：
  · 有实质进展/多方会谈/重大协议落地 → 写 130~160 字，把具体成果与分歧交代清楚；
  · 休会/无新进展 → 40~60 字精炼交代现状与最新定价即可，严禁为凑字数写空话套话。
  信息量决定长度，不是长度决定信息量。
【归档】仅当事实确认事件彻底落幕(峰会闭幕/协议签署)或终局合约结算才 archived=true。
【小字只做叙事·硬要求】简介(lead+rest)是「每天更新的事件叙事」，**不写任何行情/概率/价格数字**（96.8%、77.5%、布伦特100.8、美债4.3%、JOLTS752.3万等）——这些由卡片下方的实时行情块/Polymarket承载，写进每日快照会打架且过时。
写什么：用**有信息量的事实与表态**填充，比如——
  · 美联储：写「沃什称要为超级智能时代制定政策预案」「施密德警告通胀仍高、需更多紧缩」这类**官员最新表态及对加息的倾向**，而不是利率概率数字；
  · 谈判类：写「谁提了什么方案」「卡在哪个具体分歧」「下轮在哪/何时」；
  · 数据类：写「就业数据超预期强化紧缩预期」这类**定性结论**，不写具体数值。
即：把新闻里「谁说了什么、做了什么、倾向什么」讲清楚，让不点详情也能看懂事件进展；数字一律交给下方实时块。这是叙事，不是行情播报。
【时态准确·硬要求】严格按新闻原文时态和日期：新闻写"11月19日将举行"而今天是11月18日，就绝不能说成"已举行"，必须写"将于19日举行"或"明日举行"；"有望达成"不能说成"已签署"；"达成意向"不能说成"正式签署"。进行中的事件写明"进行中/待举行"，严禁把未来或进行中的事说成已完成——这是事实性错误。今天是2026-11-18。
【格式·硬要求】summary 是连续的一段话，用中文句号分隔，**绝不使用 ①②③ 或任何序号/列表符号**。
【长度自检】写完后自己数字数，必须落在 40~160 字区间；重点是首句≤45字且含核心结果，而不是凑到某个固定字数。"""

def _mask_numbers(s):
    """喂给 LLM 前把新闻里的行情/概率数字脱敏，从源头杜绝它抄进小字。
    只处理带明确单位/符号的数值，保留定性词句。"""
    s = re.sub(r'\d+(?:\.\d+)?\s*%', '（概率数字略）', s)
    s = re.sub(r'\d+(?:\.\d+)?\s*(万|亿|美元|基点|bp)', r'（数值略）\1', s)
    s = re.sub(r'\d+(?:\.\d+)?\s*(美元|元)', '（价格略）', s)
    return s

def build_user_prompt(card, facts_text, odds_text, yesterday):
    sc = SCOPES[card]
    # 不再把 odds 传给 LLM——小字不写数字，赔率交给前端实时块；
    # 新闻标题里的数字也脱敏，避免 LLM 照抄。
    return (f"事件：{sc['title']}\n"
            f"core_subject：{sc['core_subject']}\n"
            f"stage_focus：{'、'.join(sc['stage_focus'])}\n"
            f"exclude_noise：{'、'.join(sc['exclude_noise'])}\n\n"
            f"昨日卡片：{yesterday}\n\n"
            f"今日关联新闻（标题列表，数字已脱敏）：\n{_mask_numbers(facts_text)}\n\n"
            f"请输出该事件卡的新头部 JSON：用官员表态/谈判动作/倾向性等叙事写简介，不写任何数字。")

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
        # v432：优先用 lead(首句) + rest 拼接，代码层强制首句≤45字铁律
        lead = re.sub(r'[①②③④⑤]', '', out.get('lead', '')).strip()
        rest = re.sub(r'[①②③④⑤]', '', out.get('rest', '')).strip()
        if lead:
            if len(lead) > 45:
                # 超长：按逗号/顿号截到最后一个≤45字的语义点，不硬切词
                cut = None
                for sep in ['，', '；', '、', ' ']:
                    idx = lead.rfind(sep, 0, 46)
                    if idx > 15:
                        cut = lead[:idx]
                        break
                lead = (cut or lead[:45]).rstrip('，；、 ') + '…'
            if not lead.endswith(('。', '！', '？', '…')):
                lead += '。'
            out['_lead_truncated'] = len(out.get('lead','')) > 45
            summary = (lead + rest).strip()
        else:
            summary = re.sub(r'[①②③④⑤]', '', out.get('summary', '')).strip()
        out['summary'] = summary
        # 弹性长度校验：40~160 字；首句铁律：第一句≤45字且含核心结果
        slen = len(summary)
        first_sent = re.split(r'[。！？]', summary)[0]
        first_len = len(first_sent)
        need_fix = not (40 <= slen <= 160) or first_len > 45
        if need_fix:
            problems = []
            if slen < 40: problems.append('全文只有%d字(太短,至少40)' % slen)
            if slen > 160: problems.append('全文%d字(太长,最多160)' % slen)
            if first_len > 45: problems.append('第一句%d字(超过45字铁律,首句必须直接给核心结果)' % first_len)
            fix_prompt = (prompt + '\n\n【重写指令】你上次输出有问题：' + '；'.join(problems)
                          + '。请按倒金字塔重写：第一句≤45字直接给核心结果，全文40~160字按信息密度自适应，只输出完整JSON。')
            try:
                text2, ch2 = chat([{'role': 'system', 'content': SYS},
                                   {'role': 'user', 'content': fix_prompt}], max_tokens=700)
                out2 = extract_json(text2)
                if out2 and out2.get('summary'):
                    s2 = re.sub(r'[①②③④⑤]', '', out2['summary']).strip()
                    fs2 = len(re.split(r'[。！？]', s2)[0])
                    if 40 <= len(s2) <= 160 and fs2 <= 45:
                        out['summary'] = s2
                        out['model_channel'] = ch2 + '(rewritten)'
                        slen, first_len = len(s2), fs2
            except Exception:
                pass
        out['_summary_len'] = slen
        out['_first_sent_len'] = first_len
        if not (40 <= slen <= 160) or first_len > 45:
            out['_warn'] = 'summary %d chars, first sentence %d chars (target 40-160, first<=45)' % (slen, first_len)
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
        print('[%s] channel=%s status=%s len=%s first=%s' % (
            cid, out.get('model_channel'), out.get('status'),
            out.get('_summary_len'), out.get('_first_sent_len')))

    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print('已写', a.out)

if __name__ == '__main__':
    main()
