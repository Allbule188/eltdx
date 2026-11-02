#!/usr/bin/env python
"""逐笔成交(0x0fc5)的更新粒度测试：高频轮询看新成交出现的间隔。"""
import statistics
import time
from collections import defaultdict

from eltdx import TdxClient

CODES = ["sz300207", "sh600519"]
DURATION = 90.0
POLL = 0.25

with TdxClient.from_hosts(pool_size=4, probe_hosts=False, timeout=8) as c:
    # 先看 absolute_index 语义
    full = c.trades.all_today(CODES[0])
    ai = [t.absolute_index for t in full.ticks]
    print(f"sz300207 all_today: count={full.count}  ticks={len(full.ticks)}  "
          f"absolute_index 范围 {ai[0]}..{ai[-1]}")
    print(f"  首笔 {full.ticks[0].time_label}  末笔 {full.ticks[-1].time_label}\n")

    arrivals = defaultdict(list)      # code -> [(wall_t, n_distinct_trades)]
    costs = []
    print(f"高频轮询 {CODES}，间隔 {POLL}s，持续 {DURATION:.0f}s")

    t0 = time.perf_counter()
    last_sig = {}
    n = 0
    while time.perf_counter() - t0 < DURATION:
        t = time.perf_counter() - t0
        ta = time.perf_counter()
        for code in CODES:
            p = c.trades.today(code, count=8)
            costs.append((time.perf_counter() - ta) * 1000)
            # 用末尾成交的 (时间,价,量,订单数) 做指纹
            tail = tuple((x.time_minutes, x.price, x.volume, x.order_count)
                         for x in p.ticks[-3:])
            if tail != last_sig.get(code):
                last_sig[code] = tail
                arrivals[code].append((t, p.ticks[-1].absolute_index))
        n += 1
        time.sleep(POLL)

elapsed = time.perf_counter() - t0
print(f"{n} 次轮询 / {elapsed:.0f}s / 实际间隔 {elapsed/n*1000:.0f}ms")
print(f"单次 trades.today(count=8) 耗时: 中位 {statistics.median(costs):.0f}ms "
      f"(min {min(costs):.0f} max {max(costs):.0f})\n")

print("=== 新成交出现的间隔（逐笔的真实更新粒度）===")
print(f"{'代码':<10} {'更新次数':>8} {'平均间隔ms':>11} {'中位间隔ms':>11} "
      f"{'最小ms':>8} {'最大ms':>9}")
print("-" * 62)
allgaps = []
for code in CODES:
    marks = [a[0] for a in arrivals[code]]
    gaps = [(marks[i]-marks[i-1])*1000 for i in range(1, len(marks))]
    if not gaps:
        print(f"{code:<10} {0:>8}  (无)")
        continue
    allgaps.extend(gaps)
    print(f"{code:<10} {len(marks):>8} {statistics.mean(gaps):>11.0f} "
          f"{statistics.median(gaps):>11.0f} {min(gaps):>8.0f} {max(gaps):>9.0f}")

if allgaps:
    allgaps.sort()
    print(f"\n=== 汇总 ===")
    print(f"  中位间隔 {statistics.median(allgaps):.0f}ms   "
          f"p10 {allgaps[int(len(allgaps)*.1)]:.0f}ms   "
          f"p90 {allgaps[int(len(allgaps)*.9)]:.0f}ms")
    print(f"  快于 3 秒的比例: "
          f"{sum(1 for g in allgaps if g < 3000)/len(allgaps)*100:.0f}%")
    print(f"  快于 1 秒的比例: "
          f"{sum(1 for g in allgaps if g < 1000)/len(allgaps)*100:.0f}%")
