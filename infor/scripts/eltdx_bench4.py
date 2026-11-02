#!/usr/bin/env python
"""实验四：实际生产配置 —— 拉全 A 股（5573 只），批量 + 少量线程。"""
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from eltdx import TdxClient

BATCH = 80


def chunks(seq, n):
    return [seq[i:i + n] for i in range(0, len(seq), n)]


with TdxClient(timeout=8) as c:
    codes = list(c.codes.all_a_shares())
print(f"全 A 股 {len(codes)} 只 → {len(chunks(codes, BATCH))} 个批次(每批 {BATCH})\n")

print(f"{'配置':<28} {'wall(s)':>9} {'吞吐(只/s)':>12} {'错误':>6}")
print("-" * 58)

results = {}
for label, pool, threads in [
    ("顺序, 分批80",              8,  1),
    ("4 线程, 分批80",            8,  4),
    ("8 线程, 分批80",            8,  8),
    ("16 线程, 分批80",          16, 16),
]:
    batches = chunks(codes, BATCH)
    errs = [0]
    got = [0]

    def one(b):
        try:
            r = cl.helpers.full_quotes(b)
            if not isinstance(r, list):
                errs[0] += 1
                return
            got[0] += len(r)
        except Exception:
            errs[0] += 1

    with TdxClient.from_hosts(pool_size=pool, probe_hosts=False, timeout=8) as cl:
        cl.helpers.full_quotes(codes[:8])
        t = time.perf_counter()
        if threads == 1:
            for b in batches:
                one(b)
        else:
            with ThreadPoolExecutor(max_workers=threads) as ex:
                list(ex.map(one, batches))
        w = time.perf_counter() - t

    results[label] = w
    print(f"{label:<28} {w:>9.2f} {got[0]/w:>12.0f} {errs[0]:>6}")

base = results["顺序, 分批80"]
print(f"\n  相对顺序分批的加速比:")
for k, w in results.items():
    if k != "顺序, 分批80":
        print(f"    {k:<24} {base/w:>5.2f}x")
