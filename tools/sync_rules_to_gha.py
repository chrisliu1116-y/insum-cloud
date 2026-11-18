# -*- coding: utf-8 -*-
"""把用户在前端项目改的规则文档同步到 insum-cloud（真相源），并提交推送触发 GHA。

用户习惯改 E:/WorkBuddy工作区/2026-11-03-23-29-44/docs/ 下的完整文档，
但 GHA 在 insum-cloud 仓库。本脚本桥接两者，一条命令完成：
  1) 复制两份完整文档到 insum-cloud/docs/
  2) 跑变更检测（--check）：若判据变了，提醒先复核 prompt_rules.md
  3) git add + commit + push → 触发 GHA 自动同步快照到 CF KV

用法：
  python3 tools/sync_rules_to_gha.py            # 同步+提交+推送
  python3 tools/sync_rules_to_gha.py --dry-run  # 只看差异
"""
import os, sys, shutil, subprocess, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
GHA = os.path.dirname(HERE)
SRC = 'E:/WorkBuddy工作区/2026-11-03-23-29-44/docs'
DOCS = ['分级标准.md', '去重规则全览.md']

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    for name in DOCS:
        s, d = os.path.join(SRC, name), os.path.join(GHA, 'docs', name)
        if not os.path.exists(s):
            print('[skip] 源不存在', s); continue
        same = os.path.exists(d) and open(s, 'rb').read() == open(d, 'rb').read()
        print('%-16s %s' % (name, '无变化' if same else '有更新 → 将同步'))
        if not same and not a.dry_run:
            shutil.copyfile(s, d)
    if a.dry_run:
        print('[dry-run] 未改动'); return 0
    # 变更检测：文档改了会返回1，提醒复核快照（不阻断同步，但明确告知）
    r = subprocess.run([sys.executable, os.path.join(HERE, 'build_prompt_rules.py'), '--check'],
                       capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode == 1:
        print('[提醒] 判据有变，请确认 prompt_rules.md 已同步最新判据后再发版。')
    # commit + push
    files = ['docs/' + n for n in DOCS]
    subprocess.run(['git', '-C', GHA, 'add'] + files, check=True)
    st = subprocess.run(['git', '-C', GHA, 'status', '--porcelain'] + files,
                        capture_output=True, text=True).stdout.strip()
    if not st:
        print('无文档变更，跳过提交'); return 0
    msg = 'docs: 同步规则文档（分级/去重）→ 触发 GHA 规则快照同步'
    subprocess.run(['git', '-C', GHA, 'commit', '-m', msg], check=True)
    subprocess.run(['git', '-C', GHA, 'push', 'origin', 'main'], check=True)
    print('[done] 已推送，GHA 将自动同步快照到 CF KV')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
