# -*- coding: utf-8 -*-
"""v486【Tushare重算指标 vs 东财底稿 决定性对齐·GHA·只读】
拉最近45个交易日daily(含北交所),按交易日批量构建每只历史序列,
用现网同款 rsi_tdx/wr_tdx 算 total/daycnt,对比东财底稿。
关键:tushare无市值,此处先验证"无市值过滤"的total量级是否接近;
若量级差远,说明拼装连基础都对不上。
"""
import time, os, sys, json
from collections import defaultdict
T0=time.time()
def tag(*a): print('[ALIGN3]',*a,flush=True)
tok=os.environ.get('TUSHARE_TOKEN','')
if not tok:tag('无token');sys.exit(0)
import tushare as ts
pro=ts.pro_api(tok)

# 东财底稿目标(最后12日)
EM_DATES=['20261028','20261029','20261030','20261102','20261103','20261104',
          '20261105','20261106','20261109','20261110','20261111','20261112']
EM_TOTAL=[70,69,68,70,78,91,105,117,116,115,113,103]
EM_DAYCNT=[47,43,64,77,119,98,152,117,55,77,48,23]

# 需要暖机: 对12个目标日,每个往前再拉40个交易日
all_dates=sorted(set(EM_DATES+[
    '20260901','20260902','20260903','20260904','20260907','20260908','20260909','20260910',
    '20260911','20260914','20260915','20260916','20260917','20260918','20260921','20260922',
    '20260923','20260924','20260925','20260928','20260929','20261009','20261012','20261013',
    '20261014','20261015','20261016','20261019','20261020','20261021','20261022','20261023',
    '20261026','20261027']))

# 按日拉全市场,构建 per-code 序列
bars=defaultdict(list)  # code -> [(date,close,high,low)]
for d in all_dates:
    try:
        df=pro.daily(trade_date=d)
    except Exception as e:
        tag('daily失败',d,str(e)[:60]);time.sleep(1);continue
    if df is None or len(df)==0:continue
    for _,r in df.iterrows():
        bars[r['ts_code']].append((r['trade_date'],float(r['close']),float(r['high']),float(r['low'])))
    time.sleep(0.25)   # 50次/分限流保护(实际<40次)
tag('拉取完成 交易日',len(all_dates),'股票数',len(bars))

# 现网同款公式(复制自breadth_lib口径)
def sma_tdx(x,n):
    m=len(x);out=[None]*m
    if m<n:return out
    seed=sum(x[:n])/n;out[n-1]=seed;y=seed
    for i in range(n,m):
        y=(x[i]+(n-1)*y)/n;out[i]=y
    return out
def rsi9(cl):
    m=len(cl)
    if m==0:return [None]*m
    up=[0.0]*m;dn=[0.0]*m
    for i in range(1,m):
        diff=cl[i]-cl[i-1];up[i]=diff if diff>0 else 0.0;dn[i]=abs(diff)
    su=sma_tdx(up,9);sd=sma_tdx(dn,9)
    return [None if su[i] is None or sd[i] is None else (su[i]/sd[i]*100 if sd[i]>0 else 0) for i in range(m)]
def wr(cl,hi,lo,n):
    m=len(cl);out=[None]*m
    for i in range(m):
        if i<n-1:continue
        hh=max(hi[i-n+1:i+1]);ll=min(lo[i-n+1:i+1]);den=hh-ll
        out[i]=((hh-cl[i])/den*100) if den>0 else 0
    return out

# 算每日 total(无市值过滤) / daycnt
daily_total=defaultdict(int);daily_daycnt=defaultdict(int)
for code,seq in bars.items():
    seq.sort(key=lambda x:x[0])
    cl=[x[1] for x in seq];hi=[x[2] for x in seq];lo=[x[3] for x in seq]
    r=rsi9(cl);w10=wr(cl,hi,lo,10);w20=wr(cl,hi,lo,20)
    cnt21=0
    for i,(dt,_,_,_) in enumerate(seq):
        hit = r[i] is not None and r[i]>70 and w10[i]<20 and w20[i]<20
        if hit:
            daily_total[dt]+=1
        if dt in set(EM_DATES):
            # COUNT(day_tj,21): 近21日内hit次数
            recent=seq[max(0,i-20):i+1]
            idxmap={x[0]:j for j,x in enumerate(seq)}
            c=0
            for rd,_,_,_ in recent:
                j=idxmap[rd]
                if r[j] is not None and r[j]>70 and w10[j]<20 and w20[j]<20:c+=1
            daily_daycnt[dt]+=c

tag('对齐对比(无市值过滤): 日期 | Tushare total/daycnt | 东财 total/daycnt')
for d,et,ed in zip(EM_DATES,EM_TOTAL,EM_DAYCNT):
    tag(' ',d,'T total=%-4d daycnt=%-4d'%(daily_total.get(d,0),daily_daycnt.get(d,0)),
        '| 东财 total=%-4d daycnt=%-4d'%(et,ed))
tag('结束 %.1fs'%(time.time()-T0))
