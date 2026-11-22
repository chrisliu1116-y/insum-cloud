# -*- coding: utf-8 -*-
"""多通道免费模型有序降级链（OpenAI 兼容），供 GHA 快讯/卡片链路共用。

设计要点（2026-11-18 实测）：
  · 免费档几乎都是 **1 并发**，所以不做"并发竞速"（会互抢配额触发 429），
    而是按实测速度排序做**有序故障转移**：快的当主，挂了才切下一个。
  · 通道顺序（实测）：
      1) 智谱 glm-4-flash-250414        → 200 / 0.6s（最快）
      2) Google gemini-flash-lite-latest → 200 / 快（v1beta 别名）
      3) Google gemini-3.1-flash-lite    → 200 / 快
      4) 本地规则兜底（本模块抛 AllModelsFailed，调用方回退）
  · 已淘汰：glm-4.7-flash（429 拥挤）、glm-4.5-flash（4.7s 空回复）、
    gemini-2.5-flash-lite / 2.5-flash（该 key 404 no longer available）。

凭据从环境变量读：ZHIPU_KEY / GEMINI_KEY；绝不打印 key 明文。
【v2 自动检测】每个通道在调用前检查自己的 key 环境变量：未配置则静默跳过该路
（不报错、不浪费超时），两路全不可用/全失败时抛 AllModelsFailed，调用方平滑回退
本地确定性规则。status() 可查每路"已配置/未配置"，供前端状态栏与自检页显示。
用法：
  from llm_pool import chat, status
  text, channel = chat("把这条新闻判为 S/A/B/C", max_tokens=200)  # 全挂抛 AllModelsFailed
  available = status()  # [{'channel':..., 'configured':bool}, ...]
"""
import os, json, urllib.request, urllib.error, time

ZHIPU_URL = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
ZHIPU_MODEL = 'glm-4-flash-250414'
GEMINI_BASE = 'https://generativelanguage.googleapis.com/v1beta/models/'

class AllModelsFailed(Exception):
    pass

def _load_env_file():
    p = 'E:/WorkBuddy工作区/_tools/llm.env'
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k, v)
_load_env_file()

def _zhipu(model):
    def call(messages, max_tokens, timeout):
        key = os.environ.get('ZHIPU_KEY', '')
        if not key: raise RuntimeError('no ZHIPU_KEY')
        body = {'model': model, 'messages': messages,
                'max_tokens': max_tokens, 'thinking': {'type': 'disabled'}}
        req = urllib.request.Request(ZHIPU_URL, data=json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        return d['choices'][0]['message']['content'] or '', 'zhipu/' + model
    return call

def _gemini(model):
    def call(messages, max_tokens, timeout):
        key = os.environ.get('GEMINI_KEY', '')
        if not key: raise RuntimeError('no GEMINI_KEY')
        parts = [{'role': 'user', 'parts': [{'text': m.get('content', '')}]} for m in messages]
        # v449【按模型版本关思考·防后台深度思考拖慢兜底】(2026-11-19 实测):
        #   · Gemini 3.x → thinkingConfig.thinkingLevel='minimal'（实测 OK 2.07s；
        #     不带该参数曾遇 503 高需求，minimal 走轻量通道反而稳）。
        #   · Gemini 2.5 → thinkingConfig.thinkingBudget=0（若启用 2.5 系列时用）。
        #   · flash-lite 系列(如 gemini-flash-lite-latest)本身无思考能力，
        #     **绝不能传** thinkingConfig —— 实测传 thinkingBudget:0 返回 400 INVALID_ARGUMENT。
        gc = {'maxOutputTokens': max_tokens}
        if model.startswith('gemini-3'):
            gc['thinkingConfig'] = {'thinkingLevel': 'minimal'}
        elif model.startswith('gemini-2.5') and 'flash' in model:
            gc['thinkingConfig'] = {'thinkingBudget': 0}
        body = {'contents': parts, 'generationConfig': gc}
        url = GEMINI_BASE + model + ':generateContent?key=' + key
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        return d['candidates'][0]['content']['parts'][0]['text'] or '', 'gemini/' + model
    return call

# 有序降级链：按实测速度排；每项 (通道名, 调用函数, 所需key环境变量)
# 两路 key：ZHIPU_KEY(智谱) / GEMINI_KEY(Google AI Studio)。同一 key 下多模型共用。
CHANNELS = [
    ('zhipu/' + ZHIPU_MODEL,            _zhipu(ZHIPU_MODEL),   'ZHIPU_KEY'),
    ('gemini/gemini-flash-lite-latest', _gemini('gemini-flash-lite-latest'), 'GEMINI_KEY'),
    ('gemini/gemini-3.1-flash-lite',    _gemini('gemini-3.1-flash-lite'),    'GEMINI_KEY'),
]

def _configured(key_env):
    return bool(os.environ.get(key_env, '').strip())

def status():
    """返回每路配置状态(不含key明文)，供前端状态栏/自检页显示。
    返回 [{'channel':..., 'key_env':..., 'configured':bool}, ...] + 兜底项。"""
    seen = set()
    out = []
    for name, _, kv in CHANNELS:
        provider = name.split('/')[0]
        if provider in seen:  # 同一 key 的多模型只列一次配置状态
            continue
        seen.add(provider)
        out.append({'channel': provider, 'key_env': kv, 'configured': _configured(kv)})
    out.append({'channel': 'local-rule', 'key_env': None, 'configured': True})
    return out

def any_configured():
    """至少一路 key 已配置 → True。供调用方决定是否值得尝试 LLM。"""
    return any(_configured(kv) for _, _, kv in CHANNELS)

def chat(messages, max_tokens=300, timeout=30, verbose=True):
    """按序尝试，返回 (text, channel)。channel 供前端状态栏显示。
    · 未配置 key 的通道：静默跳过（不报错、不占超时）。
    · 两路全未配置 或 全部调用失败：抛 AllModelsFailed → 调用方平滑回退本地确定性规则。"""
    if isinstance(messages, str):
        messages = [{'role': 'user', 'content': messages}]
    errs, skipped = [], []
    for name, fn, key_env in CHANNELS:
        # 自动检测：该路 key 未配置 → 静默跳过
        if not _configured(key_env):
            skipped.append(name)
            continue
        t = time.time()
        try:
            text, ch = fn(messages, max_tokens, timeout)
            if verbose: print('[llm] %s ok %.1fs' % (ch, time.time() - t))
            return text, ch
        except urllib.error.HTTPError as e:
            errs.append('%s:HTTP%s' % (name, e.code))
        except Exception as e:
            errs.append('%s:%s' % (name, type(e).__name__))
        if verbose: print('[llm] %s failed, next' % name)
    # 区分「全未配置」与「配置了但都失败」，便于调用方日志明确
    if not errs and skipped:
        raise AllModelsFailed('all channels skipped (keys not configured): ' + ','.join(skipped))
    raise AllModelsFailed('; '.join(errs) if errs else 'no channels available')

# ============================================================
# v449【事件卡片专用·重型深度思考通道】与盘中快讯极速链严格隔离。
# 由 evolve_events.py / event_incubator.py 调用；盘中快讯仍走上面的 chat()。
# ============================================================

# 用户要求顺序 + 实测可用性(2026-11-19 探测 GEMINI_KEY /v1beta/models)：
#   名单里有 gemini-3.8-flash/3.6-flash/2.5-pro/2.5-flash，但实测：
#     · 3.8/3.6/3.5/3.7-flash → 503 高需求(免费层挤)
#     · 3.1-pro-preview       → 429 额度满
#     · 2.5-pro / 2.5-flash   → 404 no longer available to new users
#     · gemini-3-flash-preview → ✅ 200，thinkingLevel:high 生效(thought tokens=288, 3.8s)
# 故把实测可用的 3-flash-preview 放首位，其余按用户指定顺序兜底，智谱最后兜底。
HEAVY_CHAIN = [
    # (名字, 模型, 思考参数构造器)
    ('gemini/gemini-3-flash-preview', 'gemini-3-flash-preview', lambda: {'thinkingLevel': 'high'}),
    ('gemini/gemini-3.8-flash',      'gemini-3.8-flash',      lambda: {'thinkingLevel': 'high'}),
    ('gemini/gemini-3.6-flash',      'gemini-3.6-flash',      lambda: {'thinkingLevel': 'high'}),
    ('gemini/gemini-2.5-pro',        'gemini-2.5-pro',        lambda: {'thinkingBudget': 8192}),
    ('gemini/gemini-2.5-flash',      'gemini-2.5-flash',      lambda: {'thinkingBudget': 8192}),
]

def probe_models():
    """GET /v1beta/models 探测当前 KEY 实际可用模型名单（不写死旧别名）。
    返回 (all_names, heavy_names)；失败返回 ([], []) 不抛错。"""
    key = os.environ.get('GEMINI_KEY', '')
    if not key:
        return [], []
    try:
        req = urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models?key=' + key,
                                     headers={'User-Agent': 'mp'})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        names = [m['name'].replace('models/', '') for m in d.get('models', [])]
        heavy = [n for n in names if ('gemini' in n and ('flash' in n or 'pro' in n))]
        return names, heavy
    except Exception:
        return [], []

def _gemini_heavy(model, thinking_cfg):
    """重型 Gemini 单次调用（深度思考），from OpenAI-style messages → Gemini contents。"""
    def call(messages, max_tokens, timeout):
        key = os.environ.get('GEMINI_KEY', '')
        if not key: raise RuntimeError('no GEMINI_KEY')
        # 合并 system+user 到 prompt（Gemini generateContent 无独立 system 字段这里兼容）
        parts = []
        for m in messages:
            parts.append({'text': m.get('content', '')})
        body = {'contents': [{'role': 'user', 'parts': parts}],
                'generationConfig': {'maxOutputTokens': max_tokens,
                                     'thinkingConfig': thinking_cfg() if callable(thinking_cfg) else thinking_cfg}}
        url = GEMINI_BASE + model + ':generateContent?key=' + key
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        # 深度思考开启后 parts[0] 可能是 thought(true) 无 text；取第一个有 text 的 part
        parts = (d.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
        text = ''
        for p in parts:
            if p.get('text') and not p.get('thought'):
                text = p['text']; break
        if not text:
            raise RuntimeError('no text part (thinking only?): ' + json.dumps(parts)[:120])
        return text, 'gemini/' + model + '(deep)'
    return call

def chat_heavy(messages, max_tokens=2000, timeout=100, verbose=True):
    """事件卡片专用：深度思考 + 重型主力有序降级 + 智谱兜底。
    返回 (text, channel)；全挂抛 AllModelsFailed（调用方回退本地规则）。
    · 探测可用模型名单（GET /v1beta/models），不可用别名不写死在首位。
    · Gemini 3.x → thinkingLevel:high；2.5 → thinkingBudget:8192。
    · 404/429/503 自动跳下一路，绝不中断。"""
    if isinstance(messages, str):
        messages = [{'role': 'user', 'content': messages}]
    names, heavy = probe_models()
    if verbose:
        print('[llm-heavy] 探测到 %d 模型, 可用重型 %d 个' % (len(names), len(heavy)))
    errs = []
    # 1) Gemini 重型链（仅当该模型在探测名单里才尝试；探测失败则不限制，交给 404/429 兜底）
    for name, model, tcfg in HEAVY_CHAIN:
        if not _configured('GEMINI_KEY'):
            break
        if heavy and model not in heavy:   # 探测成功但此模型不在名单 → 跳过
            continue
        t = time.time()
        try:
            fn = _gemini_heavy(model, tcfg)
            text, ch = fn(messages, max_tokens, timeout)
            if verbose: print('[llm-heavy] %s ok %.1fs (deep thinking)' % (ch, time.time() - t))
            return text, ch
        except urllib.error.HTTPError as e:
            errs.append('%s:HTTP%s' % (name, e.code))
        except Exception as e:
            errs.append('%s:%s' % (name, type(e).__name__))
        if verbose: print('[llm-heavy] %s failed, next' % name)
    # 2) 智谱重型兜底：glm-z1-flash 真推理模型（实测 2.0s、<think> 思考、JSON可解析）
    #    2026-11-19 用户强制验收：绕过 Gemini 单独测这条，确认不是"名义双通道实际只用Gemini"。
    if _configured('ZHIPU_KEY'):
        for zm in ('glm-z1-flash', ZHIPU_MODEL):   # 推理模型优先，挂了退极速flash
            t = time.time()
            try:
                text, ch = _zhipu(zm)(messages, max_tokens, timeout)
                # 剥掉 glm-z1 的 <think>...</think> 思考段，只留最终输出
                import re as _re
                text = _re.sub(r'<think>[\s\S]*?</think>', '', text).strip()
                if verbose: print('[llm-heavy] 智谱重型兜底 %s ok %.1fs' % (ch, time.time() - t))
                return text, ch + '(heavy-fallback)'
            except Exception as e:
                errs.append('zhipu/%s:%s' % (zm, type(e).__name__))
    raise AllModelsFailed('; '.join(errs) if errs else 'no heavy channels available')

if __name__ == '__main__':
    print('channels:', status())
    names, heavy = probe_models()
    print('heavy models:', heavy[:12])
    text, ch = chat_heavy('深度思考后只回复：收到', max_tokens=30)
    print('HEAVY RESULT channel=%s text=%s' % (ch, text[:30]))
