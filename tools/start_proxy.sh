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

echo "[proxy] 生成原生 proxy-providers 配置（核心自己拉取，节点数与本地客户端一致）"
mkdir -p /tmp/mihomo-conf
python3 tools/make_native_config.py /tmp/mihomo-conf/config.yaml
echo "[proxy] 原生配置已生成：3 个 provider，auto 组用亚太 filter 原生过滤"

echo "[proxy] 启动 mihomo 并验证连通性（仅 Cloudflare，零东财）"
/tmp/mihomo -d /tmp/mihomo-conf > /tmp/mihomo.log 2>&1 &
echo $! > /tmp/mihomo.pid
# v433：provider 首次拉取偶发 EOF，0 节点的强制刷新重试（最多5次×4s）
python3 tools/ensure_providers.py
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
echo "[proxy] 连通 OK。各源原生节点数（核心加载，与本地客户端一致）："
curl -s http://127.0.0.1:9090/providers/proxies \
  | python3 -c "
import sys,json
d=json.load(sys.stdin).get('providers',{})
for k in sorted(x for x in d if x.startswith('up')):
    ps=d[k].get('proxies',[]); n=len(ps); idx=k[2:]
    print('  源#%s 节点=%d'%(idx,n))
    if n==0:
        print('::warning::订阅源#%s 解析出 0 个节点——该订阅链接可能已失效/需鉴权，请检查 SUB_URLS 第%s 行'%(idx,idx))
    elif n<3:
        print('::warning::订阅源#%s 仅 %d 个节点（偏少），可能服务端按请求头降级下发'%(idx,n))
"
COUNT="$(curl -s http://127.0.0.1:9090/proxies/auto \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('all',[])))")"
COUNT_BK="$(curl -s http://127.0.0.1:9090/proxies/auto-backup \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('all',[])))")"
echo "[proxy] v469 auto(亚太主池) 存活节点=$COUNT | auto-backup(备用池) 存活节点=$COUNT_BK"
if [ "${COUNT:-0}" -lt 3 ]; then echo "::error::auto 组节点不足(exclude-filter可能误杀)"; exit 1; fi
echo "[proxy] READY — 后续跑批步骤请注入 HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890"
