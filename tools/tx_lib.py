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
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIVERSE_FILE = os.path.join(ROOT, "tools", "tx_universe.json")
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://gu.qq.com/",
}
SESSION = requests.Session()
SESSION.headers.update(UA)
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
        try:
            r = SESSION.get(KLINE, params={"param": f"{tc},day,,,{n},qfq"}, timeout=20)
            if r.status_code == 501:
                time.sleep(3 + k * 3)
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
            time.sleep(1.0 * (k + 1))
        finally:
            _sleep()
    return []
