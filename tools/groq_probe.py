# -*- coding: utf-8 -*-
"""v502【Groq能力探测 + 三层容灾切换验证·仅GHA】
测:①Groq各免费模型可用性/速度/是否支持thinking ②模拟智谱报错→自动切Groq。
"""
import os,json,urllib.request,urllib.error,time,sys
T0=time.time()
def tag(*a):print('[GROQ]',*a,flush=True)
K=os.environ.get('GROQ_KEY','')
if not K:tag('无GROQ_KEY');sys.exit(1)
URL='https://api.groq.com/openai/v1/chat/completions'
MODELS=['llama-3.3-70b-versatile','llama-3.1-8b-instant','openai/gpt-oss-120b','qwen/qwen3-32b','moonshotai/kimi-k2-instruct']
MSG=[{'role':'user','content':'把这句话判为S/A/B/C之一，只输出一个字母：美联储意外降息50个基点'}]
def call(model,extra=None,maxtok=80):
    body={'model':model,'messages':MSG,'max_tokens':maxtok,'temperature':0}
    if extra:body.update(extra)
    req=urllib.request.Request(URL,data=json.dumps(body).encode(),
      headers={'Authorization':'Bearer '+K,'Content-Type':'application/json'})
    t=time.time()
    try:
        with urllib.request.urlopen(req,timeout=40) as r:d=json.loads(r.read().decode())
    except urllib.error.HTTPError as he:
        body=he.read().decode(errors='ignore')[:300]
        tag('  HTTP错误体:',he.code,body)
        raise
    dt=time.time()-t
    ch=d['choices'][0]
    return dt,(ch.get('message') or {}).get('content',''),d.get('usage',{}),ch.get('finish_reason'),bool(ch.get('message',{}).get('reasoning'))

tag('=== 模型探测 ===')
ok=[]
for m in MODELS:
    try:
        dt,txt,usage,fr,hasreason=call(m)
        tag(m,'| %.2fs'%dt,'| 回复:',repr(txt[:40]),'| reasoning:',hasreason,'| tokens:',usage.get('total_tokens'))
        ok.append(m)
    except Exception as e:
        tag(m,'| FAIL',str(e)[:100])
tag('可用模型:',ok)

tag('=== thinking/reasoning 探测 ===')
for m in ['openai/gpt-oss-120b','qwen/qwen3-32b']:
    if m not in ok:continue
    for extra in [{'reasoning_format':'parsed'},{'reasoning_effort':'low'}]:
        try:
            dt,txt,usage,fr,hasreason=call(m,extra,maxtok=200)
            tag(m,list(extra.keys())[0],'| %.2fs'%dt,'| reasoning:',hasreason,'| 回复:',repr(txt[:50]))
        except Exception as e:
            tag(m,list(extra.keys())[0],'| FAIL',str(e)[:90])

tag('=== 三层容灾切换(模拟智谱报错) ===')
if ok:
    gm=ok[0]
    try:
        def zhipu_fail():raise RuntimeError('mock: ZHIPU 500')
        def groq():
            dt,txt,usage,fr,hasreason=call(gm)
            return txt,'groq/'+gm
        # 有序降级:智谱抛错→Groq
        try:
            zhipu_fail();res=zhipu_fail()
        except Exception as e1:
            tag('智谱mock失败:',str(e1)[:50],'→切Groq')
            txt,ch=groq()
            tag('切换成功:',repr(txt[:50]),'| channel:',ch)
    except Exception as e:tag('切换失败',str(e)[:100])
tag('=== 完成 %.1fs ==='%(time.time()-T0))
