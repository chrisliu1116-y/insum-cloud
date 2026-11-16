# -*- coding: utf-8 -*-
"""腾讯数据源（breadth 跑批用）

与 breadth_lib 保持同一接口：
  fetch_universe(verbose) -> [{"code","name","price","mktcap"}]
  fetch_kline(code, lmt, fqt=1, beg=None) -> [{"d","o","c","h","l","v"}]

设计要点（2026-11-14 实测）：
  · 腾讯 fqkline 单次上限约 1200 根（≈4.7 年），拿不到 90 年代；本跑批 LMT=750 够用。
  · 全市场名录来自 tools/tx_universe.json（由本地名录生成，5785 只）；
    总市值用 qt.gtimg.cn 批量报价字段 45（亿元）。
  · 并发必须低：实测 150 req/s 会触发 501 反爬；默认 6 并发 + 60~120ms 抖动。
  · 北交所 920xxx 只回当日 1 根，属于源限制，交由上层按数据不足跳过。
"""
import json
import os
import random
import re
import threading
import time

# v361c（用户 2026-11-15）：Chrome TLS 指纹伪装**只限东财**。
# 腾讯源恢复 v354 之前的普通 requests：腾讯 501 是并发/频率反爬，不是 TLS 指纹，
# 按「腾讯之前的配置跑法」跑即可（<=6 并发 + 60~120ms 抖动，见 fetch_kline）。
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIVERSE_FILE = os.path.join(ROOT, "tools", "tx_universe.json")
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
}
SESSION = requests.Session()
SESSION.headers.update(UA)

# ===================================================================
# v386b（2026-11-16）全局令牌桶限速：把 kline 请求速率**硬钉在 <= BREADTH_RATE
# req/s**，与并发数/单请求耗时解耦。用户口径「卡 140 上限、尽可能快」：
#   11-14 本机 5784/38s ≈ 152 req/s 触发 501 → 140 是留 ~7% 余量的上限。
# 令牌桶保证任意滑动窗口内请求数不超上限；并发只负责「喂饱桶」，不再是风险源。
# 501 自适应：每出现一次 501，速率 ×0.8（下限 60），把「被限流」转成自动降速；
# 主流程 watchdog 计数达阈值即急停（见 breadth_run.py）。
# ===================================================================
RATE = max(10.0, float(os.environ.get("BREADTH_RATE", "140")))
_STOP = threading.Event()          # 急停信号：watchdog 置位后 acquire() 立即返回 False


class _TokenBucket:
    def __init__(self, rate):
        self.lock = threading.Lock()
        self.rate = float(rate)
        self.min_rate = 60.0
        self.capacity = max(10.0, float(rate) / 2)   # 0.5s 突发容量，避免首帧脉冲
        self.tokens = self.capacity
        self.last = time.monotonic()
        self.total = 0             # 累计 acquire 成功数（含重试，= 真实发出的请求数）
        self.resp501 = 0           # 累计 501 次数

    def acquire(self):
        """阻塞直到拿到 1 个令牌；返回 False 表示已急停（不再发请求）。"""
        while True:
            if _STOP.is_set():
                return False
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity,
                                  self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    self.total += 1
                    return True
                need = (1.0 - self.tokens) / self.rate
            time.sleep(min(need, 0.05))   # 锁外睡，避免占锁

    def note501(self):
        with self.lock:
            self.resp501 += 1
            old = self.rate
            self.rate = max(self.min_rate, self.rate * 0.8)
            print(f"[tx][501] 反爬 {self.resp501} 次，速率 {old:.0f}→{self.rate:.0f} req/s",
                  flush=True)

    def stats(self):
        with self.lock:
            return self.total, self.resp501, self.rate

    def reset_stop(self):
        _STOP.clear()


BUCKET = _TokenBucket(RATE)


def request_stop():
    _STOP.set()


def is_stopped():
    return _STOP.is_set()

# ===================================================================
# v361d【腾讯专属配置】按「腾讯之前的配置跑法」：
#   · 无探针（PROBE_CHAIN=[]）—— 之前的腾讯跑批没有健康检查这一步；
#   · 无熔断（CIRCUIT enabled=False）—— 名录里有 19 个历史无效代码
#     （810011/492802/023403 等，台账 8032）永远取不到，东财那套
#     「连续 30 只失败即停」会误伤；
#   · kline 3 次重试带 501 退避（fetch_kline 内置）、<=6 并发 +
#     60~120ms 抖动、普通 requests 不伪装（Chrome TLS 指纹只限东财）。
# 东财在 breadth_lib.py 里改任何跑法都只动那份文件，与这里互不影响。
# ===================================================================
PROBE_CHAIN = []
CIRCUIT = {'enabled': False, 'max_consec_bad': 0}
KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
QUOTE = "https://qt.gtimg.cn/q="


def tx_code(code):
    c = str(code)
    if c.startswith("6"):
        return "sh" + c
    if c.startswith(("0", "3")):
        return "sz" + c
    if c.startswith(("4", "8", "92")):
        return "bj" + c
    return None


def _sleep():
    time.sleep(random.uniform(0.05, 0.12))


def fetch_universe(verbose=False):
    raw = json.load(open(UNIVERSE_FILE, encoding="utf-8"))
    items = [{"code": c, "name": n, "tx": tx_code(c)} for c, n in raw.items()]
    items = [x for x in items if x["tx"]]
    # 总市值：qt 批量报价，60 只/次
    quotes = {}
    for i in range(0, len(items), 60):
        chunk = items[i:i + 60]
        q = ",".join(x["tx"] for x in chunk)
        for k in range(3):
            try:
                r = SESSION.get(QUOTE + q, timeout=12)
                r.encoding = "gbk"
                for line in r.text.split(";"):
                    if "=" not in line:
                        continue
                    _, val = line.split("=", 1)
                    fs = val.strip().strip('"').split("~")
                    if len(fs) < 46:
                        continue
                    code = str(fs[2])
                    if not re.fullmatch(r"\d{6}", code):
                        continue
                    try:
                        price = float(fs[3] or 0)
                        cap = float(fs[45] or 0)
                    except ValueError:
                        continue
                    if price > 0:
                        quotes[code] = (price, cap)
                break
            except Exception:
                time.sleep(0.5 * (k + 1))
        _sleep()
    out = []
    for x in items:
        price, cap = quotes.get(x["code"], (0, 0))
        if cap <= 0:
            continue
        out.append({"code": x["code"], "name": x["name"], "price": price, "mktcap": cap})
    if verbose:
        print(f"[tx] universe {len(out)}/{len(items)}（含总市值）", flush=True)
    return out


def fetch_kline(code, lmt=750, fqt=1, beg=None):
    tc = tx_code(code)
    if not tc:
        return []
    n = max(30, min(1200, int(lmt or 750)))
    for k in range(3):
        # v386b：全局令牌桶统一控速，取代旧的每 worker 固定 sleep（0.12~0.18 +
        # 0.05~0.12）——那两个固定 sleep 让 20 并发实际只有 ~50 req/s，喂不饱 140 的桶。
        # 急停（_STOP）时 acquire 立即返回 False，worker 不再发任何请求。
        if not BUCKET.acquire():
            return []
        try:
            r = SESSION.get(KLINE, params={"param": f"{tc},day,,,{n},qfq"}, timeout=20)
            if r.status_code == 501:
                BUCKET.note501()          # 计数 + 自适应降速 ×0.8
                time.sleep(3 + k * 3)     # 501 退避保留（遇限流时拉长间隔）
                continue
            d = r.json()
            box = (d.get("data") or {}).get(tc) or {}
            arr = box.get("qfqday") or box.get("day") or []
            bars = []
            for row in arr:
                if len(row) < 6:
                    continue
                try:
                    bars.append({"d": str(row[0])[:10], "o": float(row[1]), "c": float(row[2]),
                                 "h": float(row[3]), "l": float(row[4]), "v": float(row[5] or 0)})
                except Exception:
                    continue
            if bars:
                return bars
        except Exception:
            time.sleep(0.5 * (k + 1))
    return []
