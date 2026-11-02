#!/usr/bin/env python
"""全市场 5 档：refresh() 单趟路径 vs full_quotes() 双趟路径。"""
import statistics
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor

from eltdx import TdxClient

BATCH = 100          # 0x0547 硬上限
SWEEPS = 5           # 连续扫描轮数


def chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def sweep_refresh(client, batches, threads):
    """refresh()：1 趟往返 / 100 只"""
    out = {}

    def one(b):
        page = client.quotes.refresh(b, cursors={})
        return page.records

    if threads == 1:
        for b in batches:
            for r in one(b):
                out[r.full_code] = r
    else:
        with ThreadPoolExecutor(max_workers=threads) as ex:
            for recs in ex.map(one, batches):
                for r in recs:
                    out[r.full_code] = r
    return out


def sweep_full_quotes(client, batches, threads):
    """full_quotes()：2 趟往返 / 80 只"""
    out = {}

    def one(b):
        return client.helpers.full_quotes(b)

    if threads == 1:
        for b in batches:
            for r in one(b):
                out[r.full_code] = r
    else:
        with ThreadPoolExecutor(max_workers=threads) as ex:
            for recs in ex.map(one, batches):
                for r in recs:
                    out[r.full_code] = r
    return out


with TdxClient.from_hosts(pool_size=8, probe_hosts=False, timeout=8) as c:
    codes = list(c.codes.all_a_shares())
    b100 = chunks(codes, BATCH)
    b80 = chunks(codes, 80)
    print(f"全 A 股 {len(codes)} 只   → refresh: {len(b100)} 批(100/批)   "
          f"full_quotes: {len(b80)} 批(80/批)\n")

    # 预热
    c.quotes.refresh(codes[:100], cursors={})
    c.helpers.full_quotes(codes[:80])

    print(f"{'路径':<22} {'线程':>5} {'耗时(s)':>9} {'吞吐(只/s)':>12} {'覆盖':>7} "
          f"{'5档完整':>9} {'缺档':>6}")
    print("-" * 78)

    for label, fn, batches in [
        ("refresh() 单趟", sweep_refresh, b100),
        ("full_quotes() 双趟", sweep_full_quotes, b80),
    ]:
        for threads in (1, 8):
            t = time.perf_counter()
            res = fn(c, batches, threads)
            w = time.perf_counter() - t
            full5 = sum(1 for r in res.values()
                        if len(r.buy_levels) == 5 and len(r.sell_levels) == 5)
            missing = len(codes) - len(res)
            print(f"{label:<22} {threads:>5} {w:>9.2f} {len(res)/w:>12.0f} "
                  f"{len(res):>7} {full5:>9} {missing:>6}")

    # 连续扫描 + 内存
    print(f"\n=== 连续 {SWEEPS} 轮全市场 5 档（refresh, 8 线程）===")
    tracemalloc.start()
    walls, sizes = [], []
    for i in range(SWEEPS):
        t = time.perf_counter()
        res = sweep_refresh(c, b100, 8)
        w = time.perf_counter() - t
        walls.append(w)
        cur, peak = tracemalloc.get_traced_memory()
        sizes.append(cur)
        moved = sum(1 for r in res.values() if r.total_hand)
        print(f"  第{i+1}轮: {w:>5.2f}s   {len(res)} 只   "
              f"快照内存 {cur/1024/1024:>5.2f} MB   (峰值 {peak/1024/1024:.2f} MB)")
        del res
    tracemalloc.stop()

    print(f"\n  轮次耗时: 中位 {statistics.median(walls):.2f}s   "
          f"min {min(walls):.2f}s   max {max(walls):.2f}s")
    print(f"  快照常驻: 约 {statistics.median(sizes)/1024/1024:.2f} MB ({len(codes)} 只 × 5档×2边)")
    print(f"  1 秒轮询可行性: 每轮 {statistics.median(walls):.2f}s → "
          f"占空比 {statistics.median(walls)*100:.0f}%")
