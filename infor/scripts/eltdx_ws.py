#!/usr/bin/env python
"""WebSocket quotes.subscribe 实测：原生增量推送 vs 2秒轮询。"""
import asyncio
import json
import time
from collections import defaultdict

import websockets

CODES = ["sz000001", "sh600519", "sz300750", "sz300207", "sh601318"]
DURATION = 30.0

events = defaultdict(list)      # code -> [(t, price, total_hand)]
first_push = {}
sub_id = None


def unpack(msg):
    """从推送 data 里取出代码/价格/总量，兼容不同字段形态。"""
    if not isinstance(msg, dict):
        return None
    code = msg.get("full_code") or msg.get("code") or msg.get("symbol")
    if code and code.isdigit() and msg.get("exchange"):
        code = f"{msg['exchange']}{code}"
    price = msg.get("last_price")
    hand = msg.get("total_hand")
    return code, price, hand


async def main():
    global sub_id
    async with websockets.connect("ws://127.0.0.1:8000/ws", max_size=None,
                                  ping_interval=None) as ws:
        await ws.send(json.dumps({
            "id": 1, "method": "quotes.subscribe", "params": {"codes": CODES}
        }))

        t_start = time.perf_counter()
        deadline = t_start + DURATION
        n_resp = n_push = 0

        while time.perf_counter() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.perf_counter())
            except asyncio.TimeoutError:
                break
            t = time.perf_counter() - t_start
            msg = json.loads(raw)

            if msg.get("event") == "quote":
                n_push += 1
                d = msg.get("data")
                if isinstance(d, list):
                    for item in d:
                        r = unpack(item)
                        if r and r[0]:
                            events[r[0]].append((t, r[1], r[2]))
                            first_push.setdefault(r[0], item)
                else:
                    r = unpack(d)
                    if r and r[0]:
                        events[r[0]].append((t, r[1], r[2]))
                        first_push.setdefault(r[0], d)
            else:
                n_resp += 1
                if msg.get("id") == 1:
                    res = msg.get("result") or {}
                    sub_id = res.get("subscription_id")
                    print(f"订阅响应: ok={msg.get('ok')}  sub_id={sub_id}  "
                          f"codes={res.get('codes')}")
                    init = res.get("initial")
                    print(f"  基线 initial 类型={type(init).__name__} "
                          f"{'条数=' + str(len(init)) if hasattr(init,'__len__') else ''}")

        print(f"\n{DURATION:.0f}s 内收到: {n_push} 条 quote 事件, {n_resp} 条 RPC 响应")
        if first_push:
            c0 = next(iter(first_push))
            print(f"\n首条推送 data 字段 ({c0}):")
            for k, v in list(first_push[c0].items())[:22]:
                sv = repr(v)
                print(f"   {k:<22} = {sv[:60]}")

        print(f"\n{'代码':<10} {'推送次数':>8} {'去重后':>7} {'平均间隔ms':>11} "
              f"{'2s轮询会看到':>13} {'漏掉':>7}")
        print("-" * 66)
        tot_push = tot_poll = 0
        for code in CODES:
            ev = events.get(code, [])
            if not ev:
                print(f"{code:<10} {0:>8}  (无推送)")
                continue
            # 去重：价格或总量变化才算一次真更新
            uniq, last = [], None
            for t, p, h in ev:
                if (p, h) != last:
                    uniq.append((t, p, h))
                    last = (p, h)
            # 模拟 2 秒轮询：每 2 秒取最近一次观测
            seen, last = set(), -1
            for t, p, h in uniq:
                bucket = int(t // 2)
                if bucket != last:
                    seen.add((p, h))
                    last = bucket
            gaps = [uniq[i][0] - uniq[i - 1][0] for i in range(1, len(uniq))]
            avg = sum(gaps) / len(gaps) * 1000 if gaps else 0
            tot_push += len(uniq); tot_poll += len(seen)
            print(f"{code:<10} {len(ev):>8} {len(uniq):>7} {avg:>11.0f} "
                  f"{len(seen):>13} {len(uniq)-len(seen):>7}")

        if tot_poll:
            print(f"\n合计: 推送看到 {tot_push} 次真实更新, 2秒轮询只能看到 {tot_poll} 次 "
                  f"→ 漏掉 {tot_push-tot_poll} 次 ({(1-tot_poll/tot_push)*100:.0f}%)")


asyncio.run(main())
