#!/usr/bin/env python
"""实验二：线程数越过核数(8)继续加压 + 批量 vs 单条对比。"""
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from eltdx import TdxClient

N = 200


def pct(vals, p):
    s = sorted(vals)
    return s[min(int(len(s) * p), len(s) - 1)] * 1000


def timed(fn):
    c0, w0 = time.process_time(), time.perf_counter()
    out = fn()
    return time.perf_counter() - w0, time.process_time() - c0, out


# ---------- 网络 RTT 基线 ----------
host = "123.60.70.228"
r = subprocess.run(["ping", "-c", "5", "-t", "5", host], capture_output=True, text=True)
line = [l for l in r.stdout.splitlines() if "round-trip" in l or "min/avg" in l]
print(f"ICMP 到主站 {host}: {line[0].split('=')[-1].strip() if line else '不可达(可能禁 ping)'}\n")

with TdxClient(timeout=8) as c:
    codes = list(c.codes.all_a_shares()[:N])

# ---------- A. 线程数越过核数 ----------
print("=== A. 线程数越过 8 核继续加压 (pool_size = 线程数) ===")
hdr = f"{'线程':>5} {'pool':>5} {'wall(s)':>9} {'cpu(s)':>8} {'并行度':>7} {'吞吐(/s)':>10} {'p50(ms)':>9} {'p99(ms)':>9}"
print(hdr); print("-" * len(hdr))

seq_wall = None
A = {}
for threads in (1, 8, 16, 32, 64):
    with TdxClient.from_hosts(pool_size=threads, probe_hosts=False, timeout=8) as c:
        c.helpers.full_quotes(codes[:8])
        lat = []
        def one(code, c=c):
            t = time.perf_counter()
            c.helpers.full_quotes([code])
            return time.perf_counter() - t
        if threads == 1:
            w, cpu, _ = timed(lambda: [lat.append(one(x)) for x in codes])
        else:
            def go():
                with ThreadPoolExecutor(max_workers=threads) as ex:
                    lat.extend(ex.map(one, codes))
            w, cpu, _ = timed(go)
    A[threads] = (w, cpu)
    if threads == 1:
        seq_wall = w
    print(f"{threads:>5} {threads:>5} {w:>9.2f} {cpu:>8.2f} {cpu/w:>7.2f} "
          f"{N/w:>10.1f} {pct(lat,.50):>9.1f} {pct(lat,.99):>9.1f}")

print("\n  相对单线程加速比:")
for t in (8, 16, 32, 64):
    print(f"    {t:>2} 线程: {seq_wall/A[t][0]:>5.2f}x   (核数只有 8)")

# ---------- B. 批量 vs 单条 ----------
print("\n=== B. 批量 vs 单条 (200 只股票) ===")
with TdxClient.from_hosts(pool_size=8, probe_hosts=False, timeout=8) as c:
    c.helpers.full_quotes(codes[:8])
    w, _, out = timed(lambda: c.helpers.full_quotes(codes))
    print(f"  1 次 full_quotes(200 只)          : {w*1000:>8.0f} ms   返回 {len(out)} 条")

    w, _, _ = timed(lambda: [c.helpers.full_quotes([x]) for x in codes])
    print(f"  200 次 full_quotes(1 只) 顺序      : {w*1000:>8.0f} ms")

    def par():
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda x: c.helpers.full_quotes([x]), codes))
    w, _, _ = timed(par)
    print(f"  200 次 full_quotes(1 只) 8 线程    : {w*1000:>8.0f} ms")
