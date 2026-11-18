# -*- coding: utf-8 -*-
"""生成 mihomo 原生 proxy-providers 配置（方法1：核心自己拉取解析订阅）。
用法：SUB_URLS=... python3 tools/make_native_config.py <输出路径>
不打印订阅链接明文。"""
import os, sys, json

def main():
    out = sys.argv[1]
    raw = os.environ.get('SUB_URLS', '')
    urls = [u.strip() for u in raw.splitlines() if u.strip().startswith(('http://', 'https://'))]
    cfg = {
        'mixed-port': 7890, 'allow-lan': False, 'mode': 'rule',
        'log-level': 'info', 'external-controller': '127.0.0.1:9090',
        'proxy-providers': {},
        'proxy-groups': [],
        'rules': ['MATCH,auto'],
    }
    names = []
    for i, url in enumerate(urls, 1):
        key = 'up%d' % i
        cfg['proxy-providers'][key] = {
            'type': 'http', 'url': url, 'interval': 3600,
            'header': {'User-Agent': ['clash.meta']},
            'health-check': {'enable': True, 'interval': 300,
                             'url': 'https://cp.cloudflare.com/generate_204'},
        }
        names.append(key)
    cfg['proxy-groups'].append({
        'name': 'auto', 'type': 'load-balance', 'use': names,
        'url': 'https://cp.cloudflare.com/generate_204', 'interval': 300,
        'strategy': 'round-robin',
    })
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print('原生 provider 配置已生成：%d 个 provider' % len(urls))

if __name__ == '__main__':
    main()
