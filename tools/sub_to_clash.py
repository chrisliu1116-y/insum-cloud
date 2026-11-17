# -*- coding: utf-8 -*-
"""
机场订阅 -> Clash/mihomo proxies YAML 转换器（纯本地，零外发）

用途：机场订阅常返回 Base64 编码的 ss:// vmess:// vless:// trojan:// 分享链接，
mihomo 的 proxy-providers type:http 读不了这种格式（报 file doesn't have any proxy）。
本脚本把订阅内容解析成结构化的 Clash proxies 列表，供 mihomo 直接加载。

用法:
    python sub_to_clash.py <订阅内容文件> <输出YAML文件>

输出 YAML 形如:
    proxies:
      - name: "HK-01"
        type: ss
        server: 1.2.3.4
        port: 443
        cipher: aes-256-gcm
        password: "xxx"
"""
import sys, os, json, base64, urllib.parse, re

def b64decode(s):
    """宽容的 base64 解码：补 padding、兼容 urlsafe、忽略空白。"""
    if not s:
        return b''
    s = re.sub(r'\s+', '', s)
    s += '=' * (-len(s) % 4)
    for fn in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return fn(s)
        except Exception:
            continue
    return b''

def url_b64decode(s):
    """分享链接里的 base64 段（可能 URL-safe）。"""
    return b64decode(s).decode('utf-8', 'replace')

def uniq_name(name, seen):
    base = name or 'Node'
    n = base
    i = 2
    while n in seen:
        n = '%s-%d' % (base, i)
        i += 1
    seen.add(n)
    return n

def parse_ss(url, seen):
    # ss://BASE64(method:password@host:port)#name
    # ss://BASE64(method:password)@host:port#name  (SIP002)
    m = re.match(r'^ss://([^#@]+)(?:@([^#:]+):(\d+))?(?:#(.*))?$', url.strip())
    if not m:
        return None
    blob, host2, port2, frag = m.group(1), m.group(2), m.group(3), m.group(4)
    name = urllib.parse.unquote(frag) if frag else None
    decoded = url_b64decode(blob)
    if '@' in decoded:
        # 老式：method:password@host:port
        if ':' not in decoded:
            return None
        cred, hp = decoded.rsplit('@', 1)
        if ':' not in hp:
            return None
        method, password = cred.split(':', 1)
        host, port = hp.rsplit(':', 1)
    else:
        # SIP002：blob 是 method:password，host/port 在 URL 里
        if host2 is None or port2 is None or ':' not in decoded:
            return None
        method, password = decoded.split(':', 1)
        host, port = host2, port2
    try: port = int(port)
    except Exception: return None
    return {'name': uniq_name(name or 'SS-%s:%s' % (host, port), seen),
            'type': 'ss', 'server': host, 'port': port,
            'cipher': method, 'password': password, 'udp': True}

def parse_vmess(url, seen):
    if not url.lower().startswith('vmess://'):
        return None
    blob = url[8:].strip()
    txt = url_b64decode(blob)
    try:
        d = json.loads(txt)
    except Exception:
        return None
    if 'add' not in d or 'port' not in d or 'id' not in d:
        return None
    out = {'name': uniq_name(d.get('ps') or 'VM-%s' % d['add'], seen),
           'type': 'vmess', 'server': str(d['add']), 'port': int(d['port']),
           'uuid': str(d['id']), 'alterId': int(d.get('aid', 0)),
           'cipher': d.get('scy') or 'auto', 'udp': True}
    # TLS
    if str(d.get('tls', '')) == 'tls':
        out['tls'] = True
        if d.get('sni'): out['servername'] = str(d['sni'])
        elif d.get('host'): out['servername'] = str(d['host'])
    # network
    net = str(d.get('net', '')).lower()
    if net == 'ws':
        out['network'] = 'ws'
        opts = {}
        if d.get('path'): opts['path'] = str(d['path'])
        h = d.get('host')
        if h: opts['headers'] = {'Host': str(h)}
        if opts: out['ws-opts'] = opts
    elif net == 'grpc':
        out['network'] = 'grpc'
        if d.get('path'): out['grpc-opts'] = {'grpc-service-name': str(d['path'])}
    elif net == 'h2':
        out['network'] = 'h2'
        opts = {}
        if d.get('path'): opts['path'] = str(d['path'])
        if d.get('host'): opts['headers'] = {'Host': str(d['host'])}
        if opts: out['h2-opts'] = opts
    return out

def parse_uri_proxy(url, seen, scheme):
    """vless:// / trojan:// / hysteria2:// 共用解析。"""
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return None
    host = u.hostname
    port = u.port
    if not host or not port:
        return None
    name = urllib.parse.unquote(u.fragment) if u.fragment else None
    q = urllib.parse.parse_qs(u.query)
    def qv(k, default=None):
        v = q.get(k)
        return v[0] if v else default
    if scheme == 'vless':
        out = {'name': uniq_name(name or 'VL-%s' % host, seen),
               'type': 'vless', 'server': host, 'port': port,
               'uuid': urllib.parse.unquote(u.username), 'udp': True}
        if qv('security', '') in ('tls', 'reality'):
            out['tls'] = True
            if qv('sni'): out['servername'] = qv('sni')
            if qv('pbk'): out['reality-opts'] = {'public-key': qv('pbk'), 'short-id': qv('sid', '')}
            if qv('fp'): out['client-fingerprint'] = qv('fp')
        net = qv('type', 'tcp')
        if net == 'ws':
            out['network'] = 'ws'
            opts = {}
            if qv('path'): opts['path'] = qv('path')
            if qv('host'): opts['headers'] = {'Host': qv('host')}
            if opts: out['ws-opts'] = opts
        elif net == 'grpc':
            out['network'] = 'grpc'
            if qv('serviceName'): out['grpc-opts'] = {'grpc-service-name': qv('serviceName')}
        return out
    if scheme == 'trojan':
        out = {'name': uniq_name(name or 'TJ-%s' % host, seen),
               'type': 'trojan', 'server': host, 'port': port,
               'password': urllib.parse.unquote(u.username), 'udp': True}
        if qv('sni'): out['sni'] = qv('sni')
        if qv('alpn'): out['alpn'] = [qv('alpn')]
        return out
    if scheme in ('hysteria2', 'hy2'):
        out = {'name': uniq_name(name or 'HY-%s' % host, seen),
               'type': 'hysteria2', 'server': host, 'port': port,
               'password': urllib.parse.unquote(u.username) or qv('auth', ''), 'udp': True,
               'skip-cert-verify': True}
        if qv('sni'): out['sni'] = qv('sni')
        alpn = qv('alpn')
        if alpn:
            out['alpn'] = [a for a in alpn.split(',') if a]
        # 带宽字段（可选，机场常带）
        for key in ('up', 'down'):
            if qv(key): out[key] = qv(key)
        return out
    return None

PARSERS = [
    ('ss://', parse_ss),
    ('vmess://', parse_vmess),
    ('vless://', lambda u, s: parse_uri_proxy(u, s, 'vless')),
    ('trojan://', lambda u, s: parse_uri_proxy(u, s, 'trojan')),
    ('hysteria2://', lambda u, s: parse_uri_proxy(u, s, 'hysteria2')),
    ('hy2://', lambda u, s: parse_uri_proxy(u, s, 'hysteria2')),
]

def looks_like_yaml(text):
    t = text.lstrip('\ufeff \n\t')
    return t.startswith('proxies:') or t.startswith('proxy-providers:') or '\nproxies:' in text[:2000]

def extract_from_yaml(text):
    """订阅本身就是 Clash YAML：抽出 proxies 段（简单文本解析，避免依赖 PyYAML 版本差异）。"""
    try:
        import yaml
        d = yaml.safe_load(text)
        return d.get('proxies') or []
    except Exception:
        return []

def maybe_gunzip(data):
    """gzip magic 1f8b 则解压（机场订阅常 gzip 后再 base64）。"""
    if data[:2] == b'\x1f\x8b':
        try:
            import gzip
            return gzip.decompress(data)
        except Exception:
            return data
    return data

def convert(raw_text):
    text = raw_text.strip()
    nodes = []
    seen = set()
    # 1) 直接就是 YAML（解析出节点才 return；解析失败要继续走分享链接路径，
    #    机场订阅落地页常含 proxies: 字样但不是合法 YAML，之前在这里提前 return 导致 0 节点）
    if looks_like_yaml(text):
        for p in extract_from_yaml(text):
            if isinstance(p, dict) and p.get('server'):
                p = dict(p)
                p['name'] = uniq_name(p.get('name'), seen)
                nodes.append(p)
        if nodes:
            return nodes
    # 2) 尝试整体 base64 解码（含 gzip 压缩的订阅）
    decoded = maybe_gunzip(b64decode(text)).decode('utf-8', 'replace')
    lines = []
    for candidate in (decoded, text):
        ls = [l.strip() for l in candidate.splitlines() if l.strip()]
        if any(any(l.lower().startswith(s) for s, _ in PARSERS) for l in ls):
            lines = ls
            break
    if not lines:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
    # 3) 逐行解析分享链接
    for line in lines:
        ll = line.lower()
        for scheme, parser in PARSERS:
            if ll.startswith(scheme):
                try:
                    node = parser(line, seen)
                    if node: nodes.append(node)
                except Exception:
                    pass
                break
    return nodes

def _scalar(v):
    """标量序列化：bool/数字原样，其余加引号。"""
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return str(v)
    return '"%s"' % str(v).replace('"', "'")

def to_yaml(nodes):
    # 手写 YAML：关键要正确处理 list（alpn 等必须是真数组，写成字符串会导致
    # mihomo 报 "'alpn' is not a slice" 直接加载失败）。
    out = ['proxies:']
    for p in nodes:
        out.append('  - name: "%s"' % str(p.get('name', '')).replace('"', "'"))
        for k, v in p.items():
            if k == 'name':
                continue
            if isinstance(v, list):
                # 顶层列表：alpn: ["h3","http/1.1"]
                inner = ', '.join(_scalar(x) for x in v)
                out.append('    %s: [%s]' % (k, inner))
            elif isinstance(v, dict):
                out.append('    %s:' % k)
                for kk, vv in v.items():
                    if isinstance(vv, (dict, list)):
                        out.append('      %s:' % kk)
                        if isinstance(vv, list):
                            for item in vv:
                                if isinstance(item, dict):
                                    first = True
                                    for ik, iv in item.items():
                                        prefix = '        - ' if first else '          '
                                        out.append('%s%s: %s' % (prefix, ik, _scalar(iv)))
                                        first = False
                                else:
                                    out.append('        - %s' % _scalar(item))
                        else:
                            for kkk, vvv in vv.items():
                                out.append('        %s: %s' % (kkk, _scalar(vvv)))
                    else:
                        out.append('      %s: %s' % (kk, _scalar(vv)))
            else:
                out.append('    %s: %s' % (k, _scalar(v)))
    return '\n'.join(out) + '\n'

def deep_decode(data):
    """迭代剥掉 base64 / gzip / deflate 混合编码层，返回可读文本。

    机场订阅的编码组合五花八门：base64(纯文本)、base64(gzip(文本))、
    gzip(base64(文本))、甚至 zlib 压缩。这里从原始【字节】出发，
    每轮按魔数/字符集识别并剥一层，最多 8 轮，直到文本里出现
    可识别的节点协议或 YAML 结构。
    """
    import zlib
    if isinstance(data, str):
        data = data.encode('utf-8', 'replace')
    seen = set()
    for _ in range(8):
        if not data:
            break
        sig = data[:4]
        # 1) gzip 魔数 1f8b
        if data[:2] == b'\x1f\x8b':
            try:
                import gzip
                out = gzip.decompress(data)
                if out != data and out[:4] not in seen:
                    seen.add(out[:4]); data = out; continue
            except Exception:
                pass
        # 2) zlib 魔数 78xx (78 01/9c/da/5e)
        if data[0] == 0x78 and data[1] in (0x01, 0x9c, 0xda, 0x5e):
            try:
                out = zlib.decompress(data)
                if out != data and out[:4] not in seen:
                    seen.add(out[:4]); data = out; continue
            except Exception:
                pass
        # 3) 看起来是 base64（只含 base64 字符、够长）→ 解码
        stripped = re.sub(rb'\s+', b'', data)
        if (len(stripped) >= 24 and
                re.fullmatch(rb'[A-Za-z0-9+/=_%-]+', stripped)):
            try:
                pad = stripped + b'=' * (-len(stripped) % 4)
                out = base64.b64decode(pad, validate=True)
                if out and out != data and out[:4] not in seen:
                    seen.add(out[:4]); data = out; continue
            except Exception:
                pass
        break
    # 最终转文本
    txt = data.decode('utf-8', 'replace')
    # 若仍有替换字符且是 latin 可读，尝试 latin-1
    if '\ufffd' in txt:
        alt = data.decode('latin-1', 'replace')
        if alt.count('\ufffd') < txt.count('\ufffd'):
            txt = alt
    return txt

def main():
    if len(sys.argv) < 3:
        print('usage: python sub_to_clash.py <input> <output>', file=sys.stderr)
        sys.exit(2)
    # 二进制读取，保留 gzip/zlib 魔数（文本模式会破坏二进制）
    raw_bytes = open(sys.argv[1], 'rb').read()
    raw = deep_decode(raw_bytes)
    nodes = convert(raw)
    open(sys.argv[2], 'w', encoding='utf-8').write(to_yaml(nodes))
    from collections import Counter
    from node_filter import is_apac, EXCLUDE_RE
    types = Counter(n.get('type') for n in nodes)
    valid = [n for n in nodes if n.get('name') and not EXCLUDE_RE.search(n['name'])]
    geo = sum(1 for n in valid if is_apac(n.get('name')))
    print('解析节点 %d 个（剔除信息伪节点后有效 %d）协议分布 %s，亚太命名 %d 个' % (
        len(nodes), len(valid), dict(types), geo), file=sys.stderr)

if __name__ == '__main__':
    main()
