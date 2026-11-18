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
        body = {'contents': parts, 'generationConfig': {'maxOutputTokens': max_tokens}}
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

if __name__ == '__main__':
    print('channels:', status())
    text, ch = chat('只回复两个字：收到', max_tokens=20)
    print('RESULT channel=%s text=%s' % (ch, text[:20]))
