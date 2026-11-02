#!/usr/bin/env python
"""测 TDX 行情的最小刷新粒度：高频轮询看服务端更新时间戳和版本号的跳动。"""
import statistics
import time
from collections import defaultdict

from eltdx import TdxClient

CODES = ["sz000001", "sh600519", "sz300750", "sz300207", "sh601318",
         "sh600000", "sz000858", "sh601899", "sz002594", "sh600036"]
DURATION = 60.0
POLL = 0.25          # 每 0.25 秒轮一次

samples = defaultdict(list)      # code -> [(wall_t, update_time_raw, active, price, hand, b1p,b1v,a1p,a1v)]

with TdxClient.from_hosts(pool_size=2, probe_hosts=False, timeout=8) as c:
    c.quotes.refresh(CODES, cursors={})          # 预热
    print(f"高频轮询 {len(CODES)} 只，间隔 {POLL}s，持续 {DURATION:.0f}s\n")

    t0 = time.perf_counter()
    n = 0
    while time.perf_counter() - t0 < DURATION:
        t = time.perf_counter() - t0
        for r in c.quotes.refresh(CODES, cursors={}).records:
            b1 = r.buy_levels[0] if r.buy_levels else None
            a1 = r.sell_levels[0] if r.sell_levels else None
            samples[r.full_code].append((
                t, r.update_time_raw, r.active, r.last_price, r.total_hand,
                b1.price if b1 else None, b1.volume if b1 else None,
                a1.price if a1 else None, a1.volume if a1 else None,
            ))
        n += 1
        time.sleep(POLL)

elapsed = time.perf_counter() - t0
print(f"共 {n} 次轮询，{elapsed:.1f}s，平均间隔 {elapsed/n*1000:.0f}ms\n")

print("=== 服务端更新时间戳 update_time_raw 的跳动 ===")
print(f"{'代码':<10} {'采样数':>6} {'不同时间戳':>10} {'平均间隔ms':>11} {'最小间隔ms':>11}")
print("-" * 54)
all_gaps = []
for code in CODES:
    s = samples[code]
    # 按服务端时间戳去重，记录首次出现的墙钟时间
    marks, seen = [], set()
    for row in s:
        if row[1] not in seen:
            seen.add(row[1]); marks.append(row[0])
    gaps = [(marks[i]-marks[i-1])*1000 for i in range(1, len(marks))]
    all_gaps.extend(gaps)
    avg = statistics.mean(gaps) if gaps else 0
    mn = min(gaps) if gaps else 0
    print(f"{code:<10} {len(s):>6} {len(marks):>10} {avg:>11.0f} {mn:>11.0f}")

print("\n=== 版本号 active 的跳动（更细粒度）===")
print(f"{'代码':<10} {'不同active':>10} {'平均间隔ms':>11} {'最小间隔ms':>11}")
print("-" * 46)
act_gaps_all = []
for code in CODES:
    s = samples[code]
    marks, seen = [], set()
    for row in s:
        if row[2] not in seen:
            seen.add(row[2]); marks.append(row[0])
    gaps = [(marks[i]-marks[i-1])*1000 for i in range(1, len(marks))]
    act_gaps_all.extend(gaps)
    avg = statistics.mean(gaps) if gaps else 0
    mn = min(gaps) if gaps else 0
    print(f"{code:<10} {len(marks):>10} {avg:>11.0f} {mn:>11.0f}")

print(f"\n=== 汇总 ===")
if all_gaps:
    print(f"  服务端时间戳间隔: 中位 {statistics.median(all_gaps):.0f}ms  "
          f"最小 {min(all_gaps):.0f}ms  样本 {len(all_gaps)}")
if act_gaps_all:
    print(f"  active 版本间隔 : 中位 {statistics.median(act_gaps_all):.0f}ms  "
          f"最小 {min(act_gaps_all):.0f}ms  样本 {len(act_gaps_all)}")

# 完整状态变化的真实间隔
print(f"\n=== 完整盘口状态(价+量+买卖一)变化的间隔 ===")
state_gaps = []
for code in CODES:
    s = samples[code]
    marks, last = [], None
    for row in s:
        st = row[3:]
        if st != last:
            marks.append(row[0]); last = st
    state_gaps.extend([(marks[i]-marks[i-1])*1000 for i in range(1, len(marks))])
if state_gaps:
    state_gaps.sort()
    print(f"  中位 {statistics.median(state_gaps):.0f}ms   "
          f"p10 {state_gaps[int(len(state_gaps)*.1)]:.0f}ms   "
          f"p90 {state_gaps[int(len(state_gaps)*.9)]:.0f}ms")
    print(f"  最小 {min(state_gaps):.0f}ms   最大 {max(state_gaps):.0f}ms")

# 模拟不同轮询周期的捕获能力
print(f"\n=== 不同轮询周期能捕获多少种状态（10 只 × {elapsed:.0f}s）===")
print(f"{'周期':>6} {'捕获状态数':>10} {'相对0.25s':>10}")
print("-" * 32)
ref = None
for period in (0.25, 0.5, 1.0, 2.0, 3.0, 5.0):
    total, last = 0, {}
    for code in CODES:
        bucket = -1
        for row in samples[code]:
            b = int(row[0] // period)
            if b != bucket and b != last.get(code):
                total += 1
                last[code] = b
    if ref is None:
        ref = total
    print(f"{period:>6.2f} {total:>10} {total/ref*100:>9.0f}%")
