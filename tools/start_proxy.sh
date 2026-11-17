#!/usr/bin/env bash
# 机场代理启动器：GHA 沙盒内起 mihomo，从 SUB_URLS(Secrets) 拉 3 个订阅，
# 本地解析聚合成亚太(港台新日韩)节点池，load-balance 分摊，混合端口 7890。
# 供 breadth.yml（日常）与 proxy-live.yml（验证/正式）共用。
# 安全：订阅只从环境变量读，日志只打印节点数/UA/HTTP码，绝不打印链接内容。
set -euo pipefail

: "${SUB_URLS:?SUB_URLS 未设置}"
GITHUB_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"

echo "[proxy] 准备 mihomo 配置目录"
mkdir -p /tmp/mihomo-conf /tmp/subs

echo "[proxy] 下载 mihomo 内核"
if [ -x /tmp/mihomo ]; then
  echo "[proxy] 复用已下载内核"
else
  if [ -n "$GITHUB_TOKEN" ]; then
    URL="$(curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" \
      -H "Accept: application/vnd.github+json" \
      https://api.github.com/repos/MetaCubeX/mihomo/releases/latest \
      | python3 -c 'import sys,json; d=json.load(sys.stdin); \
        a=[x["browser_download_url"] for x in d["assets"] \
           if "linux-amd64" in x["name"] and "compatible" not in x["name"] and x["name"].endswith(".gz")]; \
        a=a or [x["browser_download_url"] for x in d["assets"] \
           if "linux-amd64-compatible" in x["name"] and x["name"].endswith(".gz")]; \
        print(a[0] if a else "")')"
  else
    URL=""
  fi
  if [ -z "$URL" ]; then
    echo "[proxy] 无 token 或取址失败，用固定 latest 下载别名"
    URL="https://github.com/MetaCubeX/mihomo/releases/latest/download/mihomo-linux-amd64.gz"
  fi
  echo "[proxy] 下载 ${URL##*/}"
  curl -fsSL "$URL" | gunzip > /tmp/mihomo && chmod +x /tmp/mihomo
fi
/tmp/mihomo -v 2>&1 | head -1 || true

echo "[proxy] 拉取并解析订阅（仅打印数量）"
i=0
while IFS= read -r url; do
  url="$(printf '%s' "$url" | tr -d '\r' | xargs || true)"
  case "$url" in http://*|https://*) ;; *) continue ;; esac
  i=$((i+1))
  got=0
  for ua in 'Clash/v1.18.0' 'Clash-verge/v1.6.0' 'mihomo/1.19.0' \
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36'; do
    code="$(curl -sSL --max-time 60 -A "$ua" -w '%{http_code}' "$url" -o "/tmp/subs/raw_$i.txt" || echo 000)"
    sz="$(wc -c < "/tmp/subs/raw_$i.txt" 2>/dev/null || echo 0)"
    echo "  订阅#$i UA=${ua%%/*} HTTP=$code 字节=$sz"
    if [ "$code" = "200" ] && [ "${sz:-0}" -gt 100 ]; then got=1; break; fi
  done
  if [ "$got" != "1" ]; then echo "[proxy] ::warning::订阅#$i 所有 UA 失败，跳过"; continue; fi
  python3 -c "
import sys,re
sys.path.insert(0,'tools')
from sub_to_clash import deep_decode
b=open('/tmp/subs/raw_$i.txt','rb').read(); d=deep_decode(b)
feat=['原始%d字%d'%(len(b),len(d)),'gzip=%s'%(b[:2]==b'\x1f\x8b'),
      'proxies段=%s'%('proxies:' in d),'YAML项=%s'%bool(re.search(r'(?m)^\s*-\s*name:',d))]
for proto in ['ss','vmess','vless','trojan','hysteria2']:
    feat.append('%s=%d'%(proto,d.lower().count(proto+'://')))
feat.append('html=%s'%bool(re.search(r'<(html|!doctype|body)',d[:500],re.I)))
feat.append('json=%s'%(d.lstrip()[:1] in ('{','[')))
feat.append('outbounds=%s'%('outbounds:' in d))
print('订阅#$i 特征: '+' '.join(feat))
keys=[]
for ln in d.splitlines()[:60]:
    s=ln.strip()
    if ':' in s and not s.startswith(('http','ss://','vmess://','vless://','trojan://')):
        k=s.split(':',1)[0].strip()
        if k and len(k)<30 and k not in keys: keys.append(k)
print('订阅#$i 顶层键名(无值):',keys[:12])
"
  python3 tools/sub_to_clash.py "/tmp/subs/raw_$i.txt" "/tmp/subs/proxies_$i.yaml"
done <<< "${SUB_URLS}"
echo "[proxy] 共拉取 $i 个订阅"

echo "[proxy] 聚合各源节点"
python3 - <<'PY'
import glob, yaml, os, sys
sys.path.insert(0,'tools')
from sub_to_clash import to_yaml, _scalar
from node_filter import is_apac, EXCLUDE_RE
proxies=[]; seen=set()
files=sorted(glob.glob('/tmp/subs/proxies_*.yaml'))
print('=== 各订阅节点数（仅数量）===')
for f in files:
    d=yaml.safe_load(open(f,encoding='utf-8')) or {}
    ps=[p for p in (d.get('proxies') or []) if p.get('name') and not EXCLUDE_RE.search(p['name'])]
    geo=sum(1 for p in ps if is_apac(p.get('name')))
    idx=os.path.basename(f).replace('proxies_','').replace('.yaml','')
    print('  订阅#%s 有效节点%d 亚太%d'%(idx,len(ps),geo))
for f in files:
    d=yaml.safe_load(open(f,encoding='utf-8')) or {}
    for p in (d.get('proxies') or []):
        n=p.get('name')
        if n and n not in seen and not EXCLUDE_RE.search(n): seen.add(n); proxies.append(p)
print('合并唯一有效节点:',len(proxies))
if len(proxies)<3:
    print('::error::代理节点不足3个'); sys.exit(1)
picked=[p['name'] for p in proxies if is_apac(p.get('name'))]
if len(picked)<3: picked=[p['name'] for p in proxies]
print('亚太池节点:',len(picked))
lines=[to_yaml(proxies).rstrip(),'',
  'mixed-port: 7890','allow-lan: false','mode: rule','log-level: warning',
  'external-controller: 127.0.0.1:9090','',
  'proxy-groups:','  - name: "auto"','    type: load-balance','    proxies:']
for n in picked: lines.append('      - %s'%_scalar(n))
lines+=['    url: https://cp.cloudflare.com/generate_204',
        '    interval: 300','    strategy: round-robin','',
        'rules:','  - MATCH,auto','']
open('/tmp/mihomo-conf/config.yaml','w',encoding='utf-8').write('\n'.join(lines))
chk=yaml.safe_load(open('/tmp/mihomo-conf/config.yaml',encoding='utf-8'))
assert chk.get('proxies') and chk.get('proxy-groups')
print('mihomo 配置自检通过')
PY

echo "[proxy] 启动 mihomo 并验证连通性（仅 Cloudflare，零东财）"
/tmp/mihomo -d /tmp/mihomo-conf > /tmp/mihomo.log 2>&1 &
echo $! > /tmp/mihomo.pid
OK=0
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" -x http://127.0.0.1:7890 \
           --max-time 10 https://cp.cloudflare.com/generate_204 || true)
  if [ "$code" = "204" ]; then OK=1; break; fi
  sleep 2
done
if [ "$OK" != "1" ]; then
  echo "::error::代理连通失败"; tail -30 /tmp/mihomo.log || true; exit 1
fi
COUNT="$(curl -s http://127.0.0.1:9090/proxies/auto \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('all',[])))")"
echo "[proxy] 连通 OK，auto 组节点 $COUNT 个"
if [ "${COUNT:-0}" -lt 3 ]; then echo "::error::auto 组节点不足"; exit 1; fi
echo "[proxy] READY — 后续跑批步骤请注入 HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890"
