# -*- coding: utf-8 -*-
"""mihomo 启动后确保所有 provider 都拉到节点（修复间歇 EOF 导致的 0 节点）。

原理：mihomo 原生 provider 首次拉取偶尔因网络抖动 EOF 失败（日志可见），
失败后要等 interval(3600s) 才重试，导致该源整轮 0 节点。
本脚本轮询各 provider 节点数，对 0 节点的调用管理 API 强制 update，
最多重试 N 次，直到拿到节点。全程不打印订阅链接/节点名，只打印数量。

用法：python3 tools/ensure_providers.py
"""
import os, sys, time, json, urllib.request, urllib.error

CTRL = 'http://127.0.0.1:9090'

def api(path, method='GET'):
    req = urllib.request.Request(CTRL + path, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode('utf-8', 'replace')
            return r.status, (json.loads(raw) if raw.strip().startswith(('{', '[')) else raw)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None

def counts():
    """返回 {provider名: 节点数}"""
    st, d = api('/providers/proxies')
    if st != 200 or not isinstance(d, dict):
        return {}
    out = {}
    for k, v in (d.get('providers') or {}).items():
        if k.startswith('up'):
            out[k] = len(v.get('proxies') or [])
    return out

def force_update(name):
    """强制刷新单个 provider（mihomo API: PUT /providers/proxies/{name}/update?force=true）"""
    st, _ = api('/providers/proxies/%s/update?force=true' % name, method='PUT')
    return st == 204 or st == 200

def main():
    max_retry = int(os.environ.get('PROVIDER_RETRY', '5'))
    interval = int(os.environ.get('PROVIDER_RETRY_SEC', '4'))
    # 首次等待 provider 拉取
    for w in range(10):
        c = counts()
        if c and all(v > 0 for v in c.values()):
            break
        time.sleep(2)

    for attempt in range(1, max_retry + 1):
        c = counts()
        empty = [k for k, v in c.items() if v == 0]
        if not empty:
            print('所有 provider 均有节点:', {k: v for k, v in sorted(c.items())})
            return 0
        print('第%d轮：以下 provider 0 节点，强制刷新: %s' % (attempt, empty))
        for name in empty:
            ok = force_update(name)
            print('  %s 强制刷新 %s' % (name, 'OK' if ok else '失败'))
        time.sleep(interval)

    c = counts()
    print('重试结束，最终各源节点数:', {k: v for k, v in sorted(c.items())})
    empty = [k for k, v in c.items() if v == 0]
    if empty:
        print('::warning::以下订阅源重试 %d 次后仍 0 节点（链接可能失效/需鉴权）: %s' % (max_retry, empty))
    return 0  # 不阻断跑批，由后续 auto 组节点数护栏决定是否继续

if __name__ == '__main__':
    sys.exit(main())
