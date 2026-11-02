#!/usr/bin/env python
"""money_flow 刷新侦察：捕获变化点，逐位 diff raw/buckets，找计数器 + 测刷新间隔。

必须 pool_size=1（多连接会 A/B 交替污染）。
"""
import statistics
import time
from datetime import datetime

from eltdx import TdxClient

CODE, POLL, DUR = "sz300207", 0.5, 240.0

TIER = ("super_large", "large", "medium", "small")

with TdxClient.from_hosts(pool_size=1, server_count=1,
                          probe_hosts=False, timeout=8) as c:
    c.quotes.refresh(CODE, cursors={})
    c.money_flow.daily(CODE)

    q = c.quotes.refresh(CODE, cursors={}).records[0]
    print(f"{CODE}  现价 {q.last_price}  总量 {q.total_hand:,}   "
          f"{datetime.now():%H:%M:%S}")
    print(f"单连接 pool_size=1  采样 {POLL}s  持续 {DUR/60:.0f} 分钟\n")

    marks, prev = [], None
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < DUR:
        t = time.perf_counter() - t0
        r = c.money_flow.daily(CODE).records[0]
        cur = {
            "ta": r.total_amount,
            "buckets": tuple(r.buckets),
            "raw": tuple(r.raw),
            **{f"m.{k}": getattr(r, f"main_{k}_net") for k in TIER},
            **{f"mb.{k}": getattr(r, f"main_buy_{k}_net") for k in TIER},
        }
        if prev is not None:
            diff = [k for k in cur if cur[k] != prev[k]]
            if diff:
                marks.append((t, cur, prev, diff))
        prev = cur
        time.sleep(POLL)

el = time.perf_counter() - t0
print(f"采样 {int(el/POLL)} 次 / {el:.0f}s   捕获变化 {len(marks)} 次\n")

if not marks:
    print("全程无变化 —— 无法判定刷新间隔（可能间隔 > 采样窗口）")
else:
    print("=== 每次变化涉及的字段 ===")
    print(f"{'#':>3} {'时刻':>8} {'t(s)':>7}  变化字段")
    print("-" * 70)
    for i, (t, cur, prv, diff) in enumerate(marks, 1):
        print(f"{i:>3} {'':>8} {t:>7.1f}  {len(diff)} 个: {', '.join(sorted(diff))}")

    # raw 逐位分析
    print(f"\n=== raw 在变化点上的逐位 diff（找计数器）===")
    raw_pos = {}
    for t, cur, prv, _ in marks:
        for i, (a, b) in enumerate(zip(prv["raw"], cur["raw"])):
            if a != b:
                raw_pos.setdefault(i, []).append(b - a)
    if not raw_pos:
        print("  raw 全程未变化 -> raw 里没有逐次更新的计数器")
    else:
        for i in sorted(raw_pos):
            ds = raw_pos[i]
            small = all(0 < d < 100000 for d in ds)
            print(f"  raw[{i:>2}]  变化 {len(ds):>2} 次  增量 {ds[:4]}"
                  f"{'   <= 疑似计数器' if small else ''}")

    # buckets 逐位
    bpos = set()
    for t, cur, prv, _ in marks:
        for i, (a, b) in enumerate(zip(prv["buckets"], cur["buckets"])):
            if a != b:
                bpos.add(i)
    print(f"\n  buckets 变化位: {sorted(bpos) if bpos else '无'}")

    # 间隔
    if len(marks) > 1:
        ts = [m[0] for m in marks]
        g = [ts[i] - ts[i - 1] for i in range(1, len(ts))]
        print(f"\n=== 刷新间隔（{len(g)} 个样本）===")
        print(f"  中位 {statistics.median(g):.2f}s   平均 {statistics.mean(g):.2f}s")
        print(f"  最短 {min(g):.2f}s   最长 {max(g):.2f}s")
        print(f"  全部: {[round(x,1) for x in g]}")

    # 四档字段是否同步
    print(f"\n=== 四档字段同步性 ===")
    allf = [k for k in marks[0][1] if k not in ("ta", "buckets", "raw")]
    counts = {k: sum(1 for m in marks if k in m[3]) for k in allf}
    same = len(set(counts.values())) == 1
    print(f"  各字段变化次数: {counts}")
    print(f"  {'全部同步（同一份快照）' if same else '存在不同步字段'}")
