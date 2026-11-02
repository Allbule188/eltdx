#!/usr/bin/env python
"""sz300207 单轮完整五档盘口。"""
import time
from datetime import datetime

from eltdx import TdxClient

CODE = "sz300207"

with TdxClient.from_hosts(pool_size=4, probe_hosts=False, timeout=8) as c:
    try:
        name = next((r.name for r in c.codes.latest_stock_list()
                     if r.full_code == CODE), "?")
    except Exception:
        name = "?"

    t0 = time.perf_counter()
    q = c.helpers.full_quotes(CODE)[0]
    dt = (time.perf_counter() - t0) * 1000

    chg = q.last_price - q.pre_close_price
    pct = chg / q.pre_close_price * 100

    print(f"\n  {CODE}  {name:<8}  {datetime.now():%Y-%m-%d %H:%M:%S}   耗时 {dt:.0f}ms")
    print(f"  现价 {q.last_price:.2f}   {chg:+.2f} ({pct:+.2f}%)   昨收 {q.pre_close_price:.2f}")
    print(f"  开 {q.open_price:.2f}   高 {q.high_price:.2f}   低 {q.low_price:.2f}")
    print(f"  总量 {q.total_hand:,} 手   现手 {q.current_hand}   成交额 {q.amount/1e8:.2f} 亿")
    print()

    bar = "  " + "─" * 46
    print(bar)
    for i in range(4, -1, -1):
        lv = q.sell_levels[i]
        mark = "  ← 卖一" if i == 0 else ""
        print(f"  卖{i+1}   {lv.price:>7.2f}   {lv.volume:>8,}{mark}")
    print(bar)
    for i in range(5):
        lv = q.buy_levels[i]
        mark = "  ← 买一" if i == 0 else ""
        print(f"  买{i+1}   {lv.price:>7.2f}   {lv.volume:>8,}{mark}")
    print(bar)

    bid_sum = sum(l.volume for l in q.buy_levels)
    ask_sum = sum(l.volume for l in q.sell_levels)
    diff = bid_sum - ask_sum
    ratio = diff / (bid_sum + ask_sum) * 100

    print(f"\n  委买 {bid_sum:,} 手   委卖 {ask_sum:,} 手")
    print(f"  委差 {diff:+,} 手   委比 {ratio:+.2f}%")
    print(f"  内盘 {q.inside_dish:,}   外盘 {q.outer_disc:,}   "
          f"外/内 {q.outer_disc/q.inside_dish:.3f}")
