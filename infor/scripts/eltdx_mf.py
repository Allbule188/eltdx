#!/usr/bin/env python
"""测 money_flow.daily 能否盘中更新：高频采样跨过 11:30 上午收盘边界。"""
import time
from datetime import datetime

from eltdx import TdxClient

CODES = ["sz300207", "sz000001"]
POLL = 5.0
DURATION = 480.0          # 8 分钟，覆盖 11:26 -> 11:34

with TdxClient.from_hosts(pool_size=4, probe_hosts=False, timeout=8) as c:
    c.money_flow.daily(CODES[0])
    c.helpers.full_quotes(CODES[0])                    # 预热

    print(f"开始 {datetime.now():%H:%M:%S}   采样间隔 {POLL:.0f}s   "
          f"持续 {DURATION/60:.0f} 分钟")
    print(f"标的 {CODES}\n")
    print(f"{'时间':>9} {'现价':>7} {'总量(手)':>11} {'MF总额(亿)':>11} "
          f"{'MF主力净额(亿)':>14}  变化")
    print("-" * 78)

    prev = {}
    changes = 0
    t0 = time.perf_counter()
    last_hb = 0

    while time.perf_counter() - t0 < DURATION:
        q = c.helpers.full_quotes(CODES[0])[0]
        b = c.money_flow.daily(CODES[0])
        today = b.records[0]
        cur = (q.last_price, q.total_hand, today.total_amount, today.main_net)

        if prev.get(CODES[0]) != cur:
            mark = []
            if CODES[0] in prev:
                if prev[CODES[0]][0] != cur[0]: mark.append("价")
                if prev[CODES[0]][1] != cur[1]: mark.append("量")
                if prev[CODES[0]][2] != cur[2]: mark.append("总额")
                if prev[CODES[0]][3] != cur[3]: mark.append("主力")
                changes += 1
            else:
                mark = ["基线"]
            prev[CODES[0]] = cur
            print(f"{datetime.now():%H:%M:%S} {cur[0]:>7.2f} {cur[1]:>11,} "
                  f"{cur[2]/1e8:>11.4f} {cur[3]/1e8:>14.4f}  {'+'.join(mark)}")
        else:
            # 心跳：每 60s 打一次，证明还在采样
            el = int(time.perf_counter() - t0)
            if el - last_hb >= 60:
                last_hb = el
                print(f"{datetime.now():%H:%M:%S} {cur[0]:>7.2f} {cur[1]:>11,} "
                      f"{cur[2]/1e8:>11.4f} {cur[3]/1e8:>14.4f}  (无变化)")

        time.sleep(POLL)

    print(f"\n共 {changes} 次变化 / 采样约 {int(DURATION/POLL)} 次")
