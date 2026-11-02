#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""资金流刷新网格验证 —— 刷新是否发生在固定的墙钟时刻？

背景
--------------------------------------------------------------------------------
2026-09-21 实测发现 money_flow (0x0ffc) 的值变化时刻似乎落在固定的墙钟网格上
（15 个观测里 14 个落在不到 1 秒宽的相位窗口内），但网格周期未能确定：
10 秒网格下相位分成两簇（1.3 / 6.3，相差 5），暗示真实周期是 5 秒。

本脚本用更严格的统计方法定论。

方法
--------------------------------------------------------------------------------
1. 0.1s 高频采样 money_flow，记录每次"值变化"的墙钟时刻（epoch，秒级小数）
2. 用快照的 update_time_raw（服务端 HHMMSS）校准本机时钟 vs 服务端时钟
3. 对候选周期 P 做**相位聚集度**分析（圆统计 resultant length R）：
      R = |mean(exp(2πi·(t mod P)/P))|
      R → 1  变化时刻精确落在 P 的网格上
      R → 0  相位均匀分布（无固定网格）
4. 报告 R 峰值、最小显著周期、半程相位稳定性

判读
--------------------------------------------------------------------------------
* 找到 R 很高的小周期 P  -> 刷新在固定墙钟网格上，周期 = P
* 所有 P 的 R 都很低      -> 刷新不是固定时刻，是"值变了才变"
* 候选中 P 的倍数也会高 R -> 正常现象，取最小的那个

限制
--------------------------------------------------------------------------------
* 只能观测"值变化"，不能观测"刷新动作"（money_flow 无时间戳/版本号字段）。
  所以测到的是"值变化时刻的网格"，不是"服务端刷新动作的网格"。
  若服务端每 5 秒刷新但值 40 秒才变一次，本脚本只能看到后者。
* 必须 pool_size=1 —— 多连接会让资金流 A/B 交替，严重污染结果。

用法
--------------------------------------------------------------------------------
    .venv/bin/python infor/scripts/eltdx_grid_probe.py [分钟数]

    默认 15 分钟。建议 >=10 分钟（按 ~40s 一次变化，10 分钟约 15 个样本）。
    必须在交易时段运行，非交易时段会直接退出。
"""

import cmath
import json
import math
import statistics
import sys
import time
from datetime import datetime

from eltdx import TdxClient

CODE = "sz300207"
POLL = 0.1                       # 采样间隔
SESSIONS = [(9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60)]


def in_session(now=None):
    now = now or datetime.now()
    m = now.hour * 60 + now.minute
    return any(a <= m < b for a, b in SESSIONS)


def hhmmss_to_sec(v):
    """140024 -> 14*3600 + 0*60 + 24"""
    h, m, s = v // 10000, (v // 100) % 100, v % 100
    return h * 3600 + m * 60 + s


def resultant(times, period):
    """圆统计 resultant length。1=完全聚集，0=均匀。"""
    if len(times) < 3 or period <= 0:
        return 0.0
    z = sum(cmath.exp(2j * math.pi * (t % period) / period) for t in times)
    return abs(z) / len(times)


def circular_phase(times, period):
    """返回聚集相位的中心（秒）与半宽。"""
    if not times:
        return None, None
    ph = sorted(t % period for t in times)
    # 用圆均值求中心
    z = sum(cmath.exp(2j * math.pi * p / period) for p in ph)
    if abs(z) < 1e-12:
        return None, None
    center = (cmath.phase(z) / (2 * math.pi) * period) % period
    # 半宽 = 到中心的最大圆距离
    half = max(min(abs(p - center), period - abs(p - center)) for p in ph)
    return center, half


def grid_fit(intervals, period):
    """所有间隔是否都是 period 的整数倍。返回最大偏差。"""
    return max(abs(x - round(x / period) * period) for x in intervals)


def fundamental_grid(intervals, hi=90.0, tol=0.8):
    """求最大周期 G，使所有间隔都约等于 G 的整数倍。

    这是判定"网格周期"的正确方法。用 periodogram 的 R 值不行 ——
    真周期的**约数**（如 5s 的 2.5s/1s）和**倍数**都会得高 R，
    因为"落在 5 秒网格上"必然也落在 2.5 秒网格上。

    【关键】平凡拟合下限 = 2×tol。任何 P <= 2×tol 的周期都能平凡拟合
    任意间隔（因为 |x - round(x/P)*P| <= P/2 <= tol）。所以低于该下限的
    结果没有意义。本函数直接从 lo = 2.2×tol 起扫，并在返回值里带出下限。

    参数 tol 应与测量精度匹配：采样间隔 POLL 时，检测误差约 ±POLL，
    故 tol 取 ~max(0.5, 3×POLL)。tol 越小能分辨的网格越细。

    返回 (最佳周期, 最大偏差, 候选列表, 平凡下限)。
    """
    lo = 2.2 * tol                      # 高于平凡拟合下限
    cands = []
    P = hi
    while P >= lo:
        e = grid_fit(intervals, P)
        if e <= tol:
            cands.append((round(P, 2), round(e, 3)))
        P = round(P - 0.1, 2)
    cands.sort(key=lambda x: -x[0])     # 从大到小
    if not cands:
        return None, None, [], lo
    return cands[0][0], cands[0][1], cands[:8], lo


def selftest():
    """用合成数据验证判据逻辑（不需要开盘）。"""
    import random
    print("=" * 70)
    print("自检：用已知网格的合成数据验证判据")
    print("=" * 70)

    def run(label, iv, truth, tol=0.8):
        G, e, c, floor = fundamental_grid(iv, tol=tol)
        ok = "✓" if (truth is None and G is None) or \
                    (truth is not None and G and abs(G - truth) < truth * 0.12) \
             else "✗"
        print(f"  {label:<26} tol={tol}  下限{floor:.1f}s  "
              f"-> 网格 {G}  偏差 {e}  {ok}")
        return ok == "✓"

    random.seed(1)
    a = run("合成 5s 网格", [random.choice([4,5,6,7,8]) * 5.0
                            + random.uniform(-.3, .3) for _ in range(25)], 5.0)
    random.seed(2)
    b = run("合成 10s 网格", [random.choice([2,3,4,5]) * 10.0
                             + random.uniform(-.3, .3) for _ in range(25)], 10.0)
    random.seed(3)
    c = run("纯随机(应无网格)", sorted(random.uniform(20, 60)
                                       for _ in range(25)), None)
    print()
    if a and b and c:
        print("  判据自检通过。可以放心用实盘数据。")
        return 0
    print("  判据自检失败 —— 不要相信实盘结果，先修判据。")
    return 1


def main():
    if "--selftest" in sys.argv:
        return selftest()

    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    dur = minutes * 60

    if not in_session():
        print(f"[中止] 现在 {datetime.now():%H:%M:%S} 不在交易时段。")
        print(f"       交易时段: 09:30-11:30, 13:00-15:00（周一至周五）")
        return 1

    print("=" * 74)
    print(f"资金流刷新网格验证   {CODE}")
    print(f"采样 {POLL}s   持续 {minutes:.0f} 分钟   "
          f"开始 {datetime.now():%H:%M:%S}")
    print("=" * 74)

    with TdxClient.from_hosts(pool_size=1, server_count=1,
                              probe_hosts=False, timeout=8) as c:
        c.quotes.refresh(CODE, cursors={})
        c.money_flow.daily(CODE)

        # ---------- 1. 时钟校准 ----------
        print("\n[1/4] 校准本机时钟 vs 服务端时钟...")
        offs = []
        for _ in range(40):
            q = c.quotes.refresh(CODE, cursors={}).records[0]
            srv = hhmmss_to_sec(q.update_time_raw)
            loc = datetime.now()
            loc_s = loc.hour * 3600 + loc.minute * 60 + loc.second + loc.microsecond / 1e6
            offs.append(loc_s - srv)
            time.sleep(0.05)
        offs.sort()
        clock_off = offs[0]          # 最小值最接近真值（服务端时间只前进不后退）
        print(f"      本机 - 服务端 ≈ {clock_off:+.2f}s  "
              f"(中位 {statistics.median(offs):+.2f}s, 最小 {offs[0]:+.2f}s)")
        print(f"      注: update_time_raw 只有秒级精度，此值有 ±1s 不确定性")

        # ---------- 2. 高频采样 ----------
        print(f"\n[2/4] 高频采样 {minutes:.0f} 分钟...")
        marks = []
        prev = None
        prev_wc = None
        t0 = time.time()
        n = 0
        while time.time() - t0 < dur:
            wc = time.time()
            ta = c.money_flow.daily(CODE).records[0].total_amount
            n += 1
            if prev is not None and ta != prev:
                # 变化发生在 (prev_wc, wc] 之间，取中点（±POLL/2 不确定度）
                marks.append(((prev_wc + wc) / 2.0, ta - prev))
            prev, prev_wc = ta, wc
            time.sleep(POLL)

        el = time.time() - t0
        print(f"      采样 {n} 次 / {el:.0f}s   捕获变化 {len(marks)} 次")

        if len(marks) < 8:
            print(f"\n[中止] 变化点太少（{len(marks)} < 8），无法做周期分析。")
            print("       可能原因：流动性太低 / 时段太短。建议换活跃股或延长时间。")
            return 1

        times = [m[0] for m in marks]
        print(f"\n      变化时刻与间隔：")
        for i, (t, d) in enumerate(marks):
            gap = f"{t - times[i-1]:>7.2f}s" if i else "      -"
            print(f"        {datetime.fromtimestamp(t):%H:%M:%S}.{int(t%1*1000):03d}"
                  f"   间隔 {gap}   Δ={d:>+14,.0f}   wall%5={t%5:>5.2f}")

        intervals = [times[i] - times[i - 1] for i in range(1, len(times))]

        # ---------- 3a. 主判据：GCD 式网格拟合 ----------
        tol = max(0.5, 3 * POLL)
        print(f"\n[3/4] 网格周期判定（容差 {tol:.2f}s，采样 {POLL}s）...")
        G, gerr, cands, floor = fundamental_grid(intervals, tol=tol)
        print(f"      平凡拟合下限 = 2×容差 = {floor:.2f}s "
              f"（小于此值的周期恒成立，无意义）")

        if G:
            print(f"\n      >>> 网格周期 = {G:.1f}s  (最大偏差 {gerr:.2f}s)")
            if G < 1.8 * floor:
                print(f"      [警告] 该周期仅略高于平凡下限 {floor:.1f}s，"
                      f"可能是假阳性")
            print(f"      所有间隔都是 {G:.1f}s 的整数倍：")
            for x in sorted(intervals):
                print(f"        {x:>7.2f}s = {round(x/G):>2} × {G:.1f}s"
                      f"   偏差 {abs(x - round(x/G)*G):.2f}s")
            if len(cands) > 1:
                print(f"      其他可行周期: "
                      f"{[f'{p:.1f}s(偏差{e:.2f})' for p, e in cands[1:5]]}")
        else:
            print("      >>> 未找到共同网格周期")
            print(f"          所有 >{floor:.1f}s 的候选周期最大偏差都超限")
            print("          → 变化时刻不是固定网格（至少在当前精度下测不出）")

        # ---------- 3b. 圆统计（仅作佐证）----------
        print(f"\n      [佐证] 圆统计 R（注意：真周期的约数与倍数都会得高 R）")
        P = 1.0
        rs = []
        while P <= 61.0:
            rs.append((P, resultant(times, P)))
            P += 0.1
        rs.sort(key=lambda x: -x[1])
        print(f"      top R: " + "  ".join(f"{p:.1f}s/{r:.3f}" for p, r in rs[:6]))
        rnd_R = resultant([i * 13.7 % 400 for i in range(len(times))], 5.0)
        print(f"      对照(乱序同时刻)的 5.0s R = {rnd_R:.3f} → 实际 {resultant(times,5.0):.3f}")

        # ---------- 4. 相位稳定性 ----------
        if G:
            print(f"\n[4/4] 相位稳定性（周期 {G:.1f}s）")
            half = len(times) // 2
            for name, seg in (("前半程", times[:half]), ("后半程", times[half:])):
                ctr, hw = circular_phase(seg, G)
                print(f"      {name}: 相位 {ctr:.2f}s ± {hw:.2f}s  (n={len(seg)})")
            ctr, hw = circular_phase(times, G)
            print(f"      全段  : 相位 {ctr:.2f}s ± {hw:.2f}s")
            print(f"      扣除本机时钟偏差({clock_off:+.2f}s)后: "
                  f"{(ctr - clock_off) % G:.2f}s (相对服务端时钟)")
            print(f"      实时值变化间隔: 中位 {statistics.median(intervals):.1f}s "
                  f"= 约 {statistics.median(intervals)/G:.1f} 个网格")

        # ---------- 保存 ----------
        out = {
            "code": CODE,
            "started": datetime.fromtimestamp(times[0]).isoformat(),
            "clock_offset_local_minus_server": clock_off,
            "n_changes": len(times),
            "duration_s": el,
            "change_times": times,
            "intervals": [times[i] - times[i - 1] for i in range(1, len(times))],
            "grid_period": G,
            "grid_max_error": gerr,
            "grid_candidates": cands,
            "trivial_floor": floor,
            "tolerance": tol,
            "periodogram_top": rs[:20],
        }
        path = "grid_probe_result.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\n      原始数据已存到 {path}")

        # ---------- 判读 ----------
        print("\n" + "=" * 74)
        print("判读")
        print("=" * 74)
        if G:
            med = statistics.median(intervals)
            strong = G >= 1.8 * floor
            print(f"  * 判定网格周期 {G:.1f}s，最大偏差 {gerr:.2f}s")
            print(f"  * 检测下限 {floor:.1f}s —— 该结论"
                  f"{'可信' if strong else '偏弱（接近下限，可能是假阳性）'}")
            print(f"  * 值约每 {med:.0f} 秒才变一次 = 约 {med/G:.1f} 个网格才跳一次")
            print(f"  * 含义：刷新时刻固定，但聚合结果不是每个网格点都更新。")
            if strong:
                print(f"  * 卡点轮询（每 {G:.1f}s 对齐）可把延迟从 ~{med/2:.0f}s 降到 "
                      f"~{G/2:.1f}s，代价是 {med/G:.0f} 倍请求量。")
            print()
            print(f"  [重要限制] 本脚本只能观测「值变化」，观测不到「刷新动作」")
            print(f"  （money_flow 无时间戳/版本号字段）。所以 {G:.1f}s 是「值变化的网格」；")
            print(f"  服务端真实刷新若更细（如 {G/2:.1f}s），而值只在 {G:.1f}s 边界变，")
            print(f"  观测结果完全一样 —— 无法区分。")
        else:
            print("  * 未找到固定网格 → 变化时刻不落在固定墙钟时刻上。")
            print("  * 更可能是「有成交就更新」的事件驱动，而非定时刷新。")
            print(f"  * 若确知有网格，说明容差 {tol:.2f}s 还不够小 —— "
                  f"降低 POLL 再跑（更细采样 = 更小容差 = 更强分辨力）。")
        print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
