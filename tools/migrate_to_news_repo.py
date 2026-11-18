# -*- coding: utf-8 -*-
"""一键迁移新闻/事件卡相关文件到独立仓库 macropulse-news。

前置（需用户操作，AI 的 PAT 无建仓权限）：
  1) 在 GitHub 新建私有仓库 macropulse-news（空仓库，带 README）
  2) 给 AI 的 PAT 加上该仓库权限，或把本脚本产出的目录手动 push

本脚本做：把散在 insum-cloud 里的新闻/事件卡文件，按分类整理到目标目录。
  规则/     → 分级标准.md、去重规则全览.md、prompt_rules.md
  流水线/   → llm_pool / llm_news_pipeline / event_incubator / evolve_events /
              polymarket_gate / build_prompt_rules / sync_rules_to_*
  数据/     → events_daily.json、events_news_today.json
  .github/workflows/ → evolve-events.yml、sync-rules.yml

用法：
  python3 tools/migrate_to_news_repo.py --target E:/WorkBuddy工作区/macropulse-news --dry-run
  python3 tools/migrate_to_news_repo.py --target E:/WorkBuddy工作区/macropulse-news
"""
import os, shutil, argparse

GHA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONT = 'E:/WorkBuddy工作区/2026-11-03-23-29-44'

MAP = {
    '规则': [
        (GHA + '/docs/分级标准.md', 'docs/分级标准.md'),
        (GHA + '/docs/去重规则全览.md', 'docs/去重规则全览.md'),
        (GHA + '/prompt_rules.md', 'prompt_rules.md'),
        (GHA + '/tools/.rules_baseline.json', 'tools/.rules_baseline.json'),
    ],
    '流水线': [
        (GHA + '/tools/llm_pool.py', 'tools/llm_pool.py'),
        (GHA + '/tools/llm_news_pipeline.py', 'tools/llm_news_pipeline.py'),
        (GHA + '/tools/event_incubator.py', 'tools/event_incubator.py'),
        (GHA + '/tools/evolve_events.py', 'tools/evolve_events.py'),
        (GHA + '/tools/polymarket_gate.py', 'tools/polymarket_gate.py'),
        (GHA + '/tools/build_prompt_rules.py', 'tools/build_prompt_rules.py'),
        (GHA + '/tools/sync_rules_to_cfkv.py', 'tools/sync_rules_to_cfkv.py'),
        (GHA + '/tools/sync_rules_to_gha.py', 'tools/sync_rules_to_gha.py'),
    ],
    '数据': [
        (GHA + '/data/events_daily.json', 'data/events_daily.json'),
        (GHA + '/data/events_news_today.json', 'data/events_news_today.json'),
    ],
    '自动化': [
        (GHA + '/.github/workflows/evolve-events.yml', '.github/workflows/evolve-events.yml'),
        (GHA + '/.github/workflows/sync-rules.yml', '.github/workflows/sync-rules.yml'),
    ],
    'cf-worker（前端项目）': [
        (FRONT + '/tools/cf-worker/worker.js', 'tools/cf-worker/worker.js'),
        (FRONT + '/tools/cf-worker/deploy_api.py', 'tools/cf-worker/deploy_api.py'),
    ],
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', required=True)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    total = 0
    for cat, files in MAP.items():
        print('\n[%s]' % cat)
        for src, rel in files:
            if not os.path.exists(src):
                print('  SKIP 源不存在:', os.path.basename(src)); continue
            dst = os.path.join(a.target, rel)
            print('  %s → %s' % (os.path.basename(src), rel))
            total += 1
            if not a.dry_run:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
    print('\n共 %d 个文件。%s' % (total, '（dry-run 未复制）' if a.dry_run else '已复制到 ' + a.target))
    if not a.dry_run:
        print('提示：目标目录需 git init + 配 remote + 补 requirements.txt/README 后 push')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
