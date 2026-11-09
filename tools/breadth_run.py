# -*- coding: utf-8 -*-
"""
市场宽度全量跑批：全A股 → 逐只算 4 输出 → 按交易日聚合 → 主公式 → breadth.json

用法：
  python tools/breadth_run.py --limit 50      # 小样本试跑
  python tools/breadth_run.py                 # 全量
  python tools/breadth_run.py --fresh         # 清缓存重跑

断点续跑：每 200 只把累加器 + 已处理清单落盘到 tools/breadth_cache/state.json，
         中断后直接重跑会自动跳过已完成的股票。
"""
import argparse
import json
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import breadth_lib as B  # noqa: E402

CACHE_DIR = os.path.join(ROOT, "tools", "breadth_cache")
STATE = os.path.join(CACHE_DIR, "state.json")
OUT_DIR = os.path.join(ROOT, "breadth", "data")
OUT = os.path.join(OUT_DIR, "breadth.json")

LMT = 750          # 每只股票取多少根日K（约 3 年）
WORKERS = 10       # 并发数
FLUSH_EVERY = 200  # 每处理多少只落盘一次

_lock = threading.Lock()


def log(*a):
    print(*a, flush=True)


def latest_trade_day():
    """拿上证指数的最后一根日K日期，用来判断「今天的收盘数据出来了没」。"""
    bars = B.fetch_kline("000001", lmt=3, fqt=1)
    return bars[-1]["d"] if bars else None


def wait_ready(max_wait_min=45, step_min=5):
    """收盘后东财要清算一阵子，日K才会补上当天那根。
    这里在跑批前先探一下：如果最新日K还不是今天，就等一会儿再探，
    免得定时任务设早了跑出一份「还差一天」的数据。"""
    now = time.localtime()
    today = time.strftime("%Y-%m-%d", now)
    hhmm = now.tm_hour * 100 + now.tm_min
    if now.tm_wday >= 5:
        log(f"  今天是周末（{today}），按最近交易日数据跑")
        return True
    # 只有「收盘后到当天结束」才需要等今天那根日K。
    # 凌晨/上午/盘中跑批一律用最近一个交易日，否则会一直等一个还不存在的日子。
    if hhmm < 1500:
        log(f"  未到收盘（{now.tm_hour:02d}:{now.tm_min:02d}），按最近交易日数据跑")
        return True
    waited = 0
    while True:
        d = latest_trade_day()
        if d == today:
            log(f"  数据就绪：最新日K = {d}")
            return True
        if waited >= max_wait_min:
            log(f"  等待超时（{max_wait_min} 分钟）：最新日K = {d}，今天 = {today}")
            return False
        log(f"  数据未就绪（最新 {d}，今天 {today}），{step_min} 分钟后重试…")
        time.sleep(step_min * 60)
        waited += step_min


def load_state():
    if not os.path.exists(STATE):
        return None
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_state(agg, done, uni, meta):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"agg": agg, "done": sorted(done), "uni": uni, "meta": meta},
                  f, ensure_ascii=False)
    os.replace(tmp, STATE)


def worker(u):
    """返回 (code, {date: [daycnt, total, willrm, newadd, active]} 或 None)"""
    code = u["code"]
    bars = B.fetch_kline(code, lmt=LMT, fqt=1)
    if not bars:
        return code, None
    # 总股本 = 总市值 / 当前不复权价。停牌无现价时用不复权收盘价倒推
    # （不能用前复权收盘价：前复权价已除过 k，倒推出的股本会偏大 1/k 倍）
    price = u.get("price") or 0
    if price > 0:
        shares = u["mktcap"] / price
    else:
        raw = B.fetch_kline(code, lmt=1, fqt=0)
        rawc = raw[-1]["c"] if raw else 0
        shares = (u["mktcap"] / rawc) if rawc > 0 else None
    if not shares or shares <= 0:
        return code, None

    day_tj, tj_count, will_rm, new_add, bars = B.stock_outputs(bars, shares)
    inc = {}
    for i, b in enumerate(bars):
        d = b["d"][:10]
        e = inc.get(d)
        if e is None:
            e = inc[d] = [0, 0, 0, 0, 0]
        e[4] += 1
        if day_tj[i]:
            e[0] += 1
        if tj_count[i]:
            e[1] += 1
        if will_rm[i]:
            e[2] += 1
        if new_add[i]:
            e[3] += 1
    return code, inc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（调试用）")
    ap.add_argument("--fresh", action="store_true", help="忽略缓存重跑")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--no-wait", action="store_true", help="不等待收盘数据就绪，直接跑")
    ap.add_argument("--out", default="", help="输出路径（默认 breadth/data/breadth.json；云端跑批用）")
    args = ap.parse_args()

    t0 = time.time()
    if not args.no_wait and not wait_ready():
        log("今天的数据还没出来，本次跳过（不发布）")
        sys.exit(2)
    out_path = os.path.abspath(args.out) if args.out else OUT
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    st = None if args.fresh else load_state()
    if st:
        uni = st["uni"]
        agg = st["agg"]
        done = set(st["done"])
        log(f"续跑：股票池 {len(uni)}，已完成 {len(done)}")
    else:
        log("拉取股票池…")
        uni = B.fetch_universe(verbose=True)
        agg = {}
        done = set()
        log(f"股票池 {len(uni)} 只")

    if args.limit:
        uni = uni[:args.limit]
        log(f"[调试] 只跑前 {len(uni)} 只")

    todo = [u for u in uni if u["code"] not in done]
    log(f"待处理 {len(todo)} 只，并发 {args.workers}")

    ok = bad = 0
    cnt = 0
    lock = threading.Lock()

    def flush(force=False):
        nonlocal cnt
        if force or cnt % FLUSH_EVERY == 0:
            save_state(agg, done, uni, {"lmt": LMT, "ts": int(time.time())})

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(worker, u) for u in todo]
        for f in as_completed(futs):
            try:
                code, inc = f.result()
            except Exception as e:
                with _lock:
                    bad += 1
                continue
            with _lock:
                cnt += 1
                done.add(code)
                if inc is None:
                    bad += 1
                else:
                    ok += 1
                    for d, e in inc.items():
                        cur = agg.get(d)
                        if cur is None:
                            agg[d] = e
                        else:
                            for k in range(5):
                                cur[k] += e[k]
                if cnt % FLUSH_EVERY == 0:
                    flush()
                    log(f"  进度 {cnt}/{len(todo)}  有效 {ok}  空/失败 {bad}  "
                        f"{time.time()-t0:.0f}s")
    flush(force=True)

    log(f"抓取完成：有效 {ok}，空/失败 {bad}，耗时 {time.time()-t0:.0f}s")

    # ---- 聚合 ----
    dates = sorted(agg.keys())
    if not dates:
        log("无数据，退出")
        return
    daycnt = [agg[d][0] for d in dates]
    total = [agg[d][1] for d in dates]
    willrm = [agg[d][2] for d in dates]
    newadd = [agg[d][3] for d in dates]
    active = [agg[d][4] for d in dates]

    log(f"交易日 {len(dates)}：{dates[0]} → {dates[-1]}")
    log(f"最新一根：符合总数={total[-1]}  当日家数={daycnt[-1]}  "
        f"将剔除={willrm[-1]}  当日新增={newadd[-1]}  有数据股={active[-1]}")

    log("跑主公式状态机…")
    sig = B.main_formula(total, daycnt)

    # 位掩码压缩：1 red / 2 green / 4 alert / 8 nred / 16 sred / 32 big150 / 64 half
    #            128 ball>=100 / 256 0<x<100
    packed = []
    for s in sig:
        v = 0
        if s["red"]:
            v |= 1
        if s["green"]:
            v |= 2
        if s["alert"]:
            v |= 4
        if s["nred"]:
            v |= 8
        if s["sred"]:
            v |= 16
        if s["big150"]:
            v |= 32
        if s["half"]:
            v |= 64
        if s["ball"] == 41:
            v |= 128
        elif s["ball"] == 40:
            v |= 256
        packed.append(v)

    out = {
        "meta": {
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "universe": len(uni),
            "valid": ok,
            "days": len(dates),
            "from": dates[0],
            "to": dates[-1],
            "lmt": LMT,
            "spec": "全部A股(含北交所) / fqt=1 / RSI9-WR10-WR20 / 总市值>100亿",
        },
        "dates": dates,
        "total": total,
        "daycnt": daycnt,
        "willrm": willrm,
        "newadd": newadd,
        "active": active,
        "sig": packed,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    sz = os.path.getsize(out_path) / 1024
    log(f"已写出 {out_path}  ({sz:.0f} KB)")
    log(f"总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
