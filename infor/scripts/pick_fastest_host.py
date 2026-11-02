#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主站优选 —— 用真实业务请求测吞吐，挑最快的那台。

为什么需要
--------------------------------------------------------------------------------
2026-09-22 实测：同样配置（10 连接 × 558 只 money_flow），不同主站差 5 倍：

    124.71.187.122   13.5 ~ 17.6 s     快
    123.60.84.66     90.7 s            慢
    81.71.32.47      96.6 s            慢

而 eltdx 自带的 `probe_hosts=True` 只测 **TCP connect 延迟**（默认 1.2s 超时），
**connect 快 != 业务吞吐高** —— 两台 connect 都很快的主站，实际吞吐可能差 5 倍。

所以必须用**真实业务请求**来测。

用法
--------------------------------------------------------------------------------
    # 探测并打印排名（结果落盘缓存）
    .venv/bin/python infor/scripts/pick_fastest_host.py

    # 指定候选数量 / 每台测多少请求 / 测哪类请求
    .venv/bin/python infor/scripts/pick_fastest_host.py --candidates 8 --sample 20

    # 只要最快那台，直接打印成可用的 host 串
    .venv/bin/python infor/scripts/pick_fastest_host.py --best-only

作为库使用
--------------------------------------------------------------------------------
    from pick_fastest_host import pick_fastest_host, make_pinned_clients

    host, report = pick_fastest_host(sample=20)
    clients = make_pinned_clients(host, 10)      # 10 条连接全固定到这台
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import date, datetime

from eltdx import TdxClient

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     ".fastest_host.json")

# 探针用哪种请求。money_flow 是最慢的业务（~43ms/只），
# 用它能放大主站差异；quotes.refresh 快得多，适合要求快速探测的场景。
WORKLOADS = {
    "money_flow": lambda c, codes: [c.money_flow.daily(cd) for cd in codes],
    "quotes": lambda c, codes: [c.quotes.refresh(cd, cursors={}) for cd in codes],
}


def _candidate_hosts(n):
    """取候选主站。用 eltdx 自己的测速排名做初筛（它至少能筛掉连不上的）。"""
    with TdxClient(timeout=8) as probe:
        ranked = list(probe.transport.hosts)
    return ranked[:n]


def _probe_one(host, codes, workload, timeout):
    """测一台主站的真实吞吐。返回 (host, 只/秒, 耗时, 错误数)。"""
    fn = WORKLOADS[workload]
    try:
        with TdxClient.from_hosts(hosts=[host], server_count=1,
                                  probe_hosts=False, timeout=timeout) as c:
            fn(c, codes[:1])                       # 预热（含建连握手）
            t0 = time.perf_counter()
            errs = 0
            for cd in codes:
                try:
                    fn(c, [cd])
                except Exception:
                    errs += 1
            el = time.perf_counter() - t0
    except Exception as e:
        return (host, 0.0, float("inf"), len(codes), f"{type(e).__name__}: {e}")
    return (host, len(codes) / el if el else 0.0, el, errs, None)


def pick_fastest_host(candidates=8, sample=20, workload="money_flow",
                      timeout=10, verbose=True):
    """探测候选主站，返回 (最快host, 完整报告)。

    报告是 list[dict]，按吞吐降序，含 host/rate/elapsed/errors/error。
    """
    if workload not in WORKLOADS:
        raise ValueError(f"workload 必须是 {list(WORKLOADS)}")

    hosts = _candidate_hosts(candidates)
    with TdxClient(timeout=8) as tmp:
        codes = list(tmp.codes.all_a_shares()[:sample])

    if verbose:
        print(f"探测 {len(hosts)} 台主站   请求类型={workload}   "
              f"每台 {len(codes)} 个请求\n")
        print(f"{'主站':<24}{'只/秒':>9}{'耗时s':>9}{'错误':>6}  备注")
        print("-" * 62)

    report = []
    for h in hosts:
        host, rate, el, errs, err = _probe_one(h, codes, workload, timeout)
        report.append({"host": host, "rate": round(rate, 2),
                       "elapsed": round(el, 3), "errors": errs, "error": err})
        if verbose:
            flag = err or ""
            if not err and rate > 0:
                best = max(r["rate"] for r in report)
                if rate == best and len(report) > 1:
                    flag = "<- 目前最快"
            print(f"{host:<24}{rate:>9.1f}{el:>9.2f}{errs:>6}  {flag}")

    report.sort(key=lambda r: -r["rate"])
    ok = [r for r in report if r["rate"] > 0]
    best = ok[0]["host"] if ok else None

    if verbose:
        print()
        if best and len(ok) > 1:
            slow = ok[-1]["rate"]
            print(f"最快 {best}  {ok[0]['rate']:.1f} 只/秒")
            print(f"最慢 {ok[-1]['host']}  {slow:.1f} 只/秒")
            if slow > 0:
                print(f"快慢差 {ok[0]['rate']/slow:.1f} 倍")
        elif best:
            print(f"只有一台可用: {best}")

    _save_cache(report, workload)
    return best, report


def _save_cache(report, workload):
    try:
        json.dump({"date": str(date.today()),
                   "measured_at": datetime.now().isoformat(),
                   "workload": workload, "report": report},
                  open(CACHE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass


def cached_host(max_age_hours=2.0):
    """读缓存的最快主站。超过 max_age_hours 小时返回 None（需重测）。"""
    try:
        d = json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        return None
    try:
        age = (datetime.now()
               - datetime.fromisoformat(d["measured_at"])).total_seconds() / 3600
    except Exception:
        return None
    if age > max_age_hours:
        return None
    rep = [r for r in d.get("report", []) if r.get("rate", 0) > 0]
    return rep[0]["host"] if rep else None


def make_pinned_clients(host, n, timeout=20):
    """开 n 条连接，全部固定到同一台主站。

    ⚠️ 必须全部固定到**同一台** —— 否则跨连接的时刻偏差会让板块聚合
       （把多只股票相加）混入不同时刻的数据。
    """
    return [TdxClient.from_hosts(hosts=[host], server_count=1,
                                 probe_hosts=False, timeout=timeout)
            for _ in range(n)]


def sweep_parallel(clients, codes, fn=None):
    """用 n 条固定连接并行扫全市场。

    fn(client, code) 默认 money_flow.daily。返回 {code: 值}。
    每条连接负责 codes[i::n]，每只股票只属于一条连接（避免 A/B 交替）。
    """
    import threading
    n = len(clients)
    chunks = [codes[i::n] for i in range(n)]
    out = [{} for _ in range(n)]

    if fn is None:
        def fn(c, cd):
            return c.money_flow.daily(cd).records[0].total_amount

    def work(i, cl, cs):
        for cd in cs:
            try:
                out[i][cd] = fn(cl, cd)
            except Exception:
                pass

    ths = [threading.Thread(target=work, args=(i, clients[i], chunks[i]))
           for i in range(n)]
    t0 = time.perf_counter()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    res = {}
    for d in out:
        res.update(d)
    return res, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=int, default=8,
                    help="探测几台候选（默认 8）")
    ap.add_argument("--sample", type=int, default=20,
                    help="每台测多少个请求（默认 20）")
    ap.add_argument("--workload", default="money_flow", choices=list(WORKLOADS))
    ap.add_argument("--timeout", type=float, default=10)
    ap.add_argument("--best-only", action="store_true",
                    help="只打印最快那台的 host 串")
    a = ap.parse_args()

    best, report = pick_fastest_host(a.candidates, a.sample, a.workload,
                                     a.timeout, verbose=not a.best_only)
    if a.best_only:
        if best:
            print(best)
            return 0
        print("无可用主站", file=sys.stderr)
        return 1
    print(f"\n缓存已写入 {CACHE}")
    print(f"最快主站: {best}")
    print(f"\n用法:")
    print(f"    host, _ = pick_fastest_host()")
    print(f"    clients = make_pinned_clients(host, 10)")
    print(f"    data, secs = sweep_parallel(clients, codes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
