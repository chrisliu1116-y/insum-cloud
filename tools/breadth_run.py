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
from datetime import datetime, timedelta, timezone

# GitHub Actions 容器是 UTC。所有「今天/是否收盘/发布时间」都必须用北京时间，
# 否则 UTC 07:20（= 北京 15:20 定时点）会被判成「未到收盘」而跳过等待，
# 跑出昨天的数据；meta.generated 也会比北京时间慢 8 小时。
# 用固定 UTC+8 偏移，不依赖 tzdata / TZ 环境变量，任何机器结果一致。
BJ = timezone(timedelta(hours=8))
def bj_now():
    return datetime.now(BJ)
def bj_today():
    return bj_now().strftime('%Y-%m-%d')
def bj_stamp():
    return bj_now().strftime('%Y-%m-%d %H:%M:%S')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import breadth_lib as B  # noqa: E402

CACHE_DIR = os.path.join(ROOT, "tools", "breadth_cache")
STATE = os.path.join(CACHE_DIR, "state.json")
OUT_DIR = os.path.join(ROOT, "breadth", "data")
OUT = os.path.join(OUT_DIR, "breadth.json")

LMT = 750          # 每只股票取多少根日K（约 3 年）
DELTA_LMT = 80     # 增量模式：只取最近 80 根（RSI9/WR20 暖机后足够覆盖近 ~40 交易日）
WORKERS = 10       # 并发数
FLUSH_EVERY = 200  # 每处理多少只落盘一次

# 本次实际抓取用的 lmt / beg（worker 里读取），由 main() 根据模式设置
LMT_USE = LMT
BEG = None

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
    now = bj_now()
    today = now.strftime("%Y-%m-%d")
    hhmm = now.hour * 100 + now.minute
    if now.weekday() >= 5:
        log(f"  今天是周末（{today} 北京时间），按最近交易日数据跑")
        return True
    # 只有「收盘后到当天结束」才需要等今天那根日K。
    # 凌晨/上午/盘中跑批一律用最近一个交易日，否则会一直等一个还不存在的日子。
    if hhmm < 1500:
        log(f"  未到收盘（北京 {now.hour:02d}:{now.minute:02d}），按最近交易日数据跑")
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
    bars = B.fetch_kline(code, lmt=LMT_USE, fqt=1, beg=BEG)
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
    ap.add_argument("--since", default="", help="补历史起点 YYYYMMDD（配合 --fresh，如 19901201 取全量）")
    ap.add_argument("--incremental", action="store_true", help="增量模式：只抓最近 N 根，追加到既有数据后")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--no-wait", action="store_true", help="不等待收盘数据就绪，直接跑")
    ap.add_argument("--out", default="", help="输出路径（默认 breadth/data/breadth.json；云端跑批用）")
    args = ap.parse_args()

    global LMT_USE, BEG
    if args.since:
        BEG = args.since
        LMT_USE = 20000       # beg 指定起点后，lmt 仍是从最新往回数的上限；
                              # 放大到 20000 让东财返回「起点至今全部」（A 股最多约 8760 根）
    elif args.incremental:
        LMT_USE = DELTA_LMT
        BEG = None
    else:
        LMT_USE = LMT
        BEG = None

    t0 = time.time()
    if not args.no_wait and not wait_ready():
        log("今天的数据还没出来，本次跳过（不发布）")
        sys.exit(2)
    out_path = os.path.abspath(args.out) if args.out else OUT
    # 增量模式：先读既有数据，稍后把「今天新出的交易日」追加到它后面。
    old = None
    if args.incremental and not args.fresh and not args.limit and os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            if not (old.get("dates") and old.get("total") is not None):
                old = None
        except Exception:
            old = None
        if old:
            log(f"增量模式：已有 {len(old['dates'])} 个交易日（{old['dates'][0]} → {old['dates'][-1]}），"
                f"本次只抓最近 {DELTA_LMT} 根日K")
    # 幂等：多时间点兜底 cron 会触发多次。若现有数据已是「今天且完整」，
    # 直接跳过，避免重复抓 5584 只 / 重复提交。（--fresh 或 --limit 调试不受影响）
    if not args.fresh and not args.limit and os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            om = old.get("meta", {})
            if om.get("to") == bj_today() and int(om.get("valid", 0)) >= 5000:
                # v276：宽度已是今日时，仍要检查上证 sse.json 是否缺/旧。
                # 否则会出现“insum 新 / 上证旧”并直接被幂等闸跳过。
                sse_path = os.path.join(os.path.dirname(out_path), "sse.json")
                sse_old = None
                try:
                    sse_old = json.load(open(sse_path, encoding="utf-8"))
                except Exception:
                    sse_old = None
                if not sse_is_valid(sse_path, om.get("to")):
                    log("宽度已今日但 sse.json 缺失/旧，补齐上证日K")
                    write_sse(sse_path)
                else:
                    log(f"数据已是今日完整版（{om.get('to')}，有效 {om.get('valid')} 只，sse {sse_old['meta']['to']}），跳过本次跑批")
                return
        except Exception as e:
            log(f"  幂等检查跳过（{e}）")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    st = None if (args.fresh or args.incremental or args.since) else load_state()
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
    if old:
        # 增量合并：只把「晚于既有最后交易日」的新日子追加进去。
        last = old["dates"][-1]
        new_dates = [d for d in sorted(agg.keys()) if d > last]
        log(f"新增交易日 {len(new_dates)} 个（> {last}）")
        if not new_dates:
            # v276：宽度已是今日但没有 sse.json 时，也补齐上证 K 线；
            # 避免今天“insum 已更新 / sse 缺失或旧”的半新半旧状态持续到收盘后。
            if bj_today() == last:
                sse_path = os.path.join(os.path.dirname(out_path), "sse.json")
                if not sse_is_valid(sse_path, last):
                    log("宽度已今日但 sse.json 缺失/旧，补齐上证日K")
                    write_sse(sse_path)
            else:
                log("没有新交易日，数据保持不变，跳过写入")
            return
        dates = old["dates"] + new_dates
        daycnt = old["daycnt"] + [agg[d][0] for d in new_dates]
        total = old["total"] + [agg[d][1] for d in new_dates]
        willrm = old["willrm"] + [agg[d][2] for d in new_dates]
        newadd = old["newadd"] + [agg[d][3] for d in new_dates]
        active = old["active"] + [agg[d][4] for d in new_dates]
    else:
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
            "generated": bj_stamp(),          # 北京时间，页面可直接展示
            "generated_tz": "Asia/Shanghai (UTC+8)",
            "run_date": bj_today(),           # 本次跑批的北京日期
            "trade_date": dates[-1],          # 数据覆盖到的最后交易日
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

    # v276：定时全量批次才生成/提交上证 K 线；小样本自测不覆盖线上 sse.json
    if not args.limit:
        write_sse(os.path.join(os.path.dirname(out_path), "sse.json"))


EM_SPOT = "https://push2.eastmoney.com/api/qt/stock/get"
SSE_SECID = "1.000001"          # 上证指数（沪市 1.）；写成 000001 会被判成深市平安银行


def fetch_index_bars(lmt=10000, beg="19900101"):
    """上证指数全量日K（显式 secid = 1.000001）。

    ⚠️ 不能用 B.fetch_kline("000001")：`secid_of` 把 0 开头判成深市 → **0.000001 = 平安银行**，
    抓回来的是个股（末收盘 ~11.71、起点 1991-04-03、8515 根），而指数是 3680 量级、
    起点 1990-12-19。这个错会让"上证 K 线"整条画错却看起来有数据。
    """
    params = {
        "secid": SSE_SECID, "klt": 101, "fqt": 1, "lmt": lmt, "end": "20500101",
        "beg": beg, "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    d = B._get(B.EM_KLINE, params)
    if not d or not d.get("data") or not d["data"].get("klines"):
        return []
    out = []
    for line in d["data"]["klines"]:
        p = line.split(",")
        if len(p) < 7:
            continue
        try:
            out.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                        "h": float(p[3]), "l": float(p[4]),
                        "v": float(p[5]), "a": float(p[6])})
        except Exception:
            continue
    return out


def fetch_spot_bar_000001():
    """收盘后用 push2 实时快照拼出「当日」上证 bar。

    为什么需要它：push2his 的日 K 常常要等几分钟到几十分钟才落当天那根（有时
    干脆当天不落，只能等次日），而 push2 快照在收盘那一刻就把 开/高/低/收/量/额
    全部定型（实测 16:11 时间戳、六项齐全）。指数日线这 6 个值属于「收盘即终值」，
    清算/交收改的是资金与持仓，不会回头改它们 —— 所以不必等"清算"。

    返回 {d,o,c,h,l,v,a,_ts,_src}；拿不到或非当日返回 None。
    """
    d = B._get(EM_SPOT, {
        "secid": "1.000001", "fltt": 2, "invt": 2,
        "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f86",
    }, retries=3)
    x = (d or {}).get("data") or {}
    ts = x.get("f86")
    if not ts:
        return None
    try:
        dt = datetime.fromtimestamp(int(ts), BJ)
    except Exception:
        return None
    o, c, h, l = x.get("f46"), x.get("f43"), x.get("f44"), x.get("f45")
    v, a = x.get("f47"), x.get("f48")
    if not all(isinstance(z, (int, float)) and z > 0 for z in (o, c, h, l)):
        return None
    return {"d": dt.strftime("%Y-%m-%d"), "o": float(o), "c": float(c),
            "h": float(h), "l": float(l), "v": float(v or 0), "a": float(a or 0),
            "_ts": dt.strftime("%Y-%m-%d %H:%M:%S"), "_src": "spot"}


def sse_is_valid(path, today):
    """sse.json 是否可直接复用：日期是今日 **且** 末收盘在指数点位量级。

    只查日期是不够的：抓成个股时（secid 0.000001 = 平安银行，末收盘 11.71）
    meta.to 同样是今日，会被误判成「已是最新」而永久跳过重写 —— 线上就一直是错数据。
    """
    try:
        o = json.load(open(path, encoding="utf-8"))
    except Exception:
        return False
    if (o.get("meta") or {}).get("to") != today:
        return False
    c = o.get("c") or []
    return bool(c) and 50 < c[-1] < 20000


def write_sse(sse_path, breadth_path=None):
    """同批次生成上证全量日K（data/sse.json）。..."""
    bars = fetch_index_bars(lmt=10000)
    if not bars:
        raise RuntimeError("上证全量日K抓取为空，拒绝写入 sse.json")
    # 标的护栏：指数点位量级 + 起点年份。抓成个股（如平安银行 11.71 / 1991-04-03）
    # 时这里会直接拒绝写入，不再静默产出一条"看起来有数据"的错 K 线。
    if not (50 < bars[-1]["c"] < 20000):
        raise RuntimeError(f"上证日K末根收盘 {bars[-1]['c']} 不在指数点位量级，疑似抓错标的，拒绝写入")
    if bars[0]["d"][:4] > "1992":
        raise RuntimeError(f"上证日K起点 {bars[0]['d']} 偏晚（应为 1990-12-19 起），疑似抓错标的，拒绝写入")
    b_path = breadth_path or os.path.join(os.path.dirname(sse_path), "breadth.json")
    with open(b_path, encoding="utf-8") as f:
        b_last = json.load(f)["dates"][-1]
    # v277：日K未落当天那根时，用 push2 收盘快照补一根，不再等行情源「清算」
    # v277：当日那根一律用收盘快照，不等日K接口，也不等"清算"。
    # 用户口径：「insum 一更新，就从上证抓一个快照过来，两边同步更新」。
    # 指数收盘价/开高低/量/额在收盘瞬间定型，快照与 insum 同一时刻取到，
    # 天然对齐；日K接口当日那根反而可能滞后几十分钟。
    src = "kline"
    spot_meta = None
    sp = fetch_spot_bar_000001()
    if sp and sp["d"] == b_last:
        bar = {k: sp[k] for k in ("d", "o", "c", "h", "l", "v", "a")}
        if bars[-1]["d"][:10] == b_last:
            bars[-1] = bar
            log(f"当日 bar 用收盘快照覆盖（{sp['_ts']}，收 {sp['c']}）—— 与 insum 同批同步")
        else:
            bars.append(bar)
            log(f"日K缺 {b_last}，用收盘快照补一根（{sp['_ts']}，收 {sp['c']}）")
        src = "kline+snapshot"
        spot_meta = {"spot_d": sp["d"], "spot_ts": sp["_ts"], "spot_close": sp["c"]}
    elif bars[-1]["d"][:10] < b_last:
        log(f"日K缺 {b_last}，且收盘快照不可用（{sp and sp['d'] or 'no-data'}）")
    dates = [b["d"][:10] for b in bars]
    last = dates[-1]
    if last != b_last:
        raise RuntimeError(f"上证日K末日 {last} != breadth 交易日 {b_last}，拒绝半新半旧提交")
    o = [round(b["o"], 2) for b in bars]
    c = [round(b["c"], 2) for b in bars]
    h = [round(b["h"], 2) for b in bars]
    l = [round(b["l"], 2) for b in bars]
    payload = {
        "meta": {
            "generated": bj_stamp(),
            "generated_tz": "Asia/Shanghai (UTC+8)",
            "source": "eastmoney 1.000001",
            "to": last,
            "from": dates[0],
            "days": len(dates),
            # v277 数据来源可审计：kline = 日K接口；kline+snapshot = 末根由收盘快照补齐
            "to_src": src,
            **({"spot": spot_meta} if spot_meta else {}),
        },
        "dates": dates, "o": o, "h": h, "l": l, "c": c,
    }
    with open(sse_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"已写出 {sse_path}  上证 {dates[0]} → {last}  ({len(dates)} 根)")


if __name__ == "__main__":
    main()
