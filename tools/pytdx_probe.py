# -*- coding: utf-8 -*-
"""v482【pytdx 纯单一源 GHA 探测·仅云端·本机零请求·不改生产】
4项：①7709节点连通率/延迟 ②市场0/1/2北交所覆盖+历史深度 ③get_xdxr_info/get_finance_info
④多连接池并发20只测速。全部只读探测。
"""
import time, sys, json, socket
T0 = time.time()
def tag(*a): print('[TDX]', *a, flush=True)

try:
    from pytdx.hq import TdxHq_API
    tag('pytdx导入OK')
except Exception as e:
    tag('pytdx导入失败', type(e).__name__, str(e)[:120]); sys.exit(1)

# 常见公开券商行情主站（海外GHA直连测试）
NODES = [
    ('招商证券','119.147.212.81',7709),
    ('国泰君安','180.153.18.170',7709),
    ('国泰君安2','180.153.18.171',7709),
    ('华泰证券','218.108.98.244',7709),
    ('华泰2','115.238.90.165',7709),
    ('中信证券','202.108.253.132',7709),
    ('通达信默认','123.125.108.20',7709),
    ('通达信默认2','123.125.108.21',7709),
    ('广发证券','124.160.88.183',7709),
    ('国信证券','119.147.164.58',7709),
    ('海通证券','220.231.8.32',7709),
    ('申万宏源','218.108.47.69',7709),
    ('银河证券','219.141.157.30',7709),
    ('中信建投','210.14.72.10',7709),
    ('中金公司','218.108.98.244',7709),
    ('东方证券','115.238.56.198',7709),
]

# ---------- ①节点连通性 ----------
ok_nodes = []
for name, ip, port in NODES:
    api = TdxHq_API(heartbeat=False, auto_retry=False)
    t1 = time.time()
    try:
        with api.connect(ip, port, time_out=3):
            cnt = api.get_security_count(0)   # 深市证券数，验证协议可用
            dt = time.time() - t1
            if cnt and cnt > 100:
                ok_nodes.append((name, ip, port, dt))
                tag('①节点OK', name, ip, '延迟%.2fs' % dt, '深市证券数', cnt)
            else:
                tag('①节点空', name, ip, 'cnt=', cnt)
    except Exception as e:
        tag('①节点FAIL', name, ip, type(e).__name__, str(e)[:50])

tag('①汇总: 可用节点 %d/%d' % (len(ok_nodes), len(NODES)))
if not ok_nodes:
    tag('★全部节点不可用——海外GHA直连券商7709被拒，路线A连通性不成立')
    tag('探测结束 %.1fs' % (time.time()-T0)); sys.exit(0)

# 选延迟最低节点做后续测试
best = min(ok_nodes, key=lambda x: x[3])
tag('★选用最快节点', best[0], best[1], '%.2fs' % best[3])

api = TdxHq_API(heartbeat=True, auto_retry=True)
try:
    api.connect(best[1], best[2], time_out=5)
    # ---------- ②市场0/1/2覆盖 ----------
    for mkt, label in [(0,'深'),(1,'沪'),(2,'北交')]:
        try:
            cnt = api.get_security_count(mkt)
            tag('②市场', mkt, label, '证券数', cnt)
            if cnt and cnt > 0:
                lst = api.get_security_list(mkt, 0)
                tag('   样本', [(x.get('code'), x.get('name')) for x in (lst or [])[:3]])
        except Exception as e:
            tag('②市场', mkt, 'EXC', type(e).__name__, str(e)[:60])

    # 北交所具体代码日线(market=2 与 market=0 都试)
    for code in ['832317','833874']:
        for mkt in [2,0]:
            try:
                bars = api.get_security_bars(9, mkt, code, 0, 800)  # 9=日K
                tag('②北交日线 mkt=%d'%mkt, code, '根数', len(bars) if bars else 0,
                    '首末', (bars[0].get('datetime'), bars[-1].get('datetime')) if bars else '-')
            except Exception as e:
                tag('②北交日线 mkt=%d'%mkt, code, 'EXC', type(e).__name__, str(e)[:50])

    # 历史深度: 茅台(1/600519)从最近往前翻页，找最早日期
    try:
        earliest = None; total = 0
        for start in range(0, 20000, 800):
            b = api.get_security_bars(9, 1, '600519', start, 800)
            if not b: break
            total += len(b)
            earliest = b[0].get('datetime')  # start越大越早
            if earliest and earliest[:4] <= '1991': break
        tag('②历史深度 600519', '累计根数', total, '最早日期', earliest)
    except Exception as e:
        tag('②历史深度 EXC', type(e).__name__, str(e)[:60])

    # ---------- ③ xdxr / finance ----------
    for mkt, code, label in [(1,'600519','茅台'),(1,'000001x','错码测试'),(2,'832317','北交'),(0,'000001','平安')]:
        if 'x' in code: continue
        try:
            x = api.get_xdxr_info(mkt, code)
            tags = {}
            for row in (x or []):
                c = row.get('category')
                tags[c] = tags.get(c, 0) + 1
            tag('③xdxr', label, mkt, code, '条数', len(x) if x else 0, '类别分布', tags)
            if x: tag('   字段', list(x[0].keys()))
        except Exception as e:
            tag('③xdxr', label, 'EXC', type(e).__name__, str(e)[:50])
    # get_finance_info: 通达信FINANCE(1)总股本所在接口
    for mkt, code, label in [(1,'600519','茅台'),(0,'000001','平安'),(2,'832317','北交')]:
        try:
            f = api.get_finance_info(mkt, code)
            tag('③finance', label, mkt, code, '字段数', len(f) if f else 0,
                '总股本字段', {k: v for k, v in (f or {}).items() if k in ('gbbq','zgb','ltgb','hd_date','gbbq_date') or 'zgb' in k or '股本' in k})
            if f: tag('   finance字段', list(f.keys())[:30])
        except Exception as e:
            tag('③finance', label, 'EXC', type(e).__name__, str(e)[:50])
finally:
    try: api.disconnect()
    except Exception: pass

# ---------- ④并发摸底 ----------
from concurrent.futures import ThreadPoolExecutor
SAMPLES = [(1,c) for c in ['600519','601318','600036','601899','600030','600887','601012','603259','600276','601166']] + \
          [(0,c) for c in ['000001','000002','000858','002594','300750','300059','000333','002415','300124','000651']]

def fetch_one(mkt_code, ip, port):
    mkt, code = mkt_code
    a = TdxHq_API(heartbeat=False, auto_retry=False)
    try:
        with a.connect(ip, port, time_out=5):
            b = a.get_security_bars(9, mkt, code, 0, 100)
            return len(b) if b else 0
    except Exception:
        return -1

for n in [1, 5, 10]:
    t1 = time.time()
    with ThreadPoolExecutor(max_workers=n) as ex:
        res = list(ex.map(lambda mc: fetch_one(mc, best[1], best[2]), SAMPLES))
    dt = time.time() - t1
    okc = sum(1 for r in res if r > 0)
    tag('④并发', n, '连接', '20只耗时%.1fs'%dt, '成功', okc, '/20', '估算全量5700只=%.0fs'%(dt/20*5700/n))

tag('探测结束 %.1fs' % (time.time()-T0))
