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
import trade_calendar as TC  # noqa: E402  v297 交易日判断

# v292（2026-11-14）：数据源可切换。
#   BREADTH_SOURCE=tencent（默认）：腾讯日K + 腾讯总市值，零东财请求；
#   BREADTH_SOURCE=eastmoney：原东财链路（解封后切回，历史更全）。
# 两种源共用同一套计算与合并逻辑；既有 breadth.json 的 1990 起历史不会被覆盖，
# 新源只负责「晚于最后一根」的增量。
SRC = os.environ.get("BREADTH_SOURCE", "tencent").strip().lower()
if SRC == "tencent":
    import tx_lib as P  # noqa: E402
else:
    P = B

CACHE_DIR = os.path.join(ROOT, "tools", "breadth_cache")
STATE = os.path.join(CACHE_DIR, "state.json")
OUT_DIR = os.path.join(ROOT, "breadth", "data")
OUT = os.path.join(OUT_DIR, "breadth.json")

LMT = 750          # 每只股票取多少根日K（约 3 年）
DELTA_LMT = 80     # 增量模式：只取最近 80 根（RSI9/WR20 暖机后足够覆盖近 ~40 交易日）
WORKERS = int(os.environ.get("BREADTH_WORKERS", "4"))  # 并发数（腾讯源实测>10易触发501）
FLUSH_EVERY = 200  # 每处理多少只落盘一次

# 方案1（2026-11-12）：股票池 / 有效抓取数低于此值视为「东财整站风控」，
# 不写假数据，仅标注 stale。与 .github/workflows/breadth.yml 提交闸门 u<5000 对齐。
STALE_MIN_UNIVERSE = 5000

# 本次实际抓取用的 lmt / beg（worker 里读取），由 main() 根据模式设置
LMT_USE = LMT
BEG = None

_lock = threading.Lock()
# v361d【熔断配置来自源模块】两个源各自带配置文件，主流程不写死任何源的条件：
#   东财 breadth_lib.CIRCUIT = enabled（11-15 实测 200~400 只区间失败率 30%，
#     连续 30 只无效即停发，避免重试正反馈把短时窗口打穿）；
#   腾讯 tx_lib.CIRCUIT = disabled（按之前跑法，名录里 19 个历史无效代码
#     会分散出现，东财那套熔断会误伤）。
# 置位后已提交的 worker 直接返回（不再发请求），整批判 stale 不写残缺数据。
_ABORT = threading.Event()
_CIRCUIT = getattr(P, 'CIRCUIT', None) or {}
_CIRCUIT_ON = bool(_CIRCUIT.get('enabled'))
_CIRCUIT_MAX = int(_CIRCUIT.get('max_consec_bad') or 0)


def log(*a):
    print(*a, flush=True)


# ===== v387b【跑批可追溯日志】所有源共用，每次真正跑批后 append 一行结构化记录 =====
# 用户要求：跑批时间限制对所有源生效外，还要有日志功能——出问题能回头追溯原因。
# 落在仓库 breadth/data/runlog.jsonl（随代码提交长期保留、可 git log 追溯），
# 每次跑批（成功/失败/急停/幂等跳过）都记一条，字段固定便于脚本汇总。
RUNLOG = os.path.join(OUT_DIR, "runlog.jsonl")


def write_runlog(conclusion, *, reason="", src="", workers=0, elapsed=0, rps=0,
                 ok=0, bad=0, n501=0, universe=0, trade_date="", new_days=0):
    """append 一行 JSON 到 runlog.jsonl；失败不影响跑批主流程。"""
    rec = {
        "ts": bj_stamp(), "source": src or SRC, "workers": workers,
        "conclusion": conclusion, "reason": reason,
        "elapsed_sec": round(elapsed), "avg_rps": round(rps),
        "ok": ok, "bad": bad, "n501": n501, "universe": universe,
        "trade_date": trade_date, "new_days": new_days,
    }
    try:
        os.makedirs(os.path.dirname(RUNLOG), exist_ok=True)
        with open(RUNLOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log(f"[runlog] {conclusion} {reason} 已记录")
    except Exception as e:
        log(f"[runlog] 写入失败（不影响跑批）：{e}")


def latest_trade_day():
    """拿上证指数的最后一根日K日期，用来判断「今天的收盘数据出来了没」。

    ⚠️ 不能用 B.fetch_kline("000001")：secid_of 会把 000001 判成深市 → 0.000001
    （平安银行个股），与文件下方 fetch_index_bars 的注释自相矛盾。必须走显式
    secid=1.000001 的 fetch_index_bars（上证指数）。该函数定义在文件后半段，
    函数体在调用时才解析全局名，前向引用没有问题。
    """
    bars = fetch_index_bars(lmt=3)
    return bars[-1]["d"] if bars else None


def wait_ready():
    """跑批前判断数据是否就绪（v278：收盘即就绪）。

    工作日北京 15:00 之后直接放行：指数开/高/低/收/量/额在收盘瞬间定型，
    当日那根由 write_sse() 用 push2 收盘快照补齐，不再等东财日K接口「清算」
    后补当天那根，也不再以 latest_trade_day()==today 作条件。
    旧逻辑会 sleep 最多 45 分钟再 sys.exit(2)，在日K当天不落/滞后的日子整批
    漏跑，是「insum 新 / 上证旧」的残留阻塞。
    15:00 前与周末的原有行为保留（按最近一个交易日数据跑）。
    """
    now = bj_now()
    today = now.strftime("%Y-%m-%d")
    hhmm = now.hour * 100 + now.minute
    # v297：先用「工作日−法定节假日」判定今天该不该有数据。
    # 非交易日按最近交易日跑（周末/节假日不产生新行，跑了也无害但不必要）。
    if not TC.is_trade_day(today):
        log(f"  {today} 不是交易日（周末/法定节假日），按最近交易日数据跑")
        return True
    if now.weekday() >= 5:
        log(f"  今天是周末（{today} 北京时间），按最近交易日数据跑")
        return True
    # 凌晨/上午/盘中跑批一律用最近一个交易日，否则会等一个还不存在的日子。
    if hhmm < 1500:
        log(f"  未到收盘（北京 {now.hour:02d}:{now.minute:02d}），按最近交易日数据跑")
        return True
    # 工作日 15:00 之后：收盘时间即就绪，当日那根由 push2 快照补齐。
    log(f"  已收盘（北京 {now.hour:02d}:{now.minute:02d}），直接就绪；"
        f"当日上证那根由收盘快照补齐，不等日K清算")
    return True


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
    if _ABORT.is_set():          # v361：熔断后不再发任何东财 kline 请求
        return code, None
    bars = P.fetch_kline(code, lmt=LMT_USE, fqt=1, beg=BEG)
    if not bars:
        return code, None
    # 总股本 = 总市值 / 当前不复权价。停牌无现价时用不复权收盘价倒推
    # （不能用前复权收盘价：前复权价已除过 k，倒推出的股本会偏大 1/k 倍）
    price = u.get("price") or 0
    if price > 0:
        shares = u["mktcap"] / price
    else:
        raw = P.fetch_kline(code, lmt=1, fqt=0)
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


def stale_reason_of(uni, ok, agg):
    """方案1 触发判据：东财全挂 / 股票池明显缩水。

    返回原因字符串；空字符串表示正常、不触发。与 breadth.yml 提交闸门
    u<5000 对齐：股票池或有效抓取数任一低于 5000 即视为本次跑批失败。
    """
    n = len(uni)
    if n < STALE_MIN_UNIVERSE:
        return f"股票池缩水 {n} < {STALE_MIN_UNIVERSE}（东财 clist/get 疑似风控）"
    if ok < STALE_MIN_UNIVERSE:
        return f"有效日K {ok} < {STALE_MIN_UNIVERSE}（东财 push2his 疑似全挂）"
    if not agg:
        return "聚合结果为空（无任何有效日K）"
    return ""


def mark_stale(out_path, reason):
    """保留数据序列不变，仅在 meta 写 stale 标记。返回是否成功标注。

    - trade_date 保留上次成功日（老文件可能只有 to / dates 末日，兜底取之）；
    - stale_since 只在首次进入 stale 时记录（北京时间 ISO），不随每日失败跑批刷新；
    - 原子写（tmp + os.replace），避免写坏既有 breadth.json。
    无既有文件或数据序列缺失时返回 False，调用方据此「不写假数据」。
    """
    if not os.path.exists(out_path):
        return False
    try:
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not (data.get("dates") and data.get("total")):
            return False
    except Exception:
        return False
    m = data.setdefault("meta", {})
    td = m.get("trade_date") or m.get("to") or data["dates"][-1]
    if not m.get("stale"):
        m["stale_since"] = bj_now().isoformat()
    m["stale"] = True
    m["trade_date"] = td
    m["stale_reason"] = reason
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out_path)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（调试用）")
    ap.add_argument("--fresh", action="store_true", help="忽略缓存重跑")
    ap.add_argument("--since", default="", help="补历史起点 YYYYMMDD（配合 --fresh，如 19901201 取全量）")
    ap.add_argument("--incremental", action="store_true", help="增量模式：只抓最近 N 根，追加到既有数据后")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--no-wait", action="store_true", help="不等待收盘数据就绪，直接跑")
    ap.add_argument("--recheck", action="store_true",
                    help="收盘后复核轮：即使今日数据已完整也强制重跑，覆盖当日行（清算/收盘修正）")
    ap.add_argument("--fix-last", action="store_true",
                    help="修复末日行：保留全部历史，用本次重算的末日值覆盖最后一根（坏数据修复用，配合 --incremental）")
    ap.add_argument("--out", default="", help="输出路径（默认 breadth/data/breadth.json；云端跑批用）")
    ap.add_argument("--no-probe", action="store_true",
                    help="跳过跑批前的探针链(clist+kline)直接一次跑完；周日恢复探测用"
                         "（用户2026-11-17决定：探不通也跑不成，探针只是多2请求；"
                         "超时/熔断已能止损，护栏仍能拒绝脏数据）")
    ap.add_argument("--verbose-fail", action="store_true",
                    help="v466：失败时打印具体原因(HTTP码/正文片段/异常/代理出口IP)，"
                         "禁止只报'0只'不给原因。用户2026-11-19明确要求。")
    args = ap.parse_args()
    # v466：把详细失败日志开关传给 breadth_lib
    if args.verbose_fail:
        B.VERBOSE_FAIL = True

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
    # v293【防混拼】腾讯源绝不向非腾讯历史追加（东财 1990 起与腾讯口径不同）。
    if not args.fresh and not args.limit and os.path.exists(out_path):
        try:
            _o = json.load(open(out_path, encoding="utf-8"))
            _src_old = ((_o.get("meta") or {}).get("source") or "").lower()
            if SRC == "tencent" and _src_old != "tencent":
                log("护栏：现有 breadth.json 不是腾讯源，拒绝增量（避免两源混拼）。请先完成腾讯全量重建。")
                return
            # v294【双向护栏】东财增量也绝不能追加到腾讯历史上：两源历史长度/口径不同。
            if SRC == "eastmoney" and _src_old == "tencent":
                log("护栏：现有 breadth.json 是腾讯源，拒绝用东财增量追加（避免两源混拼）。"
                    "切回东财请先把 breadth.json 换回东财历史（breadth/data/_archive_breadth_em_*.json）。")
                return
        except Exception:
            pass
    # 幂等：多时间点兜底 cron 会触发多次。若现有数据已是「今天且完整」，
    # 直接跳过，避免重复抓 5584 只 / 重复提交。（--fresh / --recheck / --limit 不受影响）
    # v291【recheck】15:30 之后的复核轮必须绕过幂等闸：盘后清算价会微调，
    # 首次跑批（15:0x）拿到的收盘快照可能不是最终值，需要重抓覆盖当日行。
    if args.recheck:
        log("复核轮（--recheck）：忽略「今日已完整」幂等闸，强制重抓并覆盖当日行")
    if (not args.fresh and not args.recheck and not args.fix_last and not args.limit
            and os.path.exists(out_path)):
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
                write_runlog("idempotent", reason=f"今日数据已完整 {om.get('to')}",
                             trade_date=om.get("to", ""))
                return
        except Exception as e:
            log(f"  幂等检查跳过（{e}）")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    st = None if (args.fresh or args.recheck or args.incremental or args.since) else load_state()
    if st:
        uni = st["uni"]
        agg = st["agg"]
        done = set(st["done"])
        log(f"续跑：股票池 {len(uni)}，已完成 {len(done)}")
    else:
        # v361【健康检查】1 个 clist 请求确认股票池完整，0 只/断连立刻停批。
        # 只在 GHA runner 里执行，本机不会向东财发任何请求。
        # v361d【源专属探针链】探什么、阈值多少，由源模块自己的配置决定：
        #   东财 breadth_lib.PROBE_CHAIN = [clist>=5000, kline>=1]（2 请求止损）；
        #   腾讯 tx_lib.PROBE_CHAIN = []（之前的跑法没有探针，保持原样）。
        # v412【--no-probe】周日恢复探测跳过探针，一次连续跑完（用户 2026-11-17 决定）：
        # 探针探不通本来也跑不成，只是多 2 个请求；真跑的 watchdog(600s)/stall(120s)/
        # 501 熔断(5 次)/提交护栏(uni<5000,ok<5000) 已能止损且拒绝脏数据，探针冗余。
        if getattr(args, 'no_probe', False):
            log("[no-probe] 周日探测：跳过探针链，一次连续跑完（靠 watchdog/熔断/护栏止损）")
        for _nm, _fn, _min in (getattr(P, 'PROBE_CHAIN', None) or []):
            if args.limit or getattr(args, 'no_probe', False):
                break
            _v = _fn()
            log(f"{_nm} 健康检查：{_v}（期望 >= {_min}）")
            if _v < _min:
                log(f"{_nm} 未通过：疑似端点级限流，本次不跑批，退出码 3")
                sys.exit(3)
        # v469【协议攻坚】生命周期一次：pz=1 小包探测 http明文/socks5h/直连/https代理
        # 锁定能拿到合法JSON的组合，物理规避 BoringSSL 走 CONNECT 隧道收 pz=100 大包的冲突
        if hasattr(B, 'probe_transport'):
            log("协议探测（pz=1小包）：http明文 → socks5h → 直连 → https代理")
            B.probe_transport()
        log("拉取股票池…")
        uni = P.fetch_universe(verbose=True)
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
    consec_bad = 0
    lock = threading.Lock()
    # v386c：速率计时从「kline 循环开始」算，不把股票池拉取(本轮实测 31s)算进去。
    # 否则 5765 请求 / 78s = 74 req/s 会误导成「没喂饱 140」，实际 kline 47s/5765 ≈ 123 req/s。
    t_fetch = time.time()

    # ===== v386b 全程监控 + 自动急停 =====
    # 用户要求：跑批过程中可全程监控、发现卡住/风控能及时停，绝不允许无限期跑下去。
    # 三道自动急停（任一命中即置位 _ABORT + P.request_stop()，worker 立即停发请求）：
    #   ① 卡住：progress 计数 N 秒无增长（默认 120s）→ 无论卡在哪个请求都止损；
    #   ② 501 计数：令牌桶累计 501 >= 阈值（默认 5）→ 确认触发腾讯反爬，立刻停；
    #   ③ 总时长：超过 MAX_RUN_SEC（默认 600s=10min）→ 兜底硬闸，绝不无限跑。
    # 三层超时：watchdog 10min（脚本内停+写原因） < 探测轮询1000s < 平台25min强杀。
    # 所有阈值可用环境变量覆盖；_watch_reason 供退出日志写明命中哪一条。
    MAX_RUN_SEC = int(os.environ.get("BREADTH_MAX_RUN_SEC", "600"))
    STALL_SEC = int(os.environ.get("BREADTH_STALL_SEC", "120"))
    MAX_501 = int(os.environ.get("BREADTH_MAX_501", "5"))
    _watch_reason = {"v": ""}
    _snap = {"cnt": 0, "t": time.time(), "ok": 0, "bad": 0}  # watchdog 读的最新进度快照
    _has_bucket = hasattr(P, "BUCKET")

    def watchdog():
        while not _ABORT.is_set():
            time.sleep(5)
            now = time.time()
            elapsed = now - t0
            with _lock:
                c = _snap["cnt"]
                last_t = _snap["t"]
                _ok, _bad = _snap["ok"], _snap["bad"]
            stall = now - last_t
            why = ""
            if elapsed > MAX_RUN_SEC:
                why = f"超过总时长上限 {MAX_RUN_SEC}s（已跑 {elapsed:.0f}s）"
            elif _has_bucket:
                _tot, n501, _rate = P.BUCKET.stats()
                if n501 >= MAX_501:
                    why = f"501 反爬累计 {n501} >= {MAX_501}，确认触发风控"
            if not why and c > 0 and stall > STALL_SEC:
                why = f"卡住：{STALL_SEC}s 内进度无增长（停在 {c}/{len(todo)}）"
            if why:
                _watch_reason["v"] = why
                _ABORT.set()
                if hasattr(P, "request_stop"):
                    P.request_stop()
                # v386h：命中急停时把当时的进度/速率/失败数一起打出，
                # 用户要「从日志找原因」——一眼分清：卡住(c 不动) / 限流(501或bad高) /
                # 并发不足(c 在涨但慢)。速率用实际请求数或完成数÷耗时。
                _el = time.time() - t_fetch
                _rate_txt = ""
                if _has_bucket:
                    _tot, _n501, _rr = P.BUCKET.stats()
                    _rate_txt = f" · 已发{_tot}请求/{_tot/_el:.0f}req/s · 501={_n501}"
                elif _el > 0:
                    _rate_txt = f" · {c/_el:.0f}req/s"
                log(f"  !! WATCHDOG 急停：{why} | 快照 进度{c}/{len(todo)} "
                    f"有效{_ok} 失败{_bad} 停滞{stall:.0f}s{_rate_txt}")
                return

    def flush(force=False):
        nonlocal cnt
        if force or cnt % FLUSH_EVERY == 0:
            save_state(agg, done, uni, {"lmt": LMT, "ts": int(time.time())})

    wd = threading.Thread(target=watchdog, daemon=True, name="breadth-watchdog")
    wd.start()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(worker, u) for u in todo]
        for f in as_completed(futs):
            try:
                code, inc = f.result()
            except Exception as e:
                with _lock:
                    bad += 1
                    consec_bad += 1
                    _snap["ok"], _snap["bad"], _snap["t"] = ok, bad, time.time()
                    if _CIRCUIT_ON and consec_bad >= _CIRCUIT_MAX and not _ABORT.is_set():
                        _ABORT.set()
                        log(f"  !! 熔断：连续 {consec_bad} 只异常，停发新的 kline 请求")
                continue
            with _lock:
                cnt += 1
                done.add(code)
                _snap["cnt"] = cnt
                _snap["t"] = time.time()
                # v386b：每 500 只打一行——进度 / 有效 / 失败 / 实时速率 / 501 / 已耗时。
                # req/s 用令牌桶发出的真实请求数 / 已耗时（不是成功的只数），
                # 这样能直接看出是否贴近 140 上限、是否被 adaptive 降速。
                if cnt % 500 == 0:
                    el = time.time() - t_fetch
                    if _has_bucket:
                        tot, n501, rate = P.BUCKET.stats()
                        rate_line = (f" · 已发 {tot} 请求 / 实测 {tot/el:.0f} req/s"
                                     f" · 桶上限 {rate:.0f} · 501={n501}")
                    else:
                        # v386g：东财源无令牌桶，但同样打实测速率——慢速诊断要看
                        # 「每500只耗时 + req/s」才能区分「并发不足」还是「端点限流」。
                        rate_line = f" · 实测 {cnt/el:.0f} req/s"
                    eta = f" · ETA {el/cnt*(len(todo)-cnt):.0f}s" if cnt else ""
                    log(f"  进度 {cnt}/{len(todo)} · 有效 {ok} · 失败 {bad}"
                        f"{rate_line}{eta} · 已跑 {el:.0f}s")
                if inc is None:
                    bad += 1
                    consec_bad += 1
                    if _CIRCUIT_ON and consec_bad >= _CIRCUIT_MAX and not _ABORT.is_set():
                        _ABORT.set()
                        log(f"  !! 熔断：连续 {consec_bad} 只无有效日K，停发新的 kline 请求")
                else:
                    ok += 1
                    consec_bad = 0
                    for d, e in inc.items():
                        cur = agg.get(d)
                        if cur is None:
                            agg[d] = e
                        else:
                            for k in range(5):
                                cur[k] += e[k]
                _snap["ok"], _snap["bad"] = ok, bad   # v386h：供 watchdog 急停时打印快照
                if cnt % FLUSH_EVERY == 0:
                    flush()
    if not _ABORT.is_set():
        flush(force=True)

    el = time.time() - t_fetch
    rate_line = ""
    if _has_bucket:
        tot, n501, rate = P.BUCKET.stats()
        rate_line = f" · 已发 {tot} 请求 / 平均 {tot/el:.0f} req/s · 终速 {rate:.0f} · 501={n501}"
    else:
        # v386g：东财源同样输出平均速率，供周末探测的耗时分析脚本判定
        rate_line = f" · 平均 {ok/el:.0f} req/s" if el > 0 else ""
    log(f"抓取完成：有效 {ok}，空/失败 {bad}，耗时 {el:.0f}s{rate_line}")
    _avg_rps = (ok / el) if el > 0 else 0
    _n501 = P.BUCKET.stats()[1] if _has_bucket else 0
    if _ABORT.is_set():
        # 熔断/急停后结果必然不完整：直接标 stale，绝不写残缺序列、也不落 state 供续跑
        _reason = (_watch_reason["v"] or
                   f"连续失败熔断（连续 {_CIRCUIT_MAX} 只无有效日K，短时风控）")
        log(f"方案1 护栏触发：{_reason}")
        if mark_stale(out_path, _reason):
            log(f"  已保留上次成功 breadth.json 并写入 stale 标记：{out_path}")
        write_runlog("abort", reason=_reason, workers=args.workers, elapsed=el,
                     rps=_avg_rps, ok=ok, bad=bad, n501=_n501, universe=len(uni))
        return

    # ===== 方案1（2026-11-12）：失败不静默停更 =====
    # 东财整站风控有两种表现：clist/get 拿不到全量股票池（uni 缩水），
    # 或 push2his 日K 全挂（ok≈0）。任一成立都不写「假数据」覆盖已有 breadth.json，
    # 只保留上次成功的数据序列，并在 meta 明确标注 stale / trade_date / stale_since。
    # 正常路径（股票池>=5000 且 ok>=5000）判断为空，下面聚合/写入一字不改地继续。
    # --limit 是主动缩小股票池的调试开关，跳过本护栏，避免污染线上文件 meta。
    if not args.limit:
        reason = stale_reason_of(uni, ok, agg)
        if reason:
            log(f"方案1 护栏触发：{reason}")
            log("  不覆盖数据序列，仅标注 meta.stale；trade_date 保留上次成功日")
            if mark_stale(out_path, reason):
                log(f"  已保留上次成功 breadth.json 并写入 stale 标记：{out_path}")
            else:
                log("  无既有 breadth.json 可保留；不写假数据，本次不产出")
            write_runlog("stale", reason=reason, workers=args.workers, elapsed=el,
                         rps=_avg_rps, ok=ok, bad=bad, n501=_n501, universe=len(uni))
            return

    # ---- 聚合 ----
    if old:
        # 增量合并：只把「晚于既有最后交易日」的新日子追加进去。
        last = old["dates"][-1]
        new_dates = [d for d in sorted(agg.keys()) if d > last]
        log(f"新增交易日 {len(new_dates)} 个（> {last}）")
        if args.fix_last:
            # v386e【修复末日行】保留全部历史，用本次重算的末日值覆盖最后一根。
            # 用于修复「有数据但四指标全 0」这类坏数据：历史一字不改，只换末日行。
            if last in agg:
                old["daycnt"][-1], old["total"][-1] = agg[last][0], agg[last][1]
                old["willrm"][-1], old["newadd"][-1] = agg[last][2], agg[last][3]
                old["active"][-1] = agg[last][4]
                log(f"修复末日行 {last}：daycnt={agg[last][0]} total={agg[last][1]} "
                    f"willrm={agg[last][2]} newadd={agg[last][3]} active={agg[last][4]}")
            else:
                log(f"修复失败：本次 agg 里没有末日 {last} 的数据（可能日K未覆盖当日）")
                write_runlog("fail", reason=f"fix-last 无末日 {last} 数据", workers=args.workers,
                             elapsed=el, rps=_avg_rps, ok=ok, bad=bad, n501=_n501, universe=len(uni))
                sys.exit(3)
            dates, daycnt, total = old["dates"], old["daycnt"], old["total"]
            willrm, newadd, active = old["willrm"], old["newadd"], old["active"]
            # 修复模式也要重写 sse（末日那根快照不受影响，但保持一致）
            sse_path = os.path.join(os.path.dirname(out_path), "sse.json")
            if not sse_is_valid(sse_path, last):
                write_sse(sse_path)
        elif not new_dates:
            # v341：只要 breadth 最后一根比 sse 新，就补齐上证 K 线。
            # 旧逻辑只在 bj_today()==last 时补，周末/手动发布 breadth 后会留下
            # 「宽度新 / 上证旧」的半新半旧状态（用户 11-14 实测）。
            sse_path = os.path.join(os.path.dirname(out_path), "sse.json")
            if not sse_is_valid(sse_path, last):
                log(f"宽度末日 {last} 新于 sse，补齐上证日K")
                write_sse(sse_path)
            # v297【交易日校验】今天该有数据却没拿到 → 明确失败退出，绝不静默成功。
            # 旧逻辑在这里一律「数据保持不变，跳过写入」+ return 0，
            # 于是「交易日但当天K线没落」会被当成成功：CF 的 todayPublished 看不到
            # 新数据会继续重试，但工作流本身显示绿灯，没人知道到底跑没跑出来。
            today_str = bj_today()
            if TC.is_trade_day(today_str) and last < today_str:
                log(f"错误：{today_str} 是交易日，但数据只到 {last}（当天未更新）。"
                    f"本次不发布，退出码 3（让 CF 下一轮继续重试并暴露问题）。")
                write_runlog("fail", reason=f"交易日 {today_str} 数据只到 {last}", workers=args.workers,
                             elapsed=el, rps=_avg_rps, ok=ok, bad=bad, n501=_n501, universe=len(uni))
                sys.exit(3)
            log("没有新交易日，数据保持不变，跳过写入")
            write_runlog("noop", reason="无新交易日", workers=args.workers, elapsed=el,
                         rps=_avg_rps, ok=ok, bad=bad, n501=_n501, universe=len(uni))
            return
        else:
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
            "source": SRC,
        },
        "dates": dates,
        "total": total,
        "daycnt": daycnt,
        "willrm": willrm,
        "newadd": newadd,
        "active": active,
        "sig": packed,
    }
    # 方案1：正常成功时显式清除 stale 标记（out.meta 为全新 dict 本就不含；
    # 显式 pop 防止将来改为复用 old.meta 时残留 stale 字段）。
    for k in ("stale", "stale_since", "stale_reason"):
        out["meta"].pop(k, None)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    sz = os.path.getsize(out_path) / 1024
    log(f"已写出 {out_path}  ({sz:.0f} KB)")
    write_runlog("success", reason="", workers=args.workers, elapsed=el,
                 rps=_avg_rps, ok=ok, bad=bad, n501=_n501, universe=len(uni),
                 trade_date=dates[-1], new_days=len(new_dates) if old else len(dates))

    # v276：定时全量批次才生成/提交上证 K 线；小样本自测不覆盖线上 sse.json
    if not args.limit:
        write_sse(os.path.join(os.path.dirname(out_path), "sse.json"))


EM_SPOT = "https://push2.eastmoney.com/api/qt/stock/get"
SSE_SECID = "1.000001"          # 上证指数（沪市 1.）；写成 000001 会被判成深市平安银行

# ---- 腾讯 fqkline 备用源（2026-11-12 实测）----
# 仅当东财 push2his 返回空 / 异常时才回退；主路径成功时行为一字不改。
# 实测：param=sh000001,day,,,<count>,qfq；count>=2400 起 data 返回空（[] 坑），
#       2000 有效、1200 最稳；起止日期字段被服务端忽略，取不到 1990 起全量；
#       qfq/hfq 对指数等价（指数不复权），不带复权参数时 data 为空；
#       day[i]=[日期,开,收,高,低,成交量]，无成交额；连续 5 次 RTT 71~79ms、
#       5/5 成功、Access-Control-Allow-Origin=*、无 403/429。
TENCENT_FQKLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_MAX_COUNT = 1200        # 封顶：2400 起返回空，留足余量
TENCENT_TIMEOUT = 10            # 单次超时上限（秒），不重试，避免拖慢跑批
SSE_JSON = os.path.join(OUT_DIR, "sse.json")   # 已提交的全量历史底稿（checkout 后必存在）

# 本次 fetch_index_bars() 实际命中的上游，供 write_sse() 写 meta.kline_source 审计。
# 单线程跑批脚本；write_sse 用 globals().get 带默认值读取，测试 monkeypatch 场景不受影响。
_SSE_KLINE_SOURCE = "eastmoney"


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
    # 主路径：东财 push2his（成功时的解析与返回一字未改）
    global _SSE_KLINE_SOURCE
    try:
        d = B._get(B.EM_KLINE, params)
    except Exception:
        d = None
    out = []
    if d and d.get("data") and d["data"].get("klines"):
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
    if out:
        _SSE_KLINE_SOURCE = "eastmoney"
        return out

    # 东财返回空 / 解析全失败 / 请求异常 → 才回退腾讯。主路径成功绝不走到这里。
    bars = fetch_index_bars_fallback()
    if bars:
        _SSE_KLINE_SOURCE = "tencent-fqkline"
        return bars
    _SSE_KLINE_SOURCE = "eastmoney"
    return []


def fetch_index_bars_tencent(count=TENCENT_MAX_COUNT, timeout=TENCENT_TIMEOUT):
    """腾讯 fqkline 备用源：返回最近若干根上证日K（升序），失败返回 []。

    字段映射：腾讯 day[i] = [日期, 开, 收, 高, 低, 成交量(手)]
      对应东财 f51..f57 = 日期, 开, 收, 高, 低, 成交量, 成交额。
    腾讯无成交额字段，a 记 0（write_sse 只输出 o/h/l/c，不输出 v/a，不影响产物）。
    """
    count = max(1, min(int(count), TENCENT_MAX_COUNT))   # 封顶，规避 count 过大返回空
    try:
        r = B._session().get(   # v468: SESSION改为thread-local
            TENCENT_FQKLINE,
            params={"param": "sh000001,day,,,%d,qfq" % count},
            timeout=timeout,
        )
        if r.status_code != 200:
            return []
        node = ((r.json().get("data") or {}).get("sh000001") or {})
        arr = node.get("day") or node.get("qfqday") or []
        out = []
        for row in arr:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            try:
                out.append({"d": str(row[0])[:10], "o": float(row[1]), "c": float(row[2]),
                            "h": float(row[3]), "l": float(row[4]),
                            "v": float(row[5]), "a": 0.0})
            except (ValueError, TypeError):
                continue
        return out
    except Exception:
        return []


def _merge_tencent_history(tx_bars, sse_path=None):
    """腾讯近端数据 + 已提交 sse.json 全量历史 → 完整 bars（起点 1990）。

    腾讯只给最近约 1200 根，直接交给 write_sse 会触发「起点偏晚」护栏拒写。
    sse.json 随仓库提交（breadth.yml checkout 后必存在），以它作历史底稿：
    腾讯数据按日期覆盖同日根、追加新日根，保证 bars[0] 仍是 1990-12-19。
    底稿缺失/损坏时返回纯腾讯序列：起点护栏会拒写，属「失败不静默降级」。
    """
    path = sse_path or SSE_JSON
    try:
        with open(path, encoding="utf-8") as f:
            o = json.load(f)
        dates, oo, hh, ll, cc = o["dates"], o["o"], o["h"], o["l"], o["c"]
        hist = [{"d": dates[i], "o": oo[i], "c": cc[i], "h": hh[i], "l": ll[i],
                 "v": 0.0, "a": 0.0} for i in range(len(dates))]
    except Exception:
        return tx_bars
    by_date = {b["d"]: b for b in hist}
    for b in tx_bars:
        by_date[b["d"]] = b       # 覆盖同日根 / 追加新日根
    return [by_date[d] for d in sorted(by_date)]


def fetch_index_bars_fallback(count=TENCENT_MAX_COUNT, timeout=TENCENT_TIMEOUT,
                              sse_path=None):
    """东财失败时的完整回退：腾讯抓取 → 拼接已提交历史 → 返回全量 bars。"""
    tx = fetch_index_bars_tencent(count, timeout)
    if not tx:
        return []
    return _merge_tencent_history(tx, sse_path)


def fetch_spot_bar_000001():
    """收盘后用 push2 实时快照拼出「当日」上证 bar。

    为什么需要它：push2his 的日 K 常常要等几分钟到几十分钟才落当天那根（有时
    干脆当天不落，只能等次日），而 push2 快照在收盘那一刻就把 开/高/低/收/量/额
    全部定型（实测 16:11 时间戳、六项齐全）。指数日线这 6 个值属于「收盘即终值」，
    清算/交收改的是资金与持仓，不会回头改它们 —— 所以不必等"清算"。

    返回 {d,o,c,h,l,v,a,_ts,_src}；拿不到或字段非法返回 None。

    v278：f86 时间戳在盘后/跨日常与 breadth 交易日不等（清算后跳到次日凌晨、
    或行情源停在上一交易日），旧逻辑要求 sp["d"] == b_last 会让快照分支永不生效。
    放宽判断放在 write_sse() 的 spot_usable(sp, b_last)：以 breadth.json 的
    dates[-1] 为交易日，只要快照「不早于该日前 3 个自然日 + 收盘值在指数点位
    量级 + f86 与交易日同周」就允许覆盖/补当日那根，且当日那根日期一律用
    b_last（不是 sp["d"]）。这里只做基础校验，并保留 _ts/_src 供 write_sse 审计。
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
    # 指数点位护栏：收盘值不在量级内（如抓成平安银行 11.71）直接判不可用
    if not (50 < c < 20000):
        return None
    return {"d": dt.strftime("%Y-%m-%d"), "o": float(o), "c": float(c),
            "h": float(h), "l": float(l), "v": float(v or 0), "a": float(a or 0),
            "_ts": dt.strftime("%Y-%m-%d %H:%M:%S"), "_src": "spot"}


def spot_usable(sp, b_last):
    """判断 push2 收盘快照能否用来补/覆盖 breadth 交易日 b_last 那根。

    旧逻辑要求 sp["d"] == b_last 才生效，但 f86 时间戳盘后/跨日会让二者不等，
    导致快照分支永不进、to_src 永远退回 "kline"（线上实测 breadth 与 sse 差近
    5 小时、sse 永远没有 spot 字段）。v278 放宽为：
      ① 快照日期不早于 b_last 前 3 个自然日（允许盘后时间戳跨到次日）；
      ② 收盘值在 50..20000（指数点位量级，顺带挡掉抓成个股）；
      ③ 不是明显陈旧：f86 时间戳与 b_last 同一 ISO 周。
    返回 (可用?, 原因)；不可用原因供 write_sse 打明确日志，避免静默退回 kline。
    """
    if not sp:
        return False, "抓取为空 / f86 缺失 / OHLC 非法"
    sd, c, ts = sp.get("d"), sp.get("c"), sp.get("_ts")
    try:
        b_dt = datetime.strptime(b_last, "%Y-%m-%d")
        s_dt = datetime.strptime(sd, "%Y-%m-%d")
    except Exception:
        return False, f"日期无法解析（spot={sd}, b_last={b_last}）"
    if not (isinstance(c, (int, float)) and 50 < c < 20000):
        return False, f"收盘 {c} 不在指数点位量级（50..20000）"
    if s_dt < b_dt - timedelta(days=3):
        return False, f"快照日期 {sd} 早于 {b_last} 前 3 天，明显陈旧"
    try:
        ts_dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        if ts_dt.isocalendar()[:2] != b_dt.isocalendar()[:2]:
            return False, f"f86 时间戳 {ts} 与交易日 {b_last} 不同周，明显陈旧"
    except Exception:
        return False, f"f86 时间戳无法解析（{ts}）"
    return True, ""


def sse_is_valid(path, today):
    """sse.json 是否可直接复用：日期今日 + 末收盘在指数点位量级 + 必须是快照版。

    只查日期是不够的：抓成个股时（secid 0.000001 = 平安银行，末收盘 11.71）
    meta.to 同样是今日，会被误判成「已是最新」而永久跳过重写。
    v278：还必须是「收盘快照版」（meta.spot 存在 或 to_src=='kline+snapshot'）。
    线上那份「今天但是旧版 kline、无 spot」的 sse.json 因此判为无效，由幂等闸
    触发 write_sse() 用快照补齐 —— 首次补跑会多一次提交，属预期。
    """
    try:
        o = json.load(open(path, encoding="utf-8"))
    except Exception:
        return False
    m = o.get("meta") or {}
    if m.get("to") != today:
        return False
    c = o.get("c") or []
    if not (bool(c) and 50 < c[-1] < 20000):
        return False
    # 必须带收盘快照：旧版 kline（无 spot）即便是今天也要重写成 kline+snapshot
    return bool(m.get("spot")) or m.get("to_src") == "kline+snapshot"


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
    usable, why = spot_usable(sp, b_last)
    if usable:
        # 放宽后 sp["d"] 可能与 b_last 不等（f86 盘后跨日/滞后）。当日那根一律用
        # 快照的 OHLCVA，但日期对齐 breadth 交易日 b_last，保证末日护栏通过。
        bar = {k: sp[k] for k in ("o", "c", "h", "l", "v", "a")}
        bar["d"] = b_last
        if bars[-1]["d"][:10] == b_last:
            bars[-1] = bar
            log(f"当日 bar 用收盘快照覆盖（f86 {sp['_ts']}，收 {sp['c']}，"
                f"快照日 {sp['d']} → 对齐交易日 {b_last}）—— 与 insum 同批同步")
        else:
            bars.append(bar)
            log(f"日K缺 {b_last}，用收盘快照补一根（f86 {sp['_ts']}，收 {sp['c']}，"
                f"快照日 {sp['d']} → 对齐交易日 {b_last}）")
        src = "kline+snapshot"
        spot_meta = {"spot_d": sp["d"], "spot_ts": sp["_ts"], "spot_close": sp["c"]}
    else:
        # 不静默退回 kline：明确记录快照为何没生效（缺数据 / 护栏 / 跨周陈旧…）。
        # 若日K末日 != b_last，下面的「上证日K末日 != breadth 交易日」护栏会拒写。
        log(f"收盘快照不可用，to_src 保持 {src}（原因：{why}）；"
            f"若日K末日 != {b_last}，末日护栏将拒绝写入")
    dates = [b["d"][:10] for b in bars]
    last = dates[-1]
    if last != b_last:
        raise RuntimeError(f"上证日K末日 {last} != breadth 交易日 {b_last}，拒绝半新半旧提交")
    o = [round(b["o"], 2) for b in bars]
    c = [round(b["c"], 2) for b in bars]
    h = [round(b["h"], 2) for b in bars]
    l = [round(b["l"], 2) for b in bars]
    # 日K上游来源（主路径 eastmoney / 回退 tencent-fqkline），便于审计。
    # 不塞进 to_src：to_src 是「末根是否由收盘快照补齐」的既有语义，
    # sse_is_valid() 幂等闸与 test_sse_snapshot.py 都依赖它，改动会破坏护栏。
    kline_source = globals().get("_SSE_KLINE_SOURCE", "eastmoney")
    payload = {
        "meta": {
            "generated": bj_stamp(),
            "generated_tz": "Asia/Shanghai (UTC+8)",
            "source": ("tencent fqkline sh000001" if kline_source == "tencent-fqkline"
                       else "eastmoney 1.000001"),
            "to": last,
            "from": dates[0],
            "days": len(dates),
            # v277 数据来源可审计：kline = 日K接口；kline+snapshot = 末根由收盘快照补齐
            "to_src": src,
            "kline_source": kline_source,
            **({"spot": spot_meta} if spot_meta else {}),
        },
        "dates": dates, "o": o, "h": h, "l": l, "c": c,
    }
    with open(sse_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    log(f"已写出 {sse_path}  上证 {dates[0]} → {last}  ({len(dates)} 根)")


if __name__ == "__main__":
    main()
