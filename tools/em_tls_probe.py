# -*- coding: utf-8 -*-
"""v479【东财TLS指纹30秒极简探测·仅GHA云端】
4组合只打 clist pz=1 小包，全局30秒硬限。只读探测，不跑批、不提交。
组合1 requests(OpenSSL)+代理 / 2 requests+GHA直连 / 3 curl_cffi换指纹+http1.1+代理 / 4 mihomo组类型核查。
"""
import os, sys, time, json, socket, urllib.request, urllib.error
T0 = time.time()
def left(): return 30 - (time.time() - T0)
def tag(*a): print('[EM-PROBE]', *a, flush=True)
if left() <= 0: sys.exit(0)

# 东财 clist 最小包（pz=1），只读
EM = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&po=1&np=1&fltt=2&invt=2'
      '&fid=f3&fs=m:0+t:6&fields=f12,f14,f3')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
HDR = {'User-Agent': UA, 'Accept': '*/*', 'Referer': 'https://quote.eastmoney.com/'}
PROXY = 'http://127.0.0.1:7890'

def verdict(resp_body, status):
    b = (resp_body or '')[:200]
    if status == 200 and ('data' in b or '"rc"' in b): return 'OK', b
    if 'BAD_DECRYPT' in b or status in (0, None): return 'BAD_DECRYPT/EMPTY', b
    return 'HTTP%s'%status, b

# ---------- 组合1+2: 标准 requests (OpenSSL) ----------
try:
    import requests as stdreq
    tag('requests版本', stdreq.__version__)
    for name, proxies in [('1_requests_OpenSSL+代理', {'http': PROXY, 'https': PROXY}),
                          ('2_requests_OpenSSL+GHA直连', None)]:
        if left() < 4: break
        try:
            r = stdreq.get(EM, headers=HDR, proxies=proxies, timeout=min(6, max(1, left()-1)))
            v, b = verdict(r.text, r.status_code)
            tag(name, '=>', v, '| server=', str(r.headers.get('server'))[:30], '|', b[:200])
        except Exception as e:
            tag(name, '=> EXC', type(e).__name__, str(e)[:120])
except Exception as e:
    tag('requests不可用', str(e)[:100])

# ---------- 组合3: curl_cffi 换指纹 + http1.1 + 代理 ----------
try:
    from curl_cffi import requests as cffi
    for imp in ('safari15_5', 'edge101'):
        if left() < 5: break
        try:
            r = cffi.get(EM, headers=HDR, impersonate=imp, http_version=1,
                         proxies={'http': PROXY, 'https': PROXY}, timeout=min(6, max(1, left()-1)))
            v, b = verdict(r.text, r.status_code)
            tag('3_curl_cffi_%s_http1.1+代理'%imp, '=>', v, '|', b[:120])
        except Exception as e:
            tag('3_curl_cffi_%s'%imp, '=> EXC', type(e).__name__, str(e)[:120])
except Exception as e:
    tag('curl_cffi不可用', str(e)[:100])

# ---------- 组合4: mihomo 组类型核查(管理API,不打东财) ----------
try:
    import requests as stdreq
    base = 'http://127.0.0.1:9090'
    js = stdreq.get(base + '/proxies', timeout=3).json().get('proxies', {})
    g = js.get('auto', {})
    tag('4_mihomo_auto组 type=', g.get('type'), '| all节点数=', len(g.get('all') or []))
    tag('4_mihomo策略: load-balance=分摊 / url-test=单最快',
        '结论=', 'load-balance轮询' if g.get('type') == 'load-balance' else ('url-test单节点' if g.get('type') == 'url-test' else str(g.get('type'))))
except Exception as e:
    tag('4_mihomo管理API不可达', str(e)[:100])

# ---------- 二次确认：requests直连 clist pz=100 + kline(验证非pz=1侥幸) ----------
try:
    import requests as stdreq
    if left() < 8: raise RuntimeError('time left low')
    EM100 = 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6&fields=f12,f14,f3'
    r = stdreq.get(EM100, headers=HDR, timeout=min(8, max(2, left()-2)))
    tag('5_requests直连_clist_pz100 => HTTP', r.status_code, '| diff条数=', len((r.json().get('data') or {}).get('diff') or []))
    KL = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=0.000001&fields1=f1&fields2=f51,f52&klt=101&fqt=1&lmt=5&end=20500101'
    r2 = stdreq.get(KL, headers=HDR, timeout=min(8, max(2, left()-2)))
    tag('6_requests直连_kline_lmt5 => HTTP', r2.status_code, '| klines=', len(((r2.json().get('data') or {}).get('klines')) or []))
except Exception as e:
    tag('5/6二次确认 EXC', type(e).__name__, str(e)[:100])

tag('探测结束 用时%.1fs'%(time.time()-T0))
