#!/usr/bin/env python
"""10 只股票高精度测 money_flow 刷新节拍（pool_size=1 -> 数据准；10只 -> 采样 ~0.45s）"""
import statistics
import time
from collections import Counter
from datetime import datetime

from eltdx import TdxClient

POLL = 0.0
DURATION = 420.0
N = 10

with TdxClient.from_hosts(pool_size=1, server_count=1,
                          probe_hosts=False, timeout=15) as c:
    codes = list(c.codes.all_a_shares()[:N])
    c.money_flow.daily(codes)
    print(f"{N} 只  持续 {DURATION/60:.0f} 分钟  开始 {datetime.now():%H:%M:%S}\n")

    def sig(r):
        return (r.total_amount, r.buckets[0], r.buckets[8])

    prev, per_code = {}, {cd: [] for cd in codes}
    ticks, polls = [], 0
    t0 = time.time()
    while time.time() - t0 < DURATION:
        t = time.time() - t0
        try:
            page = c.money_flow.daily(codes)
        except Exception:
            time.sleep(0.5); continue
        polls += 1
        n = 0
        for blk in page.blocks:
            cd = blk.full_code
            s = sig(blk.records[0])
            if cd in prev and s != prev[cd]:
                per_code[cd].append(t); n += 1
            prev[cd] = s
        if n:
            ticks.append((t, n))
        time.sleep(POLL)

    el = time.time() - t0
    print(f"轮次 {polls} / {el:.0f}s / 采样分辨率 {el/polls*1000:.0f}ms")
    print(f"变化的轮次 {len(ticks)}\n")

    allg = []
    for cd, ts in per_code.items():
        allg += [ts[i]-ts[i-1] for i in range(1, len(ts))]
    print(f"=== 每只变化间隔 (n={len(allg)}) ===")
    if allg:
        g = sorted(allg)
        print(f"  中位 {statistics.median(g):.2f}s  平均 {statistics.mean(g):.2f}s")
        print(f"  p10 {g[int(len(g)*.1)]:.2f}s  p90 {g[int(len(g)*.9)]:.2f}s  "
              f"min {min(g):.2f}s  max {max(g):.2f}s")
        print(f"  10s 归桶: {dict(sorted(Counter(round(x/10)*10 for x in g).items()))}")

    print(f"\n=== 同步性 ===")
    print(f"  每轮变化只数分布: {dict(sorted(Counter(n for _,n in ticks).items()))}")
    print(f"  单独一只变化的轮次: {sum(1 for _,n in ticks if n==1)}/{len(ticks)}")

    print(f"\n=== 事件时刻（原始，供判读）===")
    for t, n in ticks:
        print(f"   t={t:7.2f}s  变化 {n:>2} 只   wall%5={ (t0+t) % 5:5.2f}  "
              f"wall%3={(t0+t) % 3:5.2f}")

    if len(ticks) > 2:
        g = [ticks[i][0]-ticks[i-1][0] for i in range(1, len(ticks))]
        print(f"\n=== 轮次事件间隔 ===")
        print(f"  中位 {statistics.median(g):.2f}s  min {min(g):.2f}s  max {max(g):.2f}s")
        print(f"  {[round(x,2) for x in g]}")
        # 关键：看是否所有间隔都是采样分辨率的整数倍
        res = el/polls
        print(f"\n  采样分辨率 {res:.2f}s —— 若真实节拍 < 分辨率，测不出来")
