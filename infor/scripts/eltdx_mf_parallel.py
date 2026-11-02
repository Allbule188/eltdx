#!/usr/bin/env python
"""10 连接并行跑全市场 money_flow —— 每连接固定负责一批股票（用户方案验证）。

A/B 交替的成因是「同一只股票被轮流分到不同连接」。
本方案让每只股票永远只从同一条连接取，理论上消除交替。

设计：
  * 10 个独立 TdxClient，各自 pool_size=1
  * **全部固定连同一台主站**，共享同一刷新时钟
  * 代码按 codes[i::10] 轮转切分，每只股票只属于一条连接
  * 连跑 3 轮，逐股校验 total_amount 是否单调（无回退 = 无 A/B 交替）
"""
import statistics
import threading
import time
from datetime import datetime

from eltdx import TdxClient

NCONN = 10
ROUNDS = 3

# results[worker][round] = {code: total_amount}
results = [{} for _ in range(NCONN)]
timing = [{} for _ in range(NCONN)]


def sweep(worker, client, codes, barrier):
    for rnd in range(ROUNDS):
        barrier.wait()                      # 10 路同时起跑
        t0 = time.time()
        cur = {}
        for cd in codes:
            try:
                cur[cd] = client.money_flow.daily(cd).records[0].total_amount
            except Exception:
                pass
        results[worker][rnd] = cur
        timing[worker][rnd] = time.time() - t0


print("选主站...")
with TdxClient(timeout=10) as probe:
    host = probe.transport.hosts[0]
print(f"10 条连接全部固定到 {host}\n")

with TdxClient.from_hosts(hosts=[host], server_count=1,
                          probe_hosts=False, timeout=20) as c0:
    codes = list(c0.codes.all_a_shares())
chunks = [codes[i::NCONN] for i in range(NCONN)]
print(f"全市场 {len(codes)} 只 → {NCONN} 条连接 × {len(chunks[0])} 只")
print(f"连跑 {ROUNDS} 轮，每轮 10 路并行\n")

clients = [TdxClient.from_hosts(hosts=[host], server_count=1,
                                probe_hosts=False, timeout=20)
           for _ in range(NCONN)]
for c in clients:
    c.money_flow.daily(chunks[0][0])        # 预热

print(f"{'轮':>3} {'开始':>9} {'墙钟s':>8} {'各worker(s)':>34}")
print("-" * 62)
walls = []
for rnd in range(ROUNDS):
    ts = datetime.now()
    barrier = threading.Barrier(NCONN)
    ths = [threading.Thread(target=sweep,
                            args=(i, clients[i], chunks[i], barrier))
           for i in range(NCONN)]
    t0 = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    wall = time.time() - t0
    walls.append(wall)
    per = [round(timing[i][rnd], 1) for i in range(NCONN)]
    print(f"{rnd+1:>3} {ts:%H:%M:%S} {wall:>8.1f}   {per}", flush=True)

med = statistics.median(walls)
print(f"\n墙钟 中位 {med:.1f}s  min {min(walls):.1f}  max {max(walls):.1f}")
print(f"对比串行 240s/轮 → 加速 {240/med:.1f}x")

# ---- 覆盖率 ----
cov = sum(len(results[i][ROUNDS-1]) for i in range(NCONN))
print(f"\n覆盖 {cov} / {len(codes)}  ({cov/len(codes)*100:.1f}%)")

# ---- 关键校验：逐股是否单调（A/B 交替会表现为回退）----
print(f"\n=== 逐股单调性校验（{ROUNDS} 轮）===")
mono_up = back = same = 0
worst = []
for i in range(NCONN):
    for cd in results[i][0]:
        vals = [results[i][r].get(cd) for r in range(ROUNDS)]
        if any(v is None for v in vals):
            continue
        d1, d2 = vals[1] - vals[0], vals[2] - vals[1]
        if d1 > 0 and d2 > 0:
            mono_up += 1
        elif d1 == 0 and d2 == 0:
            same += 1
        elif d1 < 0 or d2 < 0:
            back += 1
            if len(worst) < 5:
                worst.append((cd, vals))
print(f"  单调递增 {mono_up}   两轮无变化 {same}   **出现回退 {back}**")
if worst:
    print(f"  回退样例:")
    for cd, v in worst:
        print(f"    {cd}: {[f'{x:,.0f}' for x in v]}")
else:
    print(f"  ✓ 无任何回退 —— 每只股票固定连接后，A/B 交替消失")

for c in clients:
    c.close()
