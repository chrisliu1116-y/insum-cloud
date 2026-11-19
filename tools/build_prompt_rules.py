# -*- coding: utf-8 -*-
"""提示词快照守卫（v2.0 最终版 · 2026-11-19 用户拍板）。

架构定调：
  · prompt_rules.md = 云端 LLM 分级的**唯一真相源**（人工提纯的完整 v2.0 System Prompt）。
  · 本脚本**绝不**从 docs 重新生成/覆盖它——历史版本的"提纯生成"会带进源码吐槽与
    去重段落(is_dup/dup_of)，造成 prompt 回退，v2.0 起彻底废除该路径。
  · 只做两件事：--check 校验快照仍是 v2.0（防被旧文件覆盖）；--stamp 记录基线。

用法：
  python3 tools/build_prompt_rules.py --check   # 退出码1=快照非v2.0或被篡改
  python3 tools/build_prompt_rules.py --stamp   # 记录当前快照基线
"""
import os, argparse, hashlib, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(ROOT, 'prompt_rules.md')
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.rules_baseline.json')
# v2.0 快照必须出现的指纹（缺任一即判定回退/被篡改）
V2_MARKERS = ['多资产宏观交易风控分级引擎', 'S1（宏观与货币核弹）', 'S4（系统性金融信用危机）',
              's_code', 'title_prefix', '分布纪律（防通胀）', 'GTA VI']
# 已废弃的去重段落——出现即视为回退
FORBIDDEN = ['is_dup', 'dup_of', '去重规则快照']

def _md5(p):
    return hashlib.md5(open(p, 'rb').read()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--stamp', action='store_true')
    a = ap.parse_args()
    if not os.path.exists(SNAP):
        print('[check] ✗ prompt_rules.md 不存在'); return 1
    txt = open(SNAP, encoding='utf-8').read()
    missing = [m for m in V2_MARKERS if m not in txt]
    bad = [m for m in FORBIDDEN if m in txt]
    if a.stamp:
        json.dump({'md5': _md5(SNAP), 'version': 'v2.0'}, open(BASE, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        print('[stamp] 已记录 v2.0 快照基线 md5=%s' % _md5(SNAP)[:12])
        return 0 if not missing and not bad else 1
    # 默认 check
    if missing or bad:
        print('[check] ✗ 快照非 v2.0 或被回退！缺失标记=%s 违禁段落=%s' % (missing, bad))
        return 1
    print('[check] ✅ prompt_rules.md 为 v2.0 完整版（%d字, md5=%s）' % (len(txt), _md5(SNAP)[:12]))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
