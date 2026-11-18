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
        'rules': ['MATCH,auto-final'],   # v432：最终出口走 fallback（亚太主池优先，全挂切欧美备用）
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
    # 亚太近端过滤（用户定稿正则）+ 信息提示节点排除；由核心原生应用，不再手写解析
    apac = (r'(?i)(香港|HK|HKG|🇭🇰|台湾|台北|新北|TW|TPE|🇹🇼|'
            r'新加坡|狮城|SG|SIN|🇸🇬|'
            r'日本|东京|大阪|JP|NRT|KIX|🇯🇵|'
            r'韩国|首尔|KR|ICN|🇰🇷)')
    junk = r'(?i)(过期|剩余|流量|官网|重置|到期|续费|余额|套餐|购买|订阅)'
    # 主池：亚太近端节点（load-balance）
    cfg['proxy-groups'].append({
        'name': 'auto', 'type': 'load-balance', 'use': names,
        'filter': apac, 'exclude-filter': junk,
        'url': 'https://cp.cloudflare.com/generate_204', 'interval': 300,
        'strategy': 'round-robin',
    })
    # v432 方案A：欧美/小地区备用池。用 exclude-filter 排除亚太+伪节点（RE2 不支持负向 lookahead，
    # 但 exclude-filter 是 mihomo 原生字段，等价于「非亚太」）。正常跑批不用它，零影响。
    # 备用池排除项 = 亚太命中 | 伪节点命中（一个完整正则，避免拼接破坏语义）
    backup_exclude = (r'(?i)(香港|HK|HKG|🇭🇰|台湾|台北|新北|TW|TPE|🇹🇼|'
                      r'新加坡|狮城|SG|SIN|🇸🇬|'
                      r'日本|东京|大阪|JP|NRT|KIX|🇯🇵|'
                      r'韩国|首尔|KR|ICN|🇰🇷|'
                      r'过期|剩余|流量|官网|重置|到期|续费|余额|套餐|购买|订阅)')
    cfg['proxy-groups'].append({
        'name': 'auto-backup', 'type': 'load-balance', 'use': names,
        'exclude-filter': backup_exclude,
        'url': 'https://cp.cloudflare.com/generate_204', 'interval': 300,
        'strategy': 'round-robin',
    })
    # 最终出口：fallback 优先主池，主池全部不可用时才切备用池
    cfg['proxy-groups'].append({
        'name': 'auto-final', 'type': 'fallback',
        'proxies': ['auto', 'auto-backup'],
        'url': 'https://cp.cloudflare.com/generate_204', 'interval': 60,
        'fallback-threshold': 50,
    })
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print('原生 provider 配置已生成：%d 个 provider' % len(urls))

if __name__ == '__main__':
    main()
