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

echo "[proxy] 启动本地 subconverter（Docker，订阅不外发第三方）"
if command -v docker >/dev/null 2>&1; then
  docker run -d --name mp-subconverter -p 127.0.0.1:25500:25500 \
    tindy2013/subconverter:latest 2>/dev/null || true
  for i in $(seq 1 20); do
    if curl -fsS --max-time 3 http://127.0.0.1:25500/version >/dev/null 2>&1; then
      echo "[proxy] subconverter 就绪"; break
    fi
    sleep 2
  done
else
  echo "[proxy] ::warning::无 docker，订阅将只能用直连解析"
fi

echo "[proxy] 拉取并解析订阅（直连失败自动 fallback 本地转换器，仅打印数量）"
python3 tools/fetch_subs.py

echo "[proxy] 生成 mihomo 配置"
python3 - <<'PY'
import yaml, sys
sys.path.insert(0,'tools')
from sub_to_clash import to_yaml, _scalar
from node_filter import is_apac
d=yaml.safe_load(open('/tmp/subs/proxies_all.yaml',encoding='utf-8')) or {}
proxies=d.get('proxies') or []
picked=[p['name'] for p in proxies if is_apac(p.get('name'))]
if len(picked)<3: picked=[p['name'] for p in proxies]
print('亚太池节点:',len(picked),'/ 有效:',len(proxies))
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
