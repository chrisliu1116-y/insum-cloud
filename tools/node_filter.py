# -*- coding: utf-8 -*-
"""亚太节点过滤规则（集中定义，供 start_proxy.sh / proxy-live.yml / sub_to_clash.py 共用）

包含规则：香港/HK、台湾/TW、新加坡/SG、日本/JP、韩国/KR（及英文全称）
排除规则：机场常把「过期/剩余/流量/官网/重置」等信息提示塞进节点列表当伪节点，
         这些不是真代理节点，必须剔除，否则会被选进 load-balance 池导致拨号失败。
"""
import re

# 亚太地区命中正则（大小写不敏感）——用户 2026-11-18 定稿完整版
# 含中文/英文缩写/机场三字码/国旗emoji
INCLUDE_RE = re.compile(
    r'(香港|HK|HKG|🇭🇰|台湾|台北|新北|TW|TPE|🇹🇼|'
    r'新加坡|狮城|SG|SIN|🇸🇬|'
    r'日本|东京|大阪|JP|NRT|KIX|🇯🇵|'
    r'韩国|首尔|KR|ICN|🇰🇷)', re.I)

# 信息提示类伪节点排除正则
EXCLUDE_RE = re.compile(r'(过期|剩余|流量|官网|重置|到期|续费|余额|套餐|购买|订阅)', re.I)


def is_apac(name):
    """True = 有效亚太节点；False = 非亚太 或 信息提示类伪节点。"""
    if not name:
        return False
    if EXCLUDE_RE.search(name):
        return False
    return bool(INCLUDE_RE.search(name))


def pick_names(proxies, min_fallback=True):
    """从 proxies 列表选出亚太节点名；不足时回退全部（排除伪节点）。"""
    valid = [p for p in proxies if p.get('name') and not EXCLUDE_RE.search(p['name'])]
    picked = [p['name'] for p in valid if INCLUDE_RE.search(p['name'])]
    if len(picked) < 3 and min_fallback:
        picked = [p['name'] for p in valid]
    return picked, len(valid)
