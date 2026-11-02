#!/usr/bin/env python
"""sz300207：10 轮完整五档 + 1档(get_snapshots) vs 5档(full_quotes) 速度对比。"""
import statistics
import time
from datetime import datetime

from eltdx import TdxClient

CODE = "sz300207"
ROUNDS = 10
INTERVAL = 2.0

t1s, t5s = [], []

with TdxClient.from_hosts(pool_size=4, probe_hosts=False, timeout=8) as c:
    for _ in range(2):                       # 预热，越过冷启动
        c.quotes.get_snapshots(CODE)
        c.helpers.full_quotes(CODE)

    print(f"{CODE}  10 轮五档 + 速度对比\n")
    for i in range(1, ROUNDS + 1):
        t = time.perf_counter()
        sn = c.quotes.get_snapshots(CODE)          # 1 档，单趟
        d1 = (time.perf_counter() - t) * 1000
        s = sn[0] if isinstance(sn, (list, tuple)) else sn

        t = time.perf_counter()
        q = c.helpers.full_quotes(CODE)[0]         # 5 档，两趟
        d5 = (time.perf_counter() - t) * 1000

        t1s.append(d1); t5s.append(d5)
        chg = (q.last_price - q.pre_close_price) / q.pre_close_price * 100

        print(f"轮 {i:>2}  {datetime.now():%H:%M:%S}   现价 {q.last_price:.2f} ({chg:+.2f}%)   "
              f"总量 {q.total_hand:,}")
        for j in range(4, -1, -1):
            ask, bid = q.sell_levels[j], q.buy_levels[j]
            print(f"     卖{j+1} {ask.price:>6.2f} {ask.volume:>7,}  │  "
                  f"买{j+1} {bid.price:>6.2f} {bid.volume:>7,}")
        bsum = sum(l.volume for l in q.buy_levels)
        asum = sum(l.volume for l in q.sell_levels)
        wb = (bsum - asum) / (bsum + asum) * 100
        print(f"     委比 {wb:>+7.2f}%   │  1档 {d1:>6.1f}ms   5档 {d5:>6.1f}ms   "
              f"({d5/d1:.2f}x)\n")

        if i < ROUNDS:
            time.sleep(INTERVAL)

print("=" * 62)
print(f"  1 档 get_snapshots  (0x054c 单趟)")
print(f"      中位 {statistics.median(t1s):>7.1f} ms   均 {statistics.mean(t1s):>7.1f} ms   "
      f"min {min(t1s):>6.1f}   max {max(t1s):>6.1f}")
print(f"  5 档 full_quotes    (0x054c + 0x0547 两趟)")
print(f"      中位 {statistics.median(t5s):>7.1f} ms   均 {statistics.mean(t5s):>7.1f} ms   "
      f"min {min(t5s):>6.1f}   max {max(t5s):>6.1f}")
print(f"\n  5档 / 1档 倍数:  中位 {statistics.median(t5s)/statistics.median(t1s):.2f}x   "
      f"均值 {statistics.mean(t5s)/statistics.mean(t1s):.2f}x")
print(f"  多出的绝对开销:  中位 {statistics.median(t5s)-statistics.median(t1s):.1f} ms")
