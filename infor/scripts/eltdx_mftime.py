#!/usr/bin/env python
"""测 money_flow.daily 各字段的更新时间：逐字段追踪变化时刻。"""
import statistics
import time
from collections import defaultdict
from datetime import datetime

from eltdx import TdxClient

CODE = "sz300207"
POLL = 2.0
DURATION = 480.0          # 8 分钟

VALUE_FIELDS = [
    "total_amount",
    "main_net", "main_ratio",
    "main_super_large_net", "main_large_net",
    "main_medium_net", "main_small_net",
    "main_buy_net", "main_buy_ratio",
    "main_buy_super_large_net", "main_buy_large_net",
    "main_buy_medium_net", "main_buy_small_net",
]
SHORT = {f: f.replace("main_buy_", "mb.").replace("main_", "m.") for f in VALUE_FIELDS}

changes = defaultdict(list)      # field -> [t, ...]
samples = 0
snap = None
bucket_changes = []

with TdxClient.from_hosts(pool_size=2, probe_hosts=False, timeout=8) as c:
    c.money_flow.daily(CODE)          # 预热

    print(f"{CODE}  采样间隔 {POLL:.0f}s  持续 {DURATION/60:.0f} 分钟")
    print(f"开始 {datetime.now():%H:%M:%S}\n")
    print(f"{'时间':>9}  发生变化的字段")
    print("-" * 72)

    t0 = time.perf_counter()
    while time.perf_counter() - t0 < DURATION:
        t = time.perf_counter() - t0
        r = c.money_flow.daily(CODE).records[0]
        cur = {f: getattr(r, f) for f in VALUE_FIELDS}
        cur_b = tuple(r.buckets)
        samples += 1

        if snap is not None:
            ch = [f for f in VALUE_FIELDS if snap[f] != cur[f]]
            if cur_b != snap_b:
                bucket_changes.append(t)
            if ch:
                for f in ch:
                    changes[f].append(t)
                names = " ".join(SHORT[f] for f in ch)
                print(f"{datetime.now():%H:%M:%S}  ({len(ch):>2}) {names}")
        snap, snap_b = cur, cur_b
        time.sleep(POLL)

el = time.perf_counter() - t0
print(f"\n{'='*72}")
print(f"采样 {samples} 次 / {el:.0f}s / 实际间隔 {el/samples:.1f}s\n")
print(f"{'字段':<26} {'变化次数':>8} {'中位间隔s':>10} {'最短s':>7} {'最长s':>7} {'占比':>7}")
print("-" * 72)
for f in VALUE_FIELDS:
    ts = changes[f]
    if len(ts) < 2:
        print(f"{SHORT[f]:<26} {len(ts):>8}  (不足以算间隔)")
        continue
    gaps = [ts[i]-ts[i-1] for i in range(1, len(ts))]
    print(f"{SHORT[f]:<26} {len(ts):>8} {statistics.median(gaps):>10.1f} "
          f"{min(gaps):>7.1f} {max(gaps):>7.1f} {len(ts)/samples*100:>6.0f}%")

print(f"\nbuckets 变化次数: {len(bucket_changes)}")
if len(bucket_changes) >= 2:
    bg = [bucket_changes[i]-bucket_changes[i-1] for i in range(1, len(bucket_changes))]
    print(f"  buckets 中位间隔 {statistics.median(bg):.1f}s  "
          f"最短 {min(bg):.1f}s  最长 {max(bg):.1f}s")
