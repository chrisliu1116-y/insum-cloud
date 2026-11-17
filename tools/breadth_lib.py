# -*- coding: utf-8 -*-
"""
市场宽度（XY_BASE + INSUM）核心计算库
口径（与用户逐条对齐）：
  - 股票池：全部A股 = 沪深A + 北交所（8/4/920），含 ST/科创/创业/主板/次新，排除退市/B股
  - 复权：fqt=1 前复权（单序列）
  - RSI(N)：通达信 SMA(X,N,1) 递推，第一根有效 Bar 种子 = 前 N 个 X 的简单算术均值
  - WR(N)：(HHV(H,N)-C)/(HHV(H,N)-LLV(L,N))*100，分母=0 记 0
  - 市值：前复权Close × 当前总股本(f20/现价) / 1e8 > 100
  - 停牌：不平推，按自然交易日独立算，按日期 GroupBy 累加
"""
import json
import math
import os
import random
import time
from urllib.parse import urlencode, quote

# v357【统一 Chrome TLS 指纹】（2026-11-15 用户要求）
# GitHub 东财跑批全部走 curl_cffi，彻底不再使用 Python 原生 urllib / 普通
# requests —— 后者的 JA3 指纹会被东财 WAF 识别后直接断连（RemoteDisconnected）。
# Session 级指定 impersonate="chrome120" 后，该 Session 发出的每一个请求
# 都带 Chrome 120 的 TLS/JA3/HTTP2 指纹，以及下面三个标准浏览器请求头。
from curl_cffi import requests

EM_CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
EM_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://quote.eastmoney.com/",
}
# 沪深A
FS_SH = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
# 北交所（必须单独查：和上面拼在同一条 fs 里会被过滤掉，实测合并只回 5575 只、少了 360+）
FS_BJ = "m:0+t:81+s:2048"

# v357：唯一一条请求通道。GitHub Actions 与本机都走这里，不再有
# 「GHA 用 requests」的回退分支，也不再有 curl.exe / urllib 兜底。
# 缺库时直接 ImportError 明确失败，绝不静默退回被 WAF 拦的原生指纹。
SESSION = requests.Session(impersonate="chrome120")
SESSION.headers.update(UA)
HTTP_BACKEND = "curl_cffi/chrome120"

# vPROXY【CF 反代出口】2026-11-18 用户要求评估「GitHub 跑批 + Cloudflare Workers
# 透明反代」架构：东财对 GHA(Azure) 机房 IP 段限流，改由 CF 边缘节点(AS13335)出口。
# 链路：GitHub Actions →(本 SESSION)→ CF Worker /__proxy?u=<url> →(CF fetch)→ 东财。
# 本机零东财请求（dispatch 只发 GitHub API）；GHA→CF 段仍用 chrome120 指纹无所谓，
# CF→东财 段由 worker.js handleProxy 带 Chrome UA + Referer。
# 环境变量 EM_PROXY_WORKER 为空 = 关闭，行为与原来一字不差（默认关闭）。
# worker.js 已内置 /__proxy 白名单路由（PROXY_ALLOW 含 eastmoney.com），无需改 worker。
EM_PROXY_WORKER = os.environ.get("EM_PROXY_WORKER", "").strip().rstrip("/")


def _proxy_url(url, params):
    """把东财 url+params 包成 CF Worker /__proxy?u=<完整目标URL>；未开启则原样返回。"""
    if not EM_PROXY_WORKER or "eastmoney.com" not in url:
        return url, params
    full = url + ("&" if "?" in url else "?") + urlencode(params)
    return EM_PROXY_WORKER + "/__proxy?u=" + quote(full, safe=""), None


def _get(url, params, retries=1, timeout=15):
    """v361【风控第一原则：失败不重试】
    11-15 本机跑批取证：前 200 只全通，200~400 只之间失败率升到 30%，
    而 `retries=3` 会把「一次失败」放大成 3 个请求 —— 越失败越打、越打越封，
    形成正反馈。而 11-09 GHA / 11-10 本机三次成功样本都是「每只 1 请求、
    全成功」，从来没有靠重试救回来过。故默认 retries=1：失败即弃，
    交给上层护栏判 stale，绝不放大请求数。"""
    for k in range(retries):
        try:
            # v354：间隔下限由 120ms 提到 150ms（用户要求低并发 ≥150ms），
            # 峰值仍压在 ~4 并发 / 5~7 req/s，避免刚解封的 IP 再被风控。
            time.sleep(random.uniform(0.15, 0.22))
            req_url, req_params = _proxy_url(url, params)
            r = SESSION.get(req_url, params=req_params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(1.5 * (k + 1))
        except Exception:
            time.sleep(0.8 * (k + 1))
    return None


def _fetch_fs(fs, verbose=False):
    out, page, total, fails = [], 1, None, 0
    while True:
        d = _get(EM_CLIST, {
            "pn": page, "pz": 100, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            # 必须按代码排序（f12）分页：按涨跌幅（f3）排会因盘中排序变动
            # 导致跨页重复/漏项，实测沪深只拿到 5220 只（少 340+）。
            "fid": "f12", "fs": fs, "fields": "f12,f14,f2,f20",
        })
        if not d or not d.get("data"):
            # 单页偶发失败不能直接结束 —— 实测会在第 53 页断掉，
            # 沪深只拿到 5220 只（少 340+），家数基线直接错。
            fails += 1
            if fails > 4:
                break
            time.sleep(0.6)
            continue
        fails = 0
        data = d["data"]
        if total is None:
            total = data.get("total", 0)
        diff = data.get("diff") or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        if not diff:
            break
        for x in diff:
            code = str(x.get("f12", ""))
            price = x.get("f2")
            mkt = x.get("f20")
            if not code or mkt in (None, "-"):
                continue
            try:
                mkt = float(mkt)
            except Exception:
                continue
            if mkt <= 0:
                continue
            # 停牌 / 无最新价（f2='-' 或 0）不能直接丢 —— 它仍是正常上市的 A 股，
            # 丢了家数基线会少 340+（实测 5563 → 5220）。价格留空，总股本改用
            # 最近一根 K 线收盘价倒推（见 total_shares_of）。
            try:
                price = float(price)
            except Exception:
                price = 0.0
            if price <= 0:
                price = 0.0
            out.append({"code": code, "name": x.get("f14", ""), "price": price, "mktcap": mkt})
        if verbose:
            print(f"  page {page}: {len(out)}/{total}", flush=True)
        if total and len(out) >= total:
            break
        page += 1
        time.sleep(0.05)
    return out


def probe_universe():
    """v361【跑批前健康检查】只发 1 个 clist 请求，看 total 是否完整。
    11-13 / 11-15 两次 GHA 失败都是「股票池 0 只」（clist 端点级限流）：
    不先探一下就直接翻 56 页 + 5564 次 kline，全是无效请求还会加重风控。
    本机零调用（这个函数只在 GHA runner 里跑），不会碰本机行情出口。"""
    d = _get(EM_CLIST, {
        "pn": 1, "pz": 1, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f12", "fs": FS_SH, "fields": "f12,f14",
    }, retries=1)
    try:
        return int(((d or {}).get("data") or {}).get("total") or 0)
    except Exception:
        return 0


def probe_kline(code="600519"):
    """v361b【kline 端点探针】只发 1 个 push2his 请求（lmt=1）。
    11-15 首次实测：clist pz=1 健康检查通过（total=5564），但 kline 前 142 只
    有效后连续 30 只失败触发熔断 —— 说明 clist 与 kline 是**独立的端点风控**。
    先探 1 根，能省掉整批无效请求：不通就在 2 个请求内退出。"""
    d = _get(EM_KLINE, {
        "secid": secid_of(code), "klt": 101, "fqt": 1, "lmt": 1,
        "end": "20500101", "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }, retries=1)
    try:
        return len(((d or {}).get("data") or {}).get("klines") or [])
    except Exception:
        return 0


# ===================================================================
# v361d【东财专属风控配置】跑批主流程只读这两个配置，不写死任何源的条件。
# 改东财跑法（curl_cffi 指纹 / retries / 探针 / 熔断）只动本文件，
# 不会影响腾讯源（tx_lib.py 自带自己的 PROBE_CHAIN / CIRCUIT）。
# 11-15 首次实测：clist pz=1 通（total=5564）但 kline 前 142 只后连败 30
# → clist 与 push2his 是独立的端点风控，所以探针链必须两段都探，
# 不通就在 2 个请求内止损，绝不浪费整批 5584 次。
# ===================================================================
PROBE_CHAIN = [
    ('clist', probe_universe, 5000),   # 股票池完整（0 只 = clist 端点级限流）
    ('kline', probe_kline, 1),         # push2his 未被端点级限流
]
CIRCUIT = {'enabled': True, 'max_consec_bad': 30}   # 连续失败熔断（东财专属）


def fetch_universe(verbose=False):
    """全部A股 = 沪深A + 北交所（用户口径：含 8/4/920 开头）。
    必须分两次查：把北交所的 fs 拼进同一条会被过滤掉（实测只回 5575、少 360+）。"""
    sh = _fetch_fs(FS_SH, verbose)
    bj = _fetch_fs(FS_BJ, verbose)
    seen, out = set(), []
    for x in sh + bj:
        if x["code"] in seen:
            continue
        seen.add(x["code"])
        out.append(x)
    if verbose:
        print(f"  沪深 {len(sh)} + 北交所 {len(bj)} → 去重后 {len(out)}", flush=True)
    return out


def secid_of(code):
    """东财 secid：沪=1，深/北=0"""
    if code.startswith(("60", "68", "90", "11", "51", "58")):
        return "1." + code
    return "0." + code


def fetch_kline(code, lmt=320, fqt=1, retries=1, beg=None):
    """返回 bars: [{d,o,c,h,l,v,a}] 升序。fqt=1 前复权。
    beg 形如 '19901201'：东财原生支持，用于精确补历史（lmt 大于实际根数时自动取全量）。"""
    params = {
        "secid": secid_of(code), "klt": 101, "fqt": fqt, "lmt": lmt,
        "end": "20500101", "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    if beg:
        params["beg"] = beg
    d = _get(EM_KLINE, params, retries=retries)
    if not d or not d.get("data") or not d["data"].get("klines"):
        return []
    bars = []
    for line in d["data"]["klines"]:
        p = line.split(",")
        if len(p) < 7:
            continue
        try:
            bars.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                         "h": float(p[3]), "l": float(p[4]),
                         "v": float(p[5]), "a": float(p[6])})
        except Exception:
            continue
    return bars


def total_shares_of(price, mktcap, last_close=None):
    """总股本 = 总市值 / 现价；停牌无现价时用最近一根 K 线收盘价倒推。"""
    p = price if (price and price > 0) else (last_close or 0)
    if not p or p <= 0 or not mktcap or mktcap <= 0:
        return None
    return mktcap / p


def sma_tdx(x, n):
    """通达信 SMA(X,N,1)：Y=(X+(N-1)Yprev)/N；首根有效Bar种子=前N个X简单均值。
    返回等长数组，暖机期(i<n-1)为 None。"""
    m = len(x)
    out = [None] * m
    if m < n:
        return out
    # 种子：前 n 个 X 的简单均值（X 已按 bar 对齐，x[0] 通常为 0/首值）
    seed = sum(x[:n]) / n
    out[n - 1] = seed
    y = seed
    for i in range(n, m):
        y = (x[i] + (n - 1) * y) / n
        out[i] = y
    return out


def rsi_tdx(closes, n):
    """RSI(N)=SMA(MAX(C-LC,0),N,1)/SMA(ABS(C-LC),N,1)*100。暖机期 None。"""
    m = len(closes)
    if m == 0:
        return [None] * m
    up = [0.0] * m
    dn = [0.0] * m
    for i in range(1, m):
        diff = closes[i] - closes[i - 1]
        up[i] = diff if diff > 0 else 0.0
        dn[i] = abs(diff)
    su = sma_tdx(up, n)
    sd = sma_tdx(dn, n)
    out = [None] * m
    for i in range(m):
        if su[i] is None or sd[i] is None:
            continue
        out[i] = (su[i] / sd[i] * 100.0) if sd[i] > 0 else 0.0
    return out


def wr_tdx(highs, lows, closes, n):
    """WR(N)=(HHV(H,N)-C)/(HHV(H,N)-LLV(L,N))*100；分母=0记0。暖机期 None。"""
    m = len(closes)
    out = [None] * m
    for i in range(m):
        if i < n - 1:
            continue
        hh = max(highs[i - n + 1:i + 1])
        ll = min(lows[i - n + 1:i + 1])
        denom = hh - ll
        out[i] = ((hh - closes[i]) / denom * 100.0) if denom > 0 else 0.0
    return out


def stock_outputs(bars, total_shares):
    """单只股票 → 4 个输出数组（与 bars 一一对应）：
       day_tj(当日指标), tj_count(总数指标), will_remove(将剔除), new_add(当日新增)"""
    m = len(bars)
    if m == 0:
        return [], [], [], [], []
    closes = [b["c"] for b in bars]
    highs = [b["h"] for b in bars]
    lows = [b["l"] for b in bars]
    rsi9 = rsi_tdx(closes, 9)
    wr10 = wr_tdx(highs, lows, closes, 10)
    wr20 = wr_tdx(highs, lows, closes, 20)

    day_tj = [False] * m
    for i in range(m):
        if rsi9[i] is None or wr10[i] is None or wr20[i] is None:
            continue
        mv = closes[i] * total_shares / 1e8  # 亿元
        if mv <= 100:
            continue
        if rsi9[i] > 70 and wr10[i] < 20 and wr20[i] < 20:
            day_tj[i] = True

    # COUNT(day_tj, 21)
    cnt = [0] * m
    for i in range(m):
        s = max(0, i - 20)
        cnt[i] = sum(1 for k in range(s, i + 1) if day_tj[k])
    tj_count = [cnt[i] >= 5 for i in range(m)]

    will_remove = [False] * m
    for i in range(m):
        if i >= 20 and cnt[i] == 5 and day_tj[i - 20]:
            will_remove[i] = True

    new_add = [False] * m
    for i in range(m):
        if tj_count[i] and (i == 0 or not tj_count[i - 1]):
            new_add[i] = True

    return day_tj, tj_count, will_remove, new_add, bars


# ---------------- 主公式（对聚合后的「符合总数」做时间序列状态机） ----------------
def _ref(x, i, n):
    j = i - n
    return x[j] if j >= 0 else None


def _barslast(cond, i):
    for k in range(i, -1, -1):
        if cond[k]:
            return i - k
    return i + 1  # 从未成立 → 视为全历史


def _hhv(x, i, n):
    s = max(0, i - n + 1)
    vals = [v for v in x[s:i + 1] if v is not None]
    return max(vals) if vals else None


def _count(cond, i, n):
    s = max(0, i - n + 1)
    return sum(1 for k in range(s, i + 1) if cond[k])


def _valuewhen(cond, vals, i):
    for k in range(i, -1, -1):
        if cond[k]:
            return vals[k]
    return None


def main_formula(total, daycnt):
    """输入：符合总数数组 total、当日家数数组 daycnt（按全局交易日升序）。
       返回每根 bar 的信号字典列表。"""
    m = len(total)
    R = [dict() for _ in range(m)]

    up3 = [False] * m
    dn3 = [False] * m
    flat = [False] * m
    for i in range(3, m):
        a = total[i]; a1 = total[i - 1]; a2 = total[i - 2]; a3 = total[i - 3]
        if None in (a, a1, a2, a3):
            continue
        up3[i] = a > a1 > a2 > a3
        dn3[i] = a < a1 < a2 < a3
        c_lt = sum(1 for k in range(i - 2, i + 1) if _ref(total, k, 1) is not None and total[k] < total[k - 1])
        c_eq = sum(1 for k in range(i - 2, i + 1) if _ref(total, k, 1) is not None and total[k] == total[k - 1])
        flat[i] = (c_lt == 2 and c_eq == 1)

    up3_first = [up3[i] and not (i > 0 and up3[i - 1]) for i in range(m)]
    dn3_first = [dn3[i] and not (i > 0 and dn3[i - 1]) for i in range(m)]

    base = [0] * m  # 基础多空
    for i in range(m):
        base[i] = 1 if up3_first[i] else (-1 if dn3_first[i] else 0)
    state = [0] * m  # 基础状态 VALUEWHEN(base<>0, base)
    last = 0
    for i in range(m):
        if base[i] != 0:
            last = base[i]
        state[i] = last

    first_red = [False] * m
    first_green = [False] * m
    for i in range(m):
        p = state[i - 1] if i > 0 else 0
        first_red[i] = (state[i] == 1 and (p == -1 or p == 0))
        first_green[i] = (state[i] == -1 and p == 1)
    bull = [state[i] == 1 for i in range(m)]

    dist_red = [0] * m
    for i in range(m):
        dist_red[i] = _barslast(first_red, i)

    init_burst = [None] * m
    for i in range(m):
        init_burst[i] = _valuewhen(first_red, total, i)
    init_high = [_hhv(total, i, dist_red[i] + 1) for i in range(m)]

    init_break = [False] * m
    init_100 = [False] * m
    init_flat = [False] * m
    init_low_alert = [False] * m
    for i in range(m):
        r1 = _ref(total, i, 1)
        ib = init_burst[i]
        ih = init_high[i]
        if bull[i] and ib is not None and r1 is not None:
            init_break[i] = (total[i] < ib and r1 >= ib)
            init_100[i] = (total[i] < 100 and r1 >= 100 and ih is not None and ih >= 100)
        init_flat[i] = bull[i] and flat[i] and not (i > 0 and flat[i - 1])
        init_low_alert[i] = (init_break[i] or init_100[i] or init_flat[i]) and total[i] < 150

    restart_red = [False] * m
    for i in range(m):
        restart_red[i] = (bull[i] and up3_first[i] and _count(init_low_alert, i, dist_red[i] + 1) > 0 and not first_red[i])
    only_red = [first_red[i] or restart_red[i] for i in range(m)]

    dist_onlyred = [_barslast(only_red, i) for i in range(m)]
    true_burst = [_valuewhen(only_red, total, i) for i in range(m)]
    true_high = [_hhv(total, i, dist_onlyred[i] + 1) for i in range(m)]

    true_break = [False] * m
    true_100 = [False] * m
    true_flat = [False] * m
    true_warn = [False] * m
    low_pulse = [False] * m
    high_pulse = [False] * m
    for i in range(m):
        r1 = _ref(total, i, 1)
        tb = true_burst[i]
        th = true_high[i]
        if bull[i] and tb is not None and r1 is not None:
            true_break[i] = (total[i] < tb and r1 >= tb)
            true_100[i] = (total[i] < 100 and r1 >= 100 and th is not None and th >= 100)
        true_flat[i] = bull[i] and flat[i] and not (i > 0 and flat[i - 1])
        true_warn[i] = true_break[i] or true_100[i] or true_flat[i]
        low_pulse[i] = true_warn[i] and total[i] < 150
        high_pulse[i] = true_warn[i] and total[i] >= 150

    valid_low = [False] * m
    for i in range(m):
        valid_low[i] = low_pulse[i] and _count(low_pulse, i, dist_onlyred[i] + 1) == 1

    valid_high = [False] * m
    for i in range(m):
        # 距前次高警 = BARSLAST(REF(高警脉冲,1))+1
        prev_high = [False] * m
        for k in range(1, i + 1):
            prev_high[k] = high_pulse[k - 1]
        dprev = _barslast(prev_high, i) + 1
        prev_water = _ref(true_high, i, dprev)
        cond = (_count(high_pulse, i, dist_onlyred[i] + 1) == 1) or (true_high[i] is not None and prev_water is not None and true_high[i] > prev_water)
        valid_high[i] = high_pulse[i] and cond

    only_green = [False] * m
    for i in range(m):
        only_green[i] = first_green[i] and _count(low_pulse, i, dist_onlyred[i] + 1) == 0

    valid_all = [valid_low[i] or valid_high[i] for i in range(m)]
    only_alert = [valid_all[i] and not only_red[i] and not only_green[i] for i in range(m)]

    big150 = [daycnt[i] is not None and daycnt[i] >= 150 for i in range(m)]
    half = [False] * m
    for i in range(m):
        p = _ref(daycnt, i, 1)
        half[i] = (daycnt[i] is not None and p is not None and daycnt[i] <= p * 0.5)
    big3 = [_count(big150, i, 3) == 3 for i in range(m)]
    normal_red = [only_red[i] and not big3[i] for i in range(m)]
    super_red = [only_red[i] and big3[i] for i in range(m)]

    for i in range(m):
        R[i] = {
            "red": only_red[i], "green": only_green[i], "alert": only_alert[i],
            "nred": normal_red[i], "sred": super_red[i],
            "big150": big150[i], "half": half[i],
            "ball": (41 if (total[i] is not None and total[i] >= 100) else (40 if (total[i] is not None and total[i] > 0) else None)),
        }
    return R
