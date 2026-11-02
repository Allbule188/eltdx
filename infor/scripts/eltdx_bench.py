#!/usr/bin/env python
"""eltdx 并发压测：验证 GIL 释放与连接池真实表现。

核心指标:
  wall  = 墙钟时间
  cpu   = 进程 CPU 时间（跨所有线程累加）
  par   = cpu / wall  →  >1 即证明多线程真的在并行跑（GIL 已释放）
"""
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from eltdx import TdxClient

N_CODES = int(sys.argv[1]) if len(sys.argv) > 1 else 200


def fetch_codes(n):
    with TdxClient(timeout=8) as c:
        codes = c.codes.all_a_shares()
        print(f"代码表可用 {len(codes)} 只，取前 {n} 只")
        return list(codes[:n])


def run_once(client, codes, threads):
    lat = []
    errors = [0]

    def one(code):
        t = time.perf_counter()
        try:
            client.helpers.full_quotes([code])
        except Exception:
            errors[0] += 1
            return None
        return time.perf_counter() - t

    cpu0, wall0 = time.process_time(), time.perf_counter()
    if threads == 1:
        for code in codes:
            r = one(code)
            if r is not None:
                lat.append(r)
    else:
        with ThreadPoolExecutor(max_workers=threads) as ex:
            for r in ex.map(one, codes):
                if r is not None:
                    lat.append(r)
    wall = time.perf_counter() - wall0
    cpu = time.process_time() - cpu0
    return wall, cpu, lat, errors[0]


def pct(vals, p):
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(int(len(s) * p), len(s) - 1)] * 1000


def main():
    codes = fetch_codes(N_CODES)
    n = len(codes)
    print()

    # 每个配置独立的 client（pool_size 是构造期决定的）
    configs = [
        (8, 1), (8, 2), (8, 4), (8, 8),
        (4, 8),      # 连接数 < 线程数 → 应看到池饱和
        (16, 8),
    ]

    results = {}
    hdr = f"{'pool':>5} {'线程':>5} {'wall(s)':>9} {'cpu(s)':>8} {'并行度':>7} {'吞吐(/s)':>10} {'p50(ms)':>9} {'p99(ms)':>9} {'错误':>5}"
    print(hdr)
    print("-" * len(hdr))

    for pool, threads in configs:
        with TdxClient.from_hosts(pool_size=pool, probe_hosts=False, timeout=8) as c:
            c.helpers.full_quotes(codes[:8])          # 预热，建立连接
            wall, cpu, lat, err = run_once(c, codes, threads)
        par = cpu / wall if wall else 0
        results[(pool, threads)] = (wall, cpu, par, lat, err)
        print(f"{pool:>5} {threads:>5} {wall:>9.2f} {cpu:>8.2f} {par:>7.2f} "
              f"{n / wall:>10.1f} {pct(lat, .50):>9.1f} {pct(lat, .99):>9.1f} {err:>5}")

    base = results[(8, 1)][0]
    print("\n=== 相对单线程加速比 (pool_size=8) ===")
    for threads in (1, 2, 4, 8):
        w = results[(8, threads)][0]
        par = results[(8, threads)][2]
        print(f"  {threads} 线程: 加速 {base / w:>5.2f}x   并行度 {par:>5.2f}x   "
              f"单请求均摊 {w / n * 1000:>6.2f} ms")

    print("\n=== 池容量影响 (8 线程) ===")
    for pool in (4, 8, 16):
        w, _, par, _, _ = results[(pool, 8)]
        print(f"  pool_size={pool:>2}: wall {w:>6.2f}s   加速 {base / w:>5.2f}x   并行度 {par:>5.2f}x")


if __name__ == "__main__":
    main()
