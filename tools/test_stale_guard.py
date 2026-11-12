# -*- coding: utf-8 -*-
"""test_stale_guard.py — 方案1（失败不静默停更 / 明确标注数据日期）可复跑自测。

跑法：python tools/test_stale_guard.py
不联网：monkeypatch breadth_run 的 B.fetch_universe / worker / write_sse /
        save_state / load_state / FLUSH_EVERY 与 sys.argv。
不写线上数据：全部在 tempfile 临时目录里跑，breadth.json 用构造的假数据，
        绝不碰 breadth/data/breadth.json 的历史数据。

覆盖：
  ① stale_reason_of 判据（正常 / 股票池缩水 / ok=0 日K全挂 / agg 空）
  ② mark_stale：数据序列逐值不变、stale 字段正确、stale_since 不刷新、
        无既有文件 / 数据序列缺失时拒标（不写假数据）
  ③ main() 东财全挂：不覆盖数据序列，仅标注 meta.stale，保留上次成功日
  ④ main() 恢复：正常成功时 stale 三字段被清除，trade_date == dates[-1]
"""
import copy
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import breadth_run as R  # noqa: E402

PASS = FAIL = 0
SEQS = ("dates", "total", "daycnt", "willrm", "newadd", "active", "sig")


def ok(cond, name, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  OK   ' + name + (('  | ' + str(extra)) if extra != '' else ''))
    else:
        FAIL += 1
        print('  FAIL ' + name + (('  | ' + str(extra)) if extra != '' else ''))


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def seqs(d):
    """取 7 条数据序列的深拷贝，用于断言「不写坏文件」。"""
    return {k: copy.deepcopy(d.get(k)) for k in SEQS}


def make_breadth(path):
    """构造一份「上次成功」的 breadth.json（临时文件）。

    meta.valid=0 是为了让 main() 的幂等闸（valid>=5000 且 to==今天）一定不跳过，
    保证测试在任何日期都可复跑；其余字段与线上同构。"""
    data = {
        "meta": {
            "generated": "2026-11-11 15:02:57",
            "generated_tz": "Asia/Shanghai (UTC+8)",
            "run_date": "2026-11-11",
            "trade_date": "2026-11-11",
            "universe": 5584, "valid": 0, "days": 5,
            "from": "2026-11-05", "to": "2026-11-11", "lmt": 750,
            "spec": "全部A股(含北交所) / fqt=1 / RSI9-WR10-WR20 / 总市值>100亿",
        },
        "dates": ["2026-11-05", "2026-11-06", "2026-11-07", "2026-11-10", "2026-11-11"],
        "total": [100, 101, 102, 103, 113],
        "daycnt": [40, 50, 60, 70, 48],
        "willrm": [1, 2, 3, 4, 11],
        "newadd": [1, 2, 3, 4, 5],
        "active": [5500, 5510, 5520, 5530, 5574],
        "sig": [0, 1, 2, 4, 8],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return copy.deepcopy(data)


def run_main(out_path, uni_n, worker_ok):
    """monkeypatch 跑一次完整 main()，返回异常字符串（空串=正常结束）。

    uni_n 控制股票池规模（模拟 clist/get 是否拿到全量）；
    worker_ok=False 时每只股票都返回 None（模拟 push2his 日K 全挂）。"""
    orig = {
        "argv": sys.argv,
        "uni": R.B.fetch_universe,
        "worker": R.worker,
        "sse": R.write_sse,
        "save": R.save_state,
        "load": R.load_state,
        "flush": R.FLUSH_EVERY,
    }
    try:
        # 贴近线上 breadth.yml 默认增量模式
        sys.argv = ["breadth_run.py", "--no-wait", "--incremental", "--out", out_path]
        R.B.fetch_universe = lambda verbose=False: [
            {"code": str(i), "name": "x", "price": 10.0, "mktcap": 1e10}
            for i in range(uni_n)
        ]
        if worker_ok:
            # 必须给「晚于既有末日 2026-11-11」的新交易日：只有出现新交易日，
            # 增量合并才会真正重写 out（真实恢复场景 = 停更后抓到新一天才前进）。
            # 值用 0/1 布尔计数（贴近真实单股输出），避免 5001 只累加出超大整数。
            inc = {"2026-11-12": [0, 1, 0, 1, 1], "2026-11-13": [1, 1, 0, 0, 1]}
            # 每次返回全新的 dict + list：真实 worker 每只股票都新建 inc，
            # 若这里共享可变 list，聚合器会自累加出指数级大整数。
            R.worker = lambda u: (u["code"], {d: list(v) for d, v in inc.items()})
        else:
            R.worker = lambda u: (u["code"], None)
        R.write_sse = lambda *a, **k: None       # 不联网、不写 sse.json
        R.save_state = lambda *a, **k: None      # 不写真实 state.json
        R.load_state = lambda: None              # 不用真实断点续跑缓存
        R.FLUSH_EVERY = 100000                  # 抑制进度日志，保持输出干净
        err = ''
        try:
            R.main()
        except Exception as e:
            err = repr(e)
        return err
    finally:
        sys.argv = orig["argv"]
        R.B.fetch_universe = orig["uni"]
        R.worker = orig["worker"]
        R.write_sse = orig["sse"]
        R.save_state = orig["save"]
        R.load_state = orig["load"]
        R.FLUSH_EVERY = orig["flush"]


print('===== ① stale_reason_of 判据 =====')
ok(R.stale_reason_of([{}] * 6000, 5584, {"d": [1, 2, 3, 4, 5]}) == '',
   '① 正常（股票池充足 + ok 充足 + agg 非空）不触发')
ok('股票池缩水' in R.stale_reason_of([{}] * 100, 5584, {"d": [1, 2, 3, 4, 5]}),
   '① 股票池缩水 100 只触发')
ok('push2his' in R.stale_reason_of([{}] * 5584, 0, {"d": [1, 2, 3, 4, 5]}),
   '① ok=0（日K全挂）触发')
ok('聚合结果为空' in R.stale_reason_of([{}] * 5584, 5584, {}),
   '① agg 空触发')

print('===== ② mark_stale：保留数据、字段正确、不刷新起始 =====')
with tempfile.TemporaryDirectory() as tmp:
    p = os.path.join(tmp, "breadth.json")
    before = make_breadth(p)
    before_seqs = seqs(before)
    ok(R.mark_stale(p, "东财全挂模拟") is True, '② mark_stale 返回 True')
    d = load(p)
    ok(seqs(d) == before_seqs, '② 数据序列 7 项逐值不变（不写坏文件）')
    m = d["meta"]
    ok(m.get("stale") is True, '② meta.stale=True', m.get("stale"))
    ok(m.get("trade_date") == "2026-11-11", '② trade_date 保留上次成功日', m.get("trade_date"))
    ok(m.get("generated") == "2026-11-11 15:02:57", '② generated 保留上次成功（不假装今天跑过）')
    ss = m.get("stale_since", "")
    ok("+08:00" in ss and "T" in ss, '② stale_since 为北京时间 ISO(+08:00)', ss)
    ok(bool(m.get("stale_reason")), '② stale_reason 非空', m.get("stale_reason"))
    time.sleep(0.01)
    R.mark_stale(p, "再次失败")
    d2 = load(p)
    ok(d2["meta"]["stale_since"] == ss, '② 重复失败不刷新 stale_since（停留起点稳定）')
    ok(d2["meta"]["stale_reason"] == "再次失败", '② 重复失败更新 stale_reason')
    ok(R.mark_stale(os.path.join(tmp, "nope.json"), "x") is False,
       '② 无既有文件返回 False（不写假数据）')
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"meta": {}}, f)
    ok(R.mark_stale(bad, "x") is False, '② 数据序列缺失返回 False（不写坏文件）')

print('===== ③ main() 东财全挂：不覆盖数据，仅标注 meta.stale =====')
with tempfile.TemporaryDirectory() as tmp:
    p = os.path.join(tmp, "breadth.json")
    before = make_breadth(p)
    before_seqs = seqs(before)
    err = run_main(p, uni_n=10, worker_ok=False)   # 股票池缩水 + 日K全挂
    ok(err == '', '③ main() 未抛异常', err)
    d = load(p)
    ok(seqs(d) == before_seqs, '③ 数据序列 7 项逐值不变（不写假数据覆盖）')
    ok(d["meta"].get("stale") is True, '③ meta.stale=True')
    ok(d["meta"].get("trade_date") == "2026-11-11", '③ trade_date 保留上次成功日',
       d["meta"].get("trade_date"))
    ok(bool(d["meta"].get("stale_reason")), '③ stale_reason 记录了失败原因',
       d["meta"].get("stale_reason"))

print('===== ④ main() 恢复：stale 被清除 =====')
with tempfile.TemporaryDirectory() as tmp:
    p = os.path.join(tmp, "breadth.json")
    make_breadth(p)
    R.mark_stale(p, "模拟上次停更")
    ok(load(p)["meta"].get("stale") is True, '④ 前置：文件已处于 stale 状态')
    err = run_main(p, uni_n=5001, worker_ok=True)  # 股票池恢复 + 日K恢复
    ok(err == '', '④ main() 未抛异常', err)
    d = load(p)
    m = d["meta"]
    ok(not any(k in m for k in ("stale", "stale_since", "stale_reason")),
       '④ 成功跑批后 stale 三字段全被清除')
    ok(m.get("trade_date") == d["dates"][-1] == "2026-11-13",
       '④ trade_date == dates[-1]（新交易日）', m.get("trade_date"))
    ok(len(d["dates"]) == 7 and m.get("valid") == 5001,
       '④ 数据序列已正常重写（5 旧 + 2 新 / valid=5001）',
       'dates=%s valid=%s' % (len(d["dates"]), m.get("valid")))

print('\n结果：%d 通过 / %d 失败' % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
