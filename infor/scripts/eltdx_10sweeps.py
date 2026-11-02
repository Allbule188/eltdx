#!/usr/bin/env python
"""全市场 5 档 × 10 轮，测速率。用 refresh() 单趟路径。"""
import statistics
import time
import tracemalloc
from concurrent.futures import ThreadPoolExecutor

from eltdx import TdxClient

BATCH, THREADS, ROUNDS = 100, 16, 10

with TdxClient(timeout=8) as c:
    codes = list(c.codes.all_a_shares())
batches = [codes[i:i + BATCH] for i in range(0, len(codes), BATCH)]
print(f"全市场 {len(codes)} 只 → {len(batches)} 批 × {BATCH} 只   线程 {THREADS}")
print(f"路径: quotes.refresh() 单趟往返 / 批  (五档)\n")


def sweep(client, ex):
    out = {}
    for page in ex.map(lambda b: client.quotes.refresh(b, cursors={}), batches):
        for r in page.records:
            out[r.full_code] = r
    return out


def sig(r):
    """变化指纹：价格 + 总量 + 买一/卖一"""
    b = r.buy_levels[0] if r.buy_levels else None
    a = r.sell_levels[0] if r.sell_levels else None
    return (r.last_price, r.total_hand,
            b.price if b else None, b.volume if b else None,
            a.price if a else None, a.volume if a else None)


with TdxClient.from_hosts(pool_size=THREADS, probe_hosts=False, timeout=8) as c:
    with ThreadPoolExecutor(max_workers=THREADS) as ex:
        t = time.perf_counter()
        sweep(c, ex); sweep(c, ex)                      # 预热连接池
        warm = time.perf_counter() - t
        print(f"连接池预热(2轮): {warm:.2f}s\n")

        print(f"{'轮':>3} {'起始':>9} {'耗时(s)':>8} {'吞吐(只/s)':>11} {'覆盖':>6} "
              f"{'5档全':>6} {'缺档':>5} {'变化只数':>9} {'内存MB':>7}")
        print("-" * 78)

        tracemalloc.start()
        walls, prev, nchange = [], None, []
        for i in range(1, ROUNDS + 1):
            t0 = time.perf_counter()
            snap = sweep(c, ex)
            w = time.perf_counter() - t0
            walls.append(w)

            full5 = sum(1 for r in snap.values()
                        if len(r.buy_levels) == 5 and len(r.sell_levels) == 5)
            cur = tracemalloc.get_traced_memory()[0]

            if prev is not None:
                sigs = {k: sig(v) for k, v in snap.items()}
                ch = sum(1 for k in sigs if k in prev and sigs[k] != prev[k])
                nchange.append(ch)
            else:
                ch = -1
            prev = {k: sig(v) for k, v in snap.items()}

            print(f"{i:>3} {time.strftime('%H:%M:%S'):>9} {w:>8.2f} {len(snap)/w:>11.0f} "
                  f"{len(snap):>6} {full5:>6} {len(codes)-len(snap):>5} "
                  f"{ch if ch >= 0 else '-':>9} {cur/1024/1024:>7.2f}")
        tracemalloc.stop()

m = statistics.median(walls)
print("\n" + "=" * 78)
print(f"  轮次耗时   中位 {m:.2f}s   最快 {min(walls):.2f}s   最慢 {max(walls):.2f}s")
print(f"  稳态吞吐   {len(codes)/m:,.0f} 只/秒   ({len(codes)*10/m/10:,.0f} 只/秒 复核)")
print(f"  单轮请求   {len(batches)} 个 0x0547 请求")
print(f"  最大可持续频率  {1/m:.2f} 轮/秒  ({len(batches)/m:.0f} req/s)")
if nchange:
    print(f"  轮间变化   中位 {statistics.median(nchange):.0f} 只 / {len(codes)} 只 "
          f"({statistics.median(nchange)/len(codes)*100:.1f}%)")
    print(f"             最少 {min(nchange)}   最多 {max(nchange)}")
