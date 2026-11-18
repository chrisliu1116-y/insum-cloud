# -*- coding: utf-8 -*-
"""规则文档变更检测 + 快照一致性守卫（用户 2026-11-18 定调）。

设计取舍（诚实说明）：
  · 完整 .md 是「给人看的审查版」，含大量源码行号、历史迭代、函数表；
    确定性脚本提纯会带进噪音（实测 5696 token 且夹带源码吐槽），质量不如人工提纯。
  · 因此 prompt_rules.md 由人工精心提纯（约 1200 token），本脚本负责：
      1) 记录两份完整文档的 md5 基线；
      2) --check：文档变了就提醒「请复核快照」，绝不自动覆盖（防垃圾注入）；
      3) --stamp：人工复核完快照后，刷新 md5 基线（表示已同步）。
  · GHA 每次跑批前 --check；文档 push 时同步快照到 CF KV，并跑 --check 提醒复核。

用法：
  python3 tools/build_prompt_rules.py --check   # 退出码1=文档已变需复核
  python3 tools/build_prompt_rules.py --stamp   # 复核完，记录新基线
"""
import os, re, argparse, hashlib, time

# 真相源在 insum-cloud/docs（GHA 可监听）；前端项目改完后用 sync 脚本同步过来
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = [os.path.join(ROOT, 'docs/分级标准.md'), os.path.join(ROOT, 'docs/去重规则全览.md')]
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.rules_baseline.json')
SNAP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'prompt_rules.md')

def _md5(p):
    return hashlib.md5(open(p, 'rb').read()).hexdigest()

def _cur():
    return {os.path.basename(p): _md5(p) for p in DOCS}

def _est_tokens(s):
    zh = sum('\u4e00' <= c <= '\u9fff' for c in s)
    return int(zh / 1.5 + (len(s) - zh) / 2)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--stamp', action='store_true')
    a = ap.parse_args()
    cur = _cur()
    old = {}
    if os.path.exists(BASE):
        import json
        old = json.load(open(BASE, encoding='utf-8'))
    changed = [k for k, v in cur.items() if old.get(k) != v]
    snap_tokens = _est_tokens(open(SNAP, encoding='utf-8').read()) if os.path.exists(SNAP) else -1

    if a.stamp:
        import json
        json.dump(cur, open(BASE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        print('[stamp] 已记录文档基线 %s（快照约%d token）' % (cur, snap_tokens))
        return 0
    # 默认/check
    if changed:
        print('[check] ⚠️ 规则文档已变更: %s' % ', '.join(changed))
        print('[check] 请人工复核 prompt_rules.md 判据是否需同步，然后运行 --stamp')
        return 1
    print('[check] ✅ 规则文档与快照基线一致（快照约%d token）' % snap_tokens)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
