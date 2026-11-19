# -*- coding: utf-8 -*-
"""v483【综合探测·GHA】①pytdx K线小包vs大包定位 ②Tushare token存在性+daily字段对齐"""
import time, os, sys, json
T0=time.time()
def tag(*a): print('[ALIGN]',*a,flush=True)

# ---------- ① pytdx 小包vs大包 ----------
try:
    from pytdx.hq import TdxHq_API
    api=TdxHq_API(heartbeat=True,auto_retry=False)
    ip='180.153.18.170'
    if api.connect(ip,7709,time_out=5):
        for cat,label in [(4,'日K=4'),(9,'日K=9')]:
            for cnt in [10,50,100,800]:
                try:
                    b=api.get_security_bars(cat,1,'600519',0,cnt)
                    tag('①pytdx',label,'count=%d'%cnt,'根数',len(b) if b else 0,
                        '首末',(b[0].get('datetime'),b[-1].get('datetime')) if b else '-')
                except Exception as e:
                    tag('①pytdx',label,cnt,'EXC',type(e).__name__,str(e)[:50])
        try: api.disconnect()
        except Exception: pass
    else:
        tag('①pytdx 节点连接失败')
except Exception as e:
    tag('①pytdx不可用',type(e).__name__,str(e)[:80])

# ---------- ② Tushare token 存在性 ----------
tok = os.environ.get('TUSHARE_TOKEN','')
tag('②TUSHARE_TOKEN存在:', bool(tok), '长度', len(tok))
if tok:
    try:
        import tushare as ts
        pro=ts.pro_api(tok)
        # daily按交易日批量(已知可通),验证字段+北交所+返回行数
        for d in ['20241118','20261119']:
            try:
                df=pro.daily(trade_date=d)
                tag('②daily',d,'行数',len(df),'列',list(df.columns) if len(df) else '-')
                if len(df):
                    bj=df[df['ts_code'].str.endswith('.BJ')]
                    tag('   北交所.BJ行数',len(bj),'样例',bj['ts_code'].head(3).tolist() if len(bj) else '-')
            except Exception as e:
                tag('②daily',d,'EXC',str(e)[:120])
        # stock_basic 120积分是否有权
        try:
            b=pro.stock_basic(exchange='',list_status='L')
            tag('②stock_basic 行数',len(b))
        except Exception as e:
            tag('②stock_basic 无权:',str(e)[:100])
        # adj_factor / daily_basic 是否有权
        for fn in ['adj_factor','daily_basic']:
            try:
                r=getattr(pro,fn)(trade_date='20241118')
                tag('②'+fn,'行数',len(r))
            except Exception as e:
                tag('②'+fn,'无权:',str(e)[:80])
    except Exception as e:
        tag('②tushare导入/调用失败',type(e).__name__,str(e)[:100])
else:
    tag('②无TUSHARE_TOKEN——Tushare对齐无法实测，只能静态评估')

tag('探测结束 %.1fs'%(time.time()-T0))
