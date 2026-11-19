# -*- coding: utf-8 -*-
"""v485【Tushare+daily 与东财底稿对齐验证·GHA·只读】
用TUSHARE_TOKEN拉2026-11-12全市场daily,东财clist拉同日总市值,算>100亿过滤,
对比东财底稿 active=5573/total=103。验证口径能否对齐。不改生产。
"""
import time, os, json, sys
T0=time.time()
def tag(*a): print('[ALIGN2]',*a,flush=True)

tok=os.environ.get('TUSHARE_TOKEN','')
tag('token存在',bool(tok),'长度',len(tok))
if not tok:
    tag('无token,退出');sys.exit(0)

import tushare as ts
import pandas as pd
pro=ts.pro_api(tok)

# 1) 拉11-12全市场daily
try:
    df=pro.daily(trade_date='20261112')
    tag('①daily 20261112 行数',len(df),'列',list(df.columns))
except Exception as e:
    tag('①daily EXC',str(e)[:200]);sys.exit(0)
if df is None or len(df)==0:
    tag('①daily空');sys.exit(0)

# 北交所覆盖
try:
    bj=df[df['ts_code'].str.endswith('.BJ')]
    tag('②北交所.BJ行数',len(bj),'样例',bj['ts_code'].head(3).tolist() if len(bj) else '-')
except Exception as e:
    tag('②北交统计EXC',str(e)[:80])

# 2) 检查pre_close反推可行性: 找除权日(pre_close != 昨收)
# 拉11-11和11-12对比
try:
    d11=pro.daily(trade_date='20261111')
    m11=dict(zip(d11['ts_code'],d11['close']))
    xr=0;sample=[]
    for _,r in df.iterrows():
        prev=m11.get(r['ts_code'])
        if prev and abs(r['pre_close']-prev)>0.01:
            xr+=1
            if len(sample)<3:sample.append((r['ts_code'],round(prev,2),round(r['pre_close'],2)))
    tag('③pre_close≠昨收 除权股数',xr,'样例(code,昨收,pre_close)',sample)
except Exception as e:
    tag('③除权检查EXC',str(e)[:80])

# 3) 东财clist总市值(已验证通) - 用clist拿f20总市值+现价
try:
    import urllib.request
    EM='https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=6000&po=1&np=1&fltt=2&invt=2&fid=f12&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048&fields=f12,f14,f2,f20'
    req=urllib.request.Request(EM,headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'})
    with urllib.request.urlopen(req,timeout=20) as r:ed=json.loads(r.read().decode())
    diff=(ed.get('data') or {}).get('diff') or []
    tag('④东财clist行数',len(diff))
    # 算市值>100亿
    cap100=[x for x in diff if isinstance(x.get('f20'),(int,float)) and x['f20']>100e8]
    tag('   东财clist市值>100亿',len(cap100),'(东财底稿11-12 total=103/active=5573)')
except Exception as e:
    tag('④东财clist EXC',str(e)[:100])

tag('结束 %.1fs'%(time.time()-T0))
