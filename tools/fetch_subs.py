# -*- coding: utf-8 -*-
"""
订阅拉取 + 转换 + 聚合（GHA 内运行，本机零网络）
流程：
  1. 从 SUB_URLS 环境变量读 N 个订阅（每行一个），不打印链接内容
  2. 每个订阅先直连拉取（UA 轮换）；若 403/失败/解析0节点，fallback 到 subconverter 公开实例
     （转换服务从其自身服务器拉订阅，能绕开 GHA 机房 IP 被机场拦的 403，并展开 provider 二级引用）
  3. deep_decode 解多层编码 -> sub_to_clash 解析节点 -> 剔除信息伪节点
  4. 聚合成 /tmp/subs/proxies_all.yaml，打印各源节点数（仅数量）
用法：SUB_URLS=... python3 tools/fetch_subs.py
"""
import os, sys, urllib.parse, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sub_to_clash import deep_decode, convert, to_yaml
from node_filter import is_apac, EXCLUDE_RE

try:
    from curl_cffi import requests as creq
    SESSION = creq.Session(impersonate='chrome120')
    def http_get(url, ua=None, timeout=45):
        h = {'User-Agent': ua or 'Clash/v1.18.0', 'Accept': '*/*'}
        try:
            r = SESSION.get(url, headers=h, timeout=timeout)
            return r.status_code, r.content
        except Exception:
            return 0, b''
except Exception:
    def http_get(url, ua=None, timeout=45):
        req = urllib.request.Request(url, headers={'User-Agent': ua or 'Clash/v1.18.0'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b''
        except Exception:
            return 0, b''

UAS = ['Clash/v1.18.0', 'Clash-verge/v1.6.0', 'mihomo/1.19.0',
       'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36']

# subconverter 自建实例（GHA 内 docker run tindy2013/subconverter，127.0.0.1:25500）。
# 绝不用公开转换服务——那会把订阅链接(含 token)泄露给第三方。
# 自建实例专门用来展开订阅#1 的 proxy-providers 二级引用、规范化订阅#2/#3。
CONVERTERS = [
    'http://127.0.0.1:25500/sub',
]

def parse_nodes(data):
    """原始字节 -> 有效节点列表（剔除伪节点）"""
    text = deep_decode(data)
    nodes = convert(text)
    return [n for n in nodes if n.get('name') and not EXCLUDE_RE.search(n['name'])]

def fetch_one(url, idx):
    """返回 (来源描述, 节点列表)"""
    # 1) 直连 + UA 轮换
    for ua in UAS:
        try:
            code, data = http_get(url, ua)
        except Exception:
            code, data = 0, b''
        if code == 200 and len(data) > 100:
            nodes = parse_nodes(data)
            if nodes:
                return ('直连%s' % ua.split('/')[0]), nodes
    # 2) fallback: subconverter 转换（target=clash + 亚太 include + 伪节点 exclude）
    enc = urllib.parse.quote(url, safe='')
    incl = urllib.parse.quote('香港|HK|台湾|TW|新加坡|SG|日本|JP|韩国|KR|Hong|Taiwan|Singapore|Japan|Korea', safe='')
    excl = urllib.parse.quote('过期|剩余|流量|官网|重置|到期|续费|余额|套餐|购买|订阅', safe='')
    for conv in CONVERTERS:
        target = '%s?target=clash&url=%s&include=%s&exclude=%s&emoji=false&list=false' % (conv, enc, incl, excl)
        try:
            code, data = http_get(target, 'Mozilla/5.0', timeout=90)
        except Exception:
            code, data = 0, b''
        if code == 200 and len(data) > 100:
            nodes = parse_nodes(data)
            if nodes:
                return ('转换%s' % conv.split('/')[2]), nodes
    return '失败', []

def main():
    raw = os.environ.get('SUB_URLS', '')
    urls = [u.strip() for u in raw.splitlines()
            if u.strip().startswith(('http://', 'https://'))]
    print('订阅数:', len(urls))
    all_nodes, seen = [], set()
    for i, url in enumerate(urls, 1):
        src, nodes = fetch_one(url, i)
        geo = sum(1 for n in nodes if is_apac(n.get('name')))
        print('订阅#%d 来源=%s 有效节点=%d 亚太=%d' % (i, src, len(nodes), geo))
        for p in nodes:
            n = p.get('name')
            if n and n not in seen:
                seen.add(n); all_nodes.append(p)
    print('合并唯一有效节点:', len(all_nodes))
    if len(all_nodes) < 3:
        print('::error::节点不足3个')
        sys.exit(1)
    os.makedirs('/tmp/subs', exist_ok=True)
    with open('/tmp/subs/proxies_all.yaml', 'w', encoding='utf-8') as f:
        f.write(to_yaml(all_nodes))
    print('已写 /tmp/subs/proxies_all.yaml')

if __name__ == '__main__':
    main()
