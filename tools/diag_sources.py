# -*- coding: utf-8 -*-
"""
订阅诊断：方法2(递归解包) + 方法3(UA对比) + 方法4(本地subconverter) + 生成方法1原生config
只读诊断，不改正式配置。全程不打印订阅链接/节点名明文，只打印数量。
用法：SUB_URLS=... python3 tools/diag_sources.py
"""
import os, sys, urllib.parse, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sub_to_clash import deep_decode, convert
from node_filter import is_apac, EXCLUDE_RE

try:
    from curl_cffi import requests as creq
    SESSION = creq.Session(impersonate='chrome120')
    def get(url, ua, timeout=60):
        try:
            r = SESSION.get(url, headers={'User-Agent': ua, 'Accept': '*/*'}, timeout=timeout)
            return r.status_code, r.content, dict(r.headers)
        except Exception:
            return 0, b'', {}
except Exception:
    def get(url, ua, timeout=60):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': ua})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, b'', dict(e.headers)
        except Exception:
            return 0, b'', {}

UAS = [
    ('A-clash.meta', 'clash.meta'),
    ('A-mihomo', 'mihomo/1.19.0'),
    ('B-ClashVerge177', 'ClashVerge/1.7.7'),
    ('C-Clash verge', 'Clash-verge/v1.6.0'),
    ('D-Chrome', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                 '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'),
]

def count_nodes(data):
    text = deep_decode(data)
    nodes = convert(text)
    valid = [n for n in nodes if n.get('name') and not EXCLUDE_RE.search(n['name'])]
    apac = sum(1 for n in valid if is_apac(n.get('name')))
    return len(nodes), len(valid), apac

def main():
    raw = os.environ.get('SUB_URLS', '')
    urls = [u.strip() for u in raw.splitlines() if u.strip().startswith(('http://', 'https://'))]
    print('订阅数:', len(urls))
    for i, url in enumerate(urls, 1):
        print('\n===== 订阅 #%d =====' % i)
        # 方法3：UA 对比（字节数 + 节点数）
        print('[方法3 UA对比]')
        best = None
        for label, ua in UAS:
            code, data, hdr = get(url, ua)
            clen = hdr.get('Content-Length') or len(data)
            if code == 200 and data:
                raw_n, valid_n, apac_n = count_nodes(data)
            else:
                raw_n = valid_n = apac_n = 0
            print('  %-16s HTTP=%s CL=%s 实字=%d 节点=%d 有效=%d 亚太=%d' % (
                label, code, clen, len(data), raw_n, valid_n, apac_n))
            if raw_n > 0 and (best is None or raw_n > best[0]):
                best = (raw_n, ua, data)
        # 方法2：递归解包（对最佳UA的数据再做一次 deep_decode 确认）
        if best:
            _, ua, data = best
            raw_n, valid_n, apac_n = count_nodes(data)
            deep = deep_decode(data)
            print('[方法2 递归解包] 原始格式=%s 解码后长度=%d 节点=%d 有效=%d 亚太=%d' % (
                'YAML' if 'proxies:' in deep[:2000] else 'URI',
                len(deep), raw_n, valid_n, apac_n))
        # 方法4：本地 subconverter（127.0.0.1:25500）
        enc = urllib.parse.quote(url, safe='')
        conv = 'http://127.0.0.1:25500/sub?target=clash&url=%s&emoji=false' % enc
        code, data, _ = get(conv, 'Mozilla/5.0', timeout=120)
        if code == 200 and data:
            raw_n, valid_n, apac_n = count_nodes(data)
            print('[方法4 本地转换] HTTP=200 节点=%d 有效=%d 亚太=%d' % (raw_n, valid_n, apac_n))
        else:
            print('[方法4 本地转换] HTTP=%s 失败' % code)

if __name__ == '__main__':
    main()
