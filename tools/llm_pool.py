# -*- coding: utf-8 -*-
"""三路免费模型故障转移（OpenAI 兼容），供 GHA 快讯/卡片链路共用。

实测确认（2026-11-18）：
  主：智谱 glm-4-flash-250414      → 200 / 0.6s（glm-4.7-flash 实测 429 拥挤，不用作主）
  备：Google gemini-flash-lite-latest（v1beta 别名）→ 200 / 快（gemini-2.5-flash-lite 对该 key 404）
  兜底：三路全挂 → 调用方本地规则（本模块抛 AllModelsFailed）

凭据从环境变量读：ZHIPU_KEY / GEMINI_KEY
绝不打印 key 明文。用法：
  from llm_pool import chat
  text = chat("把这条新闻判为 S/A/B/C", max_tokens=200)
"""
import os, json, urllib.request, urllib.error, time

ZHIPU_URL = 'https://open.bigmodel.cn/api/paas/v4/chat/completions'
ZHIPU_MODEL = 'glm-4-flash-250414'   # 实测可用免费模型（勿改成 4.7，实测 429）
GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent'
GEMINI_MODEL = 'gemini-flash-lite-latest'  # 实测可用别名（勿用 2.5-flash-lite，该 key 404）

class AllModelsFailed(Exception):
    pass

def _load_env_file():
    """本地 PoC 从 _tools/llm.env 读；云端用环境变量，文件不存在则跳过。"""
    p = 'E:/WorkBuddy工作区/_tools/llm.env'
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k, v)

_load_env_file()

def _zhipu(messages, max_tokens, timeout):
    key = os.environ.get('ZHIPU_KEY', '')
    if not key:
        raise RuntimeError('no ZHIPU_KEY')
    body = {
        'model': ZHIPU_MODEL,
        'messages': messages,
        'max_tokens': max_tokens,
        'thinking': {'type': 'disabled'},   # 关闭思考，极速
    }
    req = urllib.request.Request(ZHIPU_URL, data=json.dumps(body).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d['choices'][0]['message']['content'] or '', 'zhipu'

def _gemini(messages, max_tokens, timeout):
    key = os.environ.get('GEMINI_KEY', '')
    if not key:
        raise RuntimeError('no GEMINI_KEY')
    # OpenAI messages → Gemini contents
    parts = []
    for m in messages:
        role = 'user' if m.get('role') != 'system' else 'user'
        parts.append({'role': role, 'parts': [{'text': m.get('content', '')}]})
    body = {'contents': parts, 'generationConfig': {'maxOutputTokens': max_tokens}}
    url = GEMINI_URL + '?key=' + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return d['candidates'][0]['content']['parts'][0]['text'] or '', 'gemini'

CHANNELS = [('zhipu', _zhipu), ('gemini', _gemini)]

def chat(messages, max_tokens=300, timeout=30, verbose=True):
    """按 主→备 顺序尝试；全挂抛 AllModelsFailed（调用方回退本地规则）。
    返回 (text, channel)。channel 供前端状态栏显示。"""
    if isinstance(messages, str):
        messages = [{'role': 'user', 'content': messages}]
    errs = []
    for name, fn in CHANNELS:
        t = time.time()
        try:
            text, ch = fn(messages, max_tokens, timeout)
            if verbose:
                print('[llm] channel=%s ok %.1fs' % (ch, time.time() - t))
            return text, ch
        except urllib.error.HTTPError as e:
            errs.append('%s:HTTP%s' % (name, e.code))
        except Exception as e:
            errs.append('%s:%s' % (name, type(e).__name__))
        if verbose:
            print('[llm] channel=%s failed, trying next' % name)
    raise AllModelsFailed('; '.join(errs))

if __name__ == '__main__':
    text, ch = chat('只回复两个字：收到', max_tokens=20)
    print('RESULT channel=%s text=%s' % (ch, text[:20]))
