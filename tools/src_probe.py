# -*- coding: utf-8 -*-
"""v481【替代数据源极小样本连通性速测·仅GHA云端·本机零请求】
4源 × 4样本(2北交所+2沪深)，只探返回格式/字段/历史长度，不跑批不提交。
样本：北交所 832317/833874，沪深 600519/000001。
"""
import json, time, urllib.request, urllib.error, re
T0=time.time()
def tag(*a): print('[SRC-PROBE]',*a,flush=True)
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
SAMPLES=[('600519','sh600519','sh'),('000001','sz000001','sz'),
         ('832317','bj832317','bj'),('833874','bj833874','bj')]

def fetch(url, headers=None, timeout=10, enc='utf-8'):
    h={'User-Agent':UA}
    if headers:h.update(headers)
    req=urllib.request.Request(url,headers=h)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read()
        return r.status, raw.decode(enc,errors='ignore')

# ---------- 1. 腾讯 qt 批量快照（含北交所+总市值字段45） ----------
try:
    q=','.join(s[1] for s in SAMPLES)
    st,txt=fetch('https://qt.gtimg.cn/q='+q,{'Referer':'https://gu.qq.com/'},enc='gbk')
    tag('1_腾讯qt批量快照 HTTP',st)
    for line in txt.split(';'):
        if '=' not in line:continue
        _,v=line.split('=',1);fs=v.strip().strip('"').split('~')
        if len(fs)<46:continue
        tag('  ',fs[1],fs[2],'| 字段数',len(fs),'| 价',fs[3],'| 字段45(总市值亿)',fs[45])
except Exception as e:
    tag('1_腾讯qt EXC',type(e).__name__,str(e)[:100])

# ---------- 2. 腾讯 fqkline 日线（北交所历史长度） ----------
for code,tc,_ in SAMPLES:
    try:
        u='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,1200,qfq'%tc
        st,txt=fetch(u,{'Referer':'https://gu.qq.com/'},timeout=12)
        d=json.loads(txt);box=(d.get('data') or {}).get(tc) or {}
        arr=box.get('qfqday') or box.get('day') or []
        tag('2_腾讯日线',tc,'HTTP',st,'| 根数',len(arr),'| 首末',(arr[0][0] if arr else '-'),(arr[-1][0] if arr else '-'))
    except Exception as e:
        tag('2_腾讯日线',tc,'EXC',type(e).__name__,str(e)[:80])

# ---------- 3. 新浪 hq.sinajs.cn 批量快照（带Referer） ----------
try:
    q=','.join(s[1] for s in SAMPLES)
    st,txt=fetch('https://hq.sinajs.cn/list='+q,{'Referer':'https://finance.sina.com.cn/'},enc='gbk')
    tag('3_新浪hq批量快照 HTTP',st)
    for line in txt.strip().split('\n'):
        if '=' not in line:continue
        name,v=line.split('=',1);fs=v.strip().strip('"').split(',')
        tag('  ',name.strip().split('_')[-1],'| 字段数',len(fs),'| 样本',fs[:4])
except Exception as e:
    tag('3_新浪hq EXC',type(e).__name__,str(e)[:100])

# ---------- 4. 新浪 quotes.sina.cn 日线(前复权) ----------
for code,tc,_ in SAMPLES:
    try:
        # 新浪日K接口(带symbol)
        u='https://quotes.sina.cn/cn/api/jsonp_v2.php/var%%20_k=/CN_MarketDataService.getKLineData?symbol=%s&scale=240&ma=no&datalen=1023'%tc
        st,txt=fetch(u,{'Referer':'https://finance.sina.com.cn/'},timeout=12)
        m=re.search(r'\((\[[\s\S]*\])\)',txt)
        if not m:
            tag('4_新浪日线',tc,'HTTP',st,'| 无JSON数组 raw=',repr(txt[:120]));continue
        arr=json.loads(m.group(1))
        tag('4_新浪日线',tc,'HTTP',st,'| 根数',len(arr),'| 首末',(arr[0].get('day') if arr else '-'),(arr[-1].get('day') if arr else '-'),'| 字段',list(arr[0].keys()) if arr else '-')
    except Exception as e:
        tag('4_新浪日线',tc,'EXC',type(e).__name__,str(e)[:80])

# ---------- 5. 搜狐 hisHq 日线(超长历史,不复权) ----------
for code,tc,_ in SAMPLES:
    try:
        # 搜狐要求 code 为纯数字 + 市场前缀参数
        u='https://q.stock.sohu.com/hisHq?code=cn_%s&start=19900101&end=20991231&stat=1&order=D&period=d&callback=&rt=jsonp'%code
        st,txt=fetch(u,{'Referer':'https://q.stock.sohu.com/'},timeout=15)
        try:
            d=json.loads(txt)
            arr=(d[0].get('hq') or []) if isinstance(d,list) and d else []
        except Exception:
            tag('5_搜狐日线',code,'HTTP',st,'| 非JSON raw=',repr(txt[:150]))
            continue
        tag('5_搜狐日线',code,'HTTP',st,'| 根数',len(arr),'| 首末',(arr[-1][0] if arr else '-'),(arr[0][0] if arr else '-'))
    except Exception as e:
        tag('5_搜狐日线',code,'EXC',type(e).__name__,str(e)[:80])

tag('速测结束 用时%.1fs'%(time.time()-T0))
