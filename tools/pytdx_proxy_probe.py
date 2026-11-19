# -*- coding: utf-8 -*-
"""v484【pytdx 经机场SOCKS5代理·GHA实测】
pytdx用原始socket,不读HTTP_PROXY;用PySocks全局劫持socket走 127.0.0.1:7890。
测:①代理下节点连通 ②K线是否返回 ③北交所 ④xdxr。只读,不改生产。
"""
import time, sys, socket
T0=time.time()
def tag(*a): print('[TDXP]',*a,flush=True)

# 全局socsk劫持
try:
    import socks
    socks.set_default_proxy(socks.SOCKS5, '127.0.0.1', 7890)
    socket.socket = socks.socksocket
    tag('PySocks全局劫持已启用')
except Exception as e:
    tag('PySocks失败',type(e).__name__,str(e)[:80]);sys.exit(1)

from pytdx.hq import TdxHq_API

NODES=[
    ('国泰君安','180.153.18.170',7709),
    ('华泰2','115.238.90.165',7709),
    ('东方证券','115.238.56.198',7709),
    ('招商','119.147.212.81',7709),
    ('中信','202.108.253.132',7709),
    ('通达信默认','123.125.108.20',7709),
]
ok=[]
for name,ip,port in NODES:
    api=TdxHq_API(heartbeat=True,auto_retry=False)
    t1=time.time()
    try:
        if api.connect(ip,port,time_out=8):
            cnt=api.get_security_count(0)
            tag('①代理节点OK',name,ip,'%.1fs'%(time.time()-t1),'深市',cnt)
            if cnt and cnt>100: ok.append((name,ip,port))
        else:
            tag('①代理节点FAIL',name,ip,'connect False')
    except Exception as e:
        tag('①代理节点FAIL',name,ip,type(e).__name__,str(e)[:60])
    finally:
        try: api.disconnect()
        except Exception: pass

tag('①汇总 代理下可用 %d/%d'%(len(ok),len(NODES)))
if not ok:
    tag('★代理下节点全不可达——pytdx连代理也不通,路线A彻底放弃')
    tag('结束%.1fs'%(time.time()-T0));sys.exit(0)

name,ip,port=ok[0]
api=TdxHq_API(heartbeat=True,auto_retry=False)
try:
    api.connect(ip,port,time_out=8)
    # K线(核心): 日K=4, 沪深+北交
    for mkt,code,label in [(1,'600519','茅台'),(0,'000001','平安'),(2,'832317','北交观典'),(2,'833874','北交泰祥')]:
        for cnt in [100,800]:
            try:
                b=api.get_security_bars(4,mkt,code,0,cnt)
                tag('②K线',label,'mkt=%d'%mkt,'count=%d'%cnt,'根数',len(b) if b else 0,
                    '首末',(b[0].get('datetime'),b[-1].get('datetime')) if b else '-')
            except Exception as e:
                tag('②K线',label,'EXC',type(e).__name__,str(e)[:50])
    # 历史深度茅台
    earliest=None;total=0
    for start in range(0,16000,800):
        try:b=api.get_security_bars(4,1,'600519',start,800)
        except Exception:break
        if not b:break
        total+=len(b);earliest=b[0].get('datetime')
        if earliest and earliest[:4]<='1991':break
    tag('②历史深度600519','累计',total,'最早',earliest)
    # xdxr北交
    x=api.get_xdxr_info(2,'832317')
    tag('③xdxr北交832317','条数',len(x) if x else 0)
finally:
    try:api.disconnect()
    except Exception:pass

# ④并发20只(如果K线可用)
from concurrent.futures import ThreadPoolExecutor
SAMPLES=[(1,c) for c in ['600519','601318','600036','601899','600030','600887','601012','603259','600276','601166']]+\
        [(0,c) for c in ['000001','000002','000858','002594','300750','300059','000333','002415','300124','000651']]
def one(mc):
    m,c=mc;a=TdxHq_API(heartbeat=False,auto_retry=False)
    try:
        with a.connect(ip,port,time_out=8):
            b=a.get_security_bars(4,m,c,0,100)
            return len(b) if b else 0
    except Exception:return -1
for n in [5,10]:
    t1=time.time()
    with ThreadPoolExecutor(max_workers=n) as ex:res=list(ex.map(one,SAMPLES))
    tag('④并发',n,'20只%.1fs'%(time.time()-t1),'成功',sum(1 for r in res if r>0),'/20')
tag('结束 %.1fs'%(time.time()-T0))
