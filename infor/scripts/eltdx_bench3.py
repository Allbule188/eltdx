#!/usr/bin/env python
"""实验三：重复取样降噪 + 延迟来源分析（RTT 绑定 vs 批量大小）。"""
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from eltdx import TdxClient

N = 200
REPEATS = 3


def med(v):
    return statistics.median(v)


def run(client, codes, threads):
    def one(code):
        t = time.perf_counter()
        client.helpers.full_quotes([code])
        return time.perf_counter() - t
    lat = []
    c0, w0 = time.process_time(), time.perf_counter()
    if threads == 1:
        lat = [one(x) for x in codes]
    else:
        with ThreadPoolExecutor(max_workers=threads) as ex:
            lat = list(ex.map(one, codes))
    return time.perf_counter() - w0, time.process_time() - c0, lat


with TdxClient(timeout=8) as c:
    codes = list(c.codes.all_a_shares()[:N])

print(f"=== A. 线程扩展（{REPEATS} 次取样取中位数, N={N}）===")
hdr = f"{'线程':>5} {'wall(s)':>9} {'吞吐(/s)':>10} {'p50(ms)':>9} {'并行度':>8}"
print(hdr); print("-" * len(hdr))

res = {}
for threads in (1, 8, 16, 32):
    ws, ps, cpus = [], [], []
    for _ in range(REPEATS):
        with TdxClient.from_hosts(pool_size=max(threads, 8), probe_hosts=False, timeout=8) as cl:
            cl.helpers.full_quotes(codes[:8])
            w, cpu, lat = run(cl, codes, threads)
        ws.append(w); ps.append(statistics.median(lat) * 1000); cpus.append(cpu / w)
    res[threads] = med(ws)
    print(f"{threads:>5} {med(ws):>9.2f} {N/med(ws):>10.1f} {med(ps):>9.1f} {med(cpus):>8.2f}")

print("\n  加速比 vs 单线程:")
base = res[1]
for t in (8, 16, 32):
    print(f"    {t:>2} 线程: {base/res[t]:>5.2f}x")

print(f"\n=== B. 单次调用延迟 vs 批量大小（每档 5 次取中位数）===")
print(f"{'批量':>6} {'往返数':>7} {'延迟(ms)':>10} {'每条(ms)':>10}")
print("-" * 36)
with TdxClient.from_hosts(pool_size=8, probe_hosts=False, timeout=8) as c:
    c.helpers.full_quotes(codes[:8])
    for size in (1, 10, 40, 80, 160):
        ts = []
        for _ in range(5):
            t = time.perf_counter()
            c.helpers.full_quotes(codes[:size])
            ts.append(time.perf_counter() - t)
        m = statistics.median(ts) * 1000
        print(f"{size:>6} {max(1,(size+79)//80):>7} {m:>10.1f} {m/size:>10.2f}")
