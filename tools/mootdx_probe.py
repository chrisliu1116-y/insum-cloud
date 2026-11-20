# -*- coding: utf-8 -*-
"""v491【mootdx 最后一次探测·仅GHA】直连+代理各测 bars(frequency=9)。
若仍0根，通达信方案彻底结案。
"""
import time,sys,socket
T0=time.time()
def tag(*a):print('[MOOTDX]',*a,flush=True)
MODE=sys.argv[1] if len(sys.argv)>1 else 'direct'
if MODE=='proxy':
    try:
        import socks
        socks.set_default_proxy(socks.SOCKS5,'127.0.0.1',7890)
        socket.socket=socks.socksocket
        tag('SOCKS5代理劫持已启用')
    except Exception as e:
        tag('代理劫持失败',str(e)[:80])
try:
    from mootdx.quotes import Quotes
    tag('mootdx导入OK')
except Exception as e:
    tag('mootdx导入失败',type(e).__name__,str(e)[:120]);sys.exit(1)

try:
    tag('建连 bestip=True...')
    client=Quotes.factory(market='std',bestip=True,timeout=15)
    tag('连接建立')
except Exception as e:
    tag('建连失败',type(e).__name__,str(e)[:150]);sys.exit(1)

for sym,mkt in [('600519','沪'),('000001','深'),('832317','北交'),('833874','北交')]:
    try:
        b=client.bars(symbol=sym,frequency=9,offset=100)
        n=len(b) if b is not None else 0
        if n:
            tag('bars',mkt,sym,'行数',n,'| 首末',str(b.iloc[0].get('datetime'))[:10],str(b.iloc[-1].get('datetime'))[:10])
        else:
            tag('bars',mkt,sym,'行数 0/None')
    except Exception as e:
        tag('bars',mkt,sym,'EXC',type(e).__name__,str(e)[:100])
tag('模式',MODE,'结束 %.1fs'%(time.time()-T0))
