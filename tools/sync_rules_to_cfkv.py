# -*- coding: utf-8 -*-
"""把 prompt_rules.md 写入 Cloudflare KV（mp_prompt_rules），供 Worker 边缘读取。

GHA 环境：CF_TOKEN / CF_ACCOUNT_ID 从 Secrets 读（绝不落明文）。
本地：从 E:/WorkBuddy工作区/_tools/cf.env 读（仅自己用）。
KV namespace: macropulse-account（与 worker.js 的 ACCT_KV 绑定同名）。

用法：
  python3 tools/sync_rules_to_cfkv.py           # 写入 KV
  python3 tools/sync_rules_to_cfkv.py --verify  # 回读校验
"""
import os, sys, json, urllib.request, urllib.error, argparse

ACCOUNT = os.environ.get('CF_ACCOUNT_ID', '')
TOKEN = os.environ.get('CF_TOKEN', '')
SNAP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prompt_rules.md')
NS_TITLE = 'macropulse-account'
KV_KEY = 'mp_prompt_rules'

def _load_local_env():
    p = 'E:/WorkBuddy工作区/_tools/cf.env'
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k, v.strip('"\''))
    global ACCOUNT, TOKEN
    ACCOUNT = os.environ.get('CF_ACCOUNT_ID', '')
    TOKEN = os.environ.get('CF_TOKEN', '')

def api(method, path, body=None, raw=False):
    data = None if body is None else (body if raw else json.dumps(body).encode())
    req = urllib.request.Request('https://api.cloudflare.com/client/v4' + path,
        data=data, method=method,
        headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def ns_id():
    r = api('GET', '/accounts/%s/storage/kv/namespaces?per_page=50' % ACCOUNT)
    for x in (r.get('result') or []):
        if x.get('title') == NS_TITLE:
            return x['id']
    raise RuntimeError('KV namespace %s not found' % NS_TITLE)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args()
    if not (ACCOUNT and TOKEN):
        _load_local_env()
    if not (ACCOUNT and TOKEN):
        print('缺少 CF_TOKEN/CF_ACCOUNT_ID，跳过（非致命）'); return 0
    nid = ns_id()
    if a.verify:
        url = '/accounts/%s/storage/kv/namespaces/%s/values/%s' % (ACCOUNT, nid, KV_KEY)
        req = urllib.request.Request('https://api.cloudflare.com/client/v4' + url,
            headers={'Authorization': 'Bearer ' + TOKEN})
        with urllib.request.urlopen(req, timeout=30) as r:
            got = r.read().decode()
        want = open(SNAP, encoding='utf-8').read()
        print('[verify] KV 快照一致' if got == want else '[verify] KV 快照不一致')
        return 0 if got == want else 1
    content = open(SNAP, 'rb').read()
    url = '/accounts/%s/storage/kv/namespaces/%s/values/%s' % (ACCOUNT, nid, KV_KEY)
    api('PUT', url, body=content, raw=True)
    print('[sync] 已写 CF KV %s（%d 字节）' % (KV_KEY, len(content)))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
