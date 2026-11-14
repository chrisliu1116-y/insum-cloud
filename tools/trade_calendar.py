# -*- coding: utf-8 -*-
"""A股交易日判断（工作日 − 法定节假日）。

口径（与用户逐条对齐）：
  · A股只在**周一至周五**开市；调休上班的周末**不开市**
    （如 2026-01-04 周日、02-14 周六、02-28 周六、05-09 周六、09-20 周日、10-10 周六）；
  · 法定节假日休市，日期取自《国务院办公厅关于2026年部分节假日安排的通知》
    （2025-11-04 发布，gov.cn）；
  · 临时休市（台风、重大事件等）无法预知，由「K线最后一根」兜底判断。

用途：跑批用它回答「今天该不该有数据」，避免把「数据没到」误判成
「不是交易日」而静默跳过 —— 这正是 11-13 之外那类「success 但没数据」的成因。
"""
import datetime

# (起, 止) 含首尾。只维护到已知年份；未维护的年份按工作日处理并告警。
HOLIDAYS = {
    "2026": [
        ("2026-01-01", "2026-01-03"),   # 元旦（1/4 周日上班，但A股不开市）
        ("2026-02-15", "2026-02-23"),   # 春节 9 天
        ("2026-04-04", "2026-04-06"),   # 清明节
        ("2026-05-01", "2026-05-05"),   # 劳动节
        ("2026-06-19", "2026-06-21"),   # 端午节
        ("2026-09-25", "2026-09-27"),   # 中秋节
        ("2026-10-01", "2026-10-07"),   # 国庆节
    ],
}


def _to_date(d):
    if isinstance(d, datetime.datetime):
        return d.date()
    if isinstance(d, datetime.date):
        return d
    return datetime.date.fromisoformat(str(d)[:10])


def is_trade_day(d=None):
    """是否 A股交易日。周末一律 False（调休上班的周末也不开市）。"""
    d = _to_date(d or datetime.date.today())
    if d.weekday() >= 5:
        return False
    for a, b in HOLIDAYS.get(str(d.year), []):
        if a <= d.isoformat() <= b:
            return False
    return True


def has_calendar(year=None):
    """该年份的节假日表是否已维护（未维护时只能按工作日判断）。"""
    return str(year or datetime.date.today().year) in HOLIDAYS


def last_trade_day(d=None):
    """不晚于 d 的最近一个交易日。"""
    d = _to_date(d or datetime.date.today())
    for _ in range(40):
        if is_trade_day(d):
            return d
        d -= datetime.timedelta(days=1)
    return d


def prev_trade_day(d=None):
    """严格早于 d 的最近一个交易日。"""
    d = _to_date(d or datetime.date.today()) - datetime.timedelta(days=1)
    return last_trade_day(d)
