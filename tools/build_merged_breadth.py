# -*- coding: utf-8 -*-
"""一次性：把东财长历史前缀 + 腾讯近期拼成单个 breadth.json（2026-11-21）

切分依据：从腾讯最早交易日 2013-09-09 往后扫，第一个 4 值(total/daycnt/willrm/newadd)
完全相等日 = 2013-12-24。故：
  - 东财段：1990-12-19 ~ 2013-12-23（5686 日，含北交所、23年历史）
  - 腾讯段：2013-12-24 ~ 2026-11-20（3138 日，当前线上数据）
  - 合并 8824 日，无重复无缺口

分段信息写入 meta.segments；meta.source 保持 tencent —— 使 breadth_run.py 的
v293/v294 防混拼护栏放行未来腾讯增量（只追加到尾部，历史段不动）。

用法：
  python tools/build_merged_breadth.py --dry-run     # 只检查
  python tools/build_merged_breadth.py               # 写出 breadth/data/breadth.json
"""
import os, sys, json, argparse, shutil, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'breadth', 'data')
EM = os.path.join(DATA, '_archive_breadth_em_20261114.json')
TX = os.path.join(DATA, '_archive_breadth_tx_20261114.json')
NOW = os.path.join(DATA, 'breadth.json')
OUT = NOW
FIELDS = ['total', 'daycnt', 'willrm', 'newadd', 'active', 'sig']
CUT = '2013-12-24'          # 第一个 4 值全等日（含）起用腾讯
EM_KEY = 'eastmoney'
TX_KEY = 'tencent'


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    em = json.load(open(EM, encoding='utf-8'))
    tx = json.load(open(TX, encoding='utf-8'))
    now = json.load(open(NOW, encoding='utf-8'))
    assert CUT in em['dates'] and CUT in tx['dates'] and CUT in now['dates'], '切分日缺失'

    # 1) 校验切分日当天两边 4 值 + sig 完全相等（用户判据）
    ie = em['dates'].index(CUT); it = tx['dates'].index(CUT)
    for f in ['total', 'daycnt', 'willrm', 'newadd', 'sig']:
        assert em[f][ie] == tx[f][it], f'切分日 {f} 不相等: {em[f][ie]} vs {tx[f][it]}'
    print(f'[OK] 切分日 {CUT} 两边 4 值 + sig 完全相等')

    # 2) 取东财前缀（< CUT）+ 腾讯全量（NOW，>= CUT，含最新 11-20）
    epre = [i for i, d in enumerate(em['dates']) if d < CUT]
    assert epre and epre[-1] + 1 == ie, '东财前缀不连续'
    ntail = [i for i, d in enumerate(now['dates']) if d >= CUT]
    assert ntail and ntail[0] == now['dates'].index(CUT), '腾讯段起点应为切分日在 NOW 的索引'

    merged = {'meta': None}
    merged['dates'] = [em['dates'][i] for i in epre] + [now['dates'][i] for i in ntail]
    for f in FIELDS:
        merged[f] = [em[f][i] for i in epre] + [now[f][i] for i in ntail]
    assert len(merged['dates']) == len(set(merged['dates'])), '合并后有重复日期'
    assert all(merged[f] and len(merged[f]) == len(merged['dates']) for f in FIELDS), '字段长度不齐'

    # 3) meta：source 保持 tencent 过护栏；segments 记录分段
    nm = dict(now['meta'])
    nm.update({
        'generated': now['meta'].get('generated'),
        'trade_date': now['meta'].get('trade_date'),
        'days': len(merged['dates']),
        'from': merged['dates'][0],
        'to': merged['dates'][-1],
        'source': 'tencent',   # 增量源（护栏用）；历史段见 segments
        'merged': True,
        'merge_cut': CUT,
        'segments': [
            {'source': EM_KEY, 'from': merged['dates'][0], 'to': merged['dates'][len(epre) - 1],
             'days': len(epre), 'note': '东财长历史(含北交所)，4值全等切分点之前'},
            {'source': TX_KEY, 'from': CUT, 'to': merged['dates'][-1],
             'days': len(ntail), 'note': '腾讯近期(当前线上)，含增量写入'},
        ],
        'spec': nm.get('spec', '') + ' / v490分段拼接(1990~2013-12-23东财 + 2013-12-24起腾讯)',
    })
    merged['meta'] = nm

    print(f'合并 {len(merged["dates"])} 日 {merged["dates"][0]} ~ {merged["dates"][-1]}')
    print(f'  东财段 {len(epre)} 日  腾讯段 {len(ntail)} 日')
    print(f'  接缝 {merged["dates"][len(epre)-1]} -> {CUT}: '
          f'total {merged["total"][len(epre)-1]}->{merged["total"][len(epre)]}, '
          f'sig {merged["sig"][len(epre)-1]}->{merged["sig"][len(epre)]}')

    if a.dry_run:
        print('[DRY-RUN] 未写文件')
        return

    # 4) 封存原两源跑批文件（先备份，原子覆盖）
    bakdir = os.path.join(DATA, '_sealed_20261121')
    os.makedirs(bakdir, exist_ok=True)
    for src in (EM, TX, NOW):
        dst = os.path.join(bakdir, os.path.basename(src))
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
            print(f'[封存] {os.path.basename(src)} -> _sealed_20261121/ (md5 {md5(src)[:10]})')
        else:
            print(f'[封存] {os.path.basename(src)} 已存在，保留原封存版本不覆盖')

    # 5) 原子写出
    tmp = OUT + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, OUT)
    print(f'[写出] {OUT}  ({os.path.getsize(OUT)/1024:.0f} KB, md5 {md5(OUT)[:10]})')


if __name__ == '__main__':
    main()
