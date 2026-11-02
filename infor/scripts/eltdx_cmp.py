#!/usr/bin/env python
"""同时采快照(0x0547)和资金流(0x0ffc)，比较两者的更新时刻。"""
import time
from datetime import datetime

from eltdx import TdxClient

CODE = "sz300207"
POLL = 0.5
DUR = 180.0

with TdxClient.from_hosts(pool_size=2, probe_hosts=False, timeout=8) as c:
    c.quotes.refresh(CODE, cursors={})
    c.money_flow.daily(CODE)

    print(f"{CODE}  同时采快照(0x0547) + 资金流(0x0ffc)  间隔 {POLL}s\n")
    print(f"{'时间':>9} {'快照':>26} {'资金流':>24}")
    print(f"{'':>9} {'update_time_raw 总量':>26} {'total_amount':>24}")
    print("-" * 64)

    prev_q, prev_m = None, None
    q_changes, m_changes = [], []
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < DUR:
        t = time.perf_counter() - t0
        q = c.quotes.refresh(CODE, cursors={}).records[0]
        m = c.money_flow.daily(CODE).records[0]

        qv = (q.update_time_raw, q.total_hand, q.last_price)
        mv = (m.total_amount, m.buckets)

        qc = prev_q is not None and qv != prev_q
        mc = prev_m is not None and mv != prev_m
        if qc: q_changes.append(t)
        if mc: m_changes.append(t)

        if qc or mc:
            mark = ("快照" if qc else "") + ("+资金" if qc and mc else
                                            ("资金" if mc else ""))
            print(f"{datetime.now():%H:%M:%S} {qv[0]:>12} {qv[1]:>13,} "
                  f"{mv[0]:>24,.0f}   ← {mark}")
        prev_q, prev_m = qv, mv
        time.sleep(POLL)

el = time.perf_counter() - t0
print(f"\n{'='*64}")
print(f"采样 {int(el/POLL)} 次 / {el:.0f}s")
print(f"  快照(0x0547) 变化 {len(q_changes):>3} 次  → 平均每 "
      f"{el/max(1,len(q_changes)):.2f}s")
print(f"  资金流(0x0ffc) 变化 {len(m_changes):>3} 次  → 平均每 "
      f"{el/max(1,len(m_changes)):.2f}s")
