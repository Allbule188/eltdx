#!/usr/bin/env python
"""最小不丢数据的轮询周期：先 0.25s 采样拿全部真实更新，再模拟各周期能召回多少。"""
import time
from collections import defaultdict

from eltdx import TdxClient

CODES = ["sz000001", "sh600519", "sz300750", "sz300207", "sh601318",
         "sh600000", "sz000858", "sh601899", "sz002594", "sh600036"]
DURATION = 90.0
POLL = 0.2

samples = defaultdict(list)

with TdxClient.from_hosts(pool_size=2, probe_hosts=False, timeout=8) as c:
    c.quotes.refresh(CODES, cursors={})
    t0 = time.perf_counter()
    n = 0
    while time.perf_counter() - t0 < DURATION:
        t = time.perf_counter() - t0
        for r in c.quotes.refresh(CODES, cursors={}).records:
            b1 = r.buy_levels[0] if r.buy_levels else None
            a1 = r.sell_levels[0] if r.sell_levels else None
            samples[r.full_code].append((
                t, r.last_price, r.total_hand,
                b1.price if b1 else None, b1.volume if b1 else None,
                a1.price if a1 else None, a1.volume if a1 else None,
            ))
        n += 1
        time.sleep(POLL)
elapsed = time.perf_counter() - t0
print(f"基线: {n} 次轮询 / {elapsed:.0f}s / 间隔 {elapsed/n*1000:.0f}ms\n")

# 真值：每个代码完整状态序列的「变化点」(时刻)
truth = {}
for code in CODES:
    pts, last = [], None
    for row in samples[code]:
        st = row[1:]
        if st != last:
            pts.append(row[0]); last = st
    truth[code] = pts

total_true = sum(len(v) for v in truth.values())
print(f"真实更新总数: {total_true} 次 (10 只 / {elapsed:.0f}s) "
      f"→ 平均每只 {total_true/10/elapsed:.2f} 次/秒\n")

print(f"{'轮询周期':>8} {'采样数':>7} {'召回更新':>9} {'召回率':>8} {'漏掉':>6} {'重复率':>8}")
print("-" * 52)
for period in (0.2, 0.5, 1.0, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0):
    hit, npoll = 0, 0
    for code in CODES:
        bucket, last = -1, None
        for row in samples[code]:
            b = int(row[0] // period)
            if b != bucket:
                bucket = b; npoll += 1
                st = row[1:]
                if st != last:
                    hit += 1
                    last = st
    dup = (npoll - hit) / npoll * 100 if npoll else 0
    print(f"{period:>8.2f} {npoll:>7} {hit:>9} {hit/total_true*100:>7.1f}% "
          f"{total_true-hit:>6} {dup:>7.1f}%")
