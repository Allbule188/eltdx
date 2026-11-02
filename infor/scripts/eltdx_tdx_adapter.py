#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""eltdx -> cloud_server tdx_data (36 字段) 适配器

目标：用 eltdx 产出 cloud_server 现有 `tdx_data` 表的 36 个字段，
      替代 tqcenter 公式引擎（`L2资金四档`）那条链路。

字段来源三层：
  L1  单次调用      quotes.refresh()       -> 现价/成交额/成交量/开盘金额/五档
  L2  单次调用      money_flow.daily()     -> 四档净额 + (buckets 自算)四档主动买卖/主力净额
  L3  全市场翻页    list_by_category()     -> 涨速/量涨速/两分钟金额/开盘抢筹  ⚠️ 70 次请求
  L4  低频/本地算   bars / finance         -> 昨日涨幅/昨日成交额/流通市值/换手率/均价

未覆盖（公式专有）：
  短期形态 / 中期形态   —— 通达信公式枚举，eltdx 无。按盘点结论这两个字段零策略依赖，
                          可置空字符串 "" 降级（数据库页两列空白）。

用法：
    .venv/bin/python infor/scripts/eltdx_tdx_adapter.py sz300207 sh600519
    .venv/bin/python infor/scripts/eltdx_tdx_adapter.py --full-market   # 全市场(慢)
"""

import sys
import time
from datetime import date, datetime

from eltdx import TdxClient

# cloud_server 的目标字段（顺序与其建表一致）
TARGET_FIELDS = [
    "现价", "涨幅", "涨速", "流通市值", "均价", "两分钟金额",
    "超大单净额", "超大单主动买", "超大单主动卖",
    "大单净额", "大单主动买", "大单主动卖",
    "中单净额", "中单主动买", "中单主动卖",
    "小单净额", "小单主动买", "小单主动卖",
    "主力净额", "主动买成交额", "主动卖成交额",
    "开盘金额", "开盘抢筹", "涨停委买额", "成交额", "成交量", "量涨速",
    "换手率", "三日换手率", "昨日涨幅", "昨日成交额",
    "短期形态", "中期形态",
    "名称", "净成占比", "竞成比",
]

# 主力口径四档在 buckets 里的买卖对下标
TIER_BUCKETS = {
    "超大单": (0, 1), "大单": (4, 5), "中单": (8, 9), "小单": (12, 13),
}
TIER_NET_FIELD = {
    "超大单": "main_super_large_net", "大单": "main_large_net",
    "中单": "main_medium_net", "小单": "main_small_net",
}

CATEGORY = "沪深A股"
PAGE = 80                      # 0x054b 硬上限，传更大也只返回 80


def fetch_category_fields(client, need_codes=None):
    """翻页 list_by_category 拿 涨速/量涨速/两分钟金额/开盘抢筹。

    ⚠️ 这是排序榜单，不能按代码过滤 —— 要指定股票就必须翻完。
    need_codes 给定时，集齐即提前退出（但榜单顺序可能让目标靠后）。
    """
    out, start, reqs = {}, 0, 0
    while True:
        pg = client.quotes.list_by_category(category=CATEGORY, start=start,
                                            count=PAGE)
        reqs += 1
        recs = pg.records
        if not recs:
            break
        for r in recs:
            out[r.full_code] = {
                "涨速": r.rise_speed,
                "量涨速": r.vol_rise_speed,
                "两分钟金额": r.min2_amount,
                "开盘抢筹": r.opening_rush,
            }
        start += len(recs)
        if need_codes and need_codes <= set(out):
            break
    return out, reqs


def build(client, codes, name_map=None):
    """产出 {full_code: {36字段}}。"""
    codes = list(codes)
    res = {}

    # ---- L2: 四档资金 ----
    mf = client.money_flow.daily(codes if len(codes) > 1 else codes[0])
    blocks = mf.blocks if hasattr(mf, "blocks") else [mf]
    for blk in blocks:
        r = blk.records[0]
        b, ta = r.buckets, r.total_amount
        row = {
            "超大单净额": r.main_super_large_net,
            "大单净额": r.main_large_net,
            "中单净额": r.main_medium_net,
            "小单净额": r.main_small_net,
            "主力净额": r.main_net,
        }
        # 【口径说明 / 重要】
        # 实测 buckets 是**双边记账**：Σ偶位 ≈ Σ奇位 ≈ 成交额（比值 1.0000）。
        # 即每笔成交在买方档位和卖方档位**各记一次**，两侧合计都等于成交额。
        # 这与 A 股「主动买/主动卖（外盘/内盘）」的**互斥划分**不是一回事
        # （内外盘互斥，合计=总成交量，见 quotes.refresh 的 inside_dish/outer_disc）。
        #
        # 所以下面产出的是「买方档位金额 / 卖方档位金额」，**不是**真主动买/主动卖。
        # 四档净额（净额=偶-奇）是可靠的，已逐档校验与 SDK 字段一致。
        # 真主动口径需要按 aggressor 侧分类再分档，0x0ffc 协议给不了 —— 见文末说明。
        buy_sum = sell_sum = 0.0
        for cn, (bi, si) in TIER_BUCKETS.items():
            buy = b[bi] / 50000 * ta
            sell = b[si] / 50000 * ta
            row[f"{cn}主动买"] = buy          # 实为「买方档位金额」，见上
            row[f"{cn}主动卖"] = sell         # 实为「卖方档位金额」
            row[f"{cn}_买方双边"] = buy
            row[f"{cn}_卖方双边"] = sell
            buy_sum += buy
            sell_sum += sell
        # 这两个恒等于成交额，无信息量；保留仅为兼容 schema，值置 None 更诚实
        row["主动买成交额"] = None
        row["主动卖成交额"] = None
        res[blk.full_code] = row

    # ---- L1: 五档 + 行情 ----
    for q in client.quotes.refresh(codes, cursors={}).records:
        row = res.setdefault(q.full_code, {})
        pre = q.last_close_price or 0
        row["现价"] = q.last_price
        row["涨幅"] = (q.last_price - pre) / pre * 100 if pre else None
        row["成交额"] = q.amount
        row["成交量"] = q.total_hand                      # 手
        row["开盘金额"] = q.open_amount_yuan
        bl = q.buy_levels[0] if q.buy_levels else None
        row["涨停委买额"] = bl.price * bl.volume * 100 if bl else None
        # 真主动口径只能在这里拿到**总量**（不分档）：内盘/外盘，单位手。
        # cloud_server 的「主动买/卖成交额」若是外盘/内盘口径，可用
        #   主动买 ≈ outer_disc × 100 × 均价   （近似，协议只给手数不给金额）
        row["_外盘手"] = q.outer_disc
        row["_内盘手"] = q.inside_dish
        row["_pre_close"] = pre
        row["_amount"] = q.amount
        row["_hand"] = q.total_hand

    # ---- L4: 昨日数据（日K）----
    for code in codes:
        try:
            ks = client.bars.get(code, period="day", count=2)
            if len(ks.bars) >= 2:
                y = ks.bars[-2]
                res.setdefault(code, {})["昨日涨幅"] = (
                    (y.close - y.open) / y.open * 100 if y.open else None)
                res.setdefault(code, {})["昨日成交额"] = y.amount
            # 均价 = 成交额 / 成交量(股)
            row = res.setdefault(code, {})
            if row.get("_amount") and row.get("_hand"):
                row["均价"] = row["_amount"] / (row["_hand"] * 100)
        except Exception:
            pass

    # ---- L4: 流通股本 -> 流通市值/换手率 ----
    try:
        fin = client.corporate.finance_batch(codes)
        for rec in fin.records:
            row = res.setdefault(rec.full_code, {})
            cs = rec.circulating_shares
            if cs:
                px = row.get("现价")
                row["流通市值"] = cs * px if px else None
                hand = row.get("_hand")
                row["换手率"] = hand * 100 / cs * 100 if hand else None
    except Exception as e:
        print(f"  [warn] finance_batch 失败: {type(e).__name__}: {e}", file=sys.stderr)

    # ---- L3: 分类行情（涨速等）----
    cat, reqs = fetch_category_fields(client, set(res))
    for code, row in res.items():
        if code in cat:
            row.update(cat[code])

    # ---- 派生 + 补齐 ----
    for code, row in res.items():
        amt = row.get("成交额")
        mn = row.get("主力净额")
        oa = row.get("开盘金额")
        pa = row.get("昨日成交额")
        row["净成占比"] = round(mn / amt * 100, 2) if mn is not None and amt else None
        row["竞成比"] = round(oa / pa * 100, 2) if oa is not None and pa else None
        row["短期形态"] = ""            # 公式专有，降级为空
        row["中期形态"] = ""
        row["三日换手率"] = None        # 需 3 日历史，未实现
        # 真主动口径近似（外盘/内盘 × 均价），供与 cloud_server 对撞用
        px = row.get("均价")
        if px and row.get("_外盘手") is not None:
            row["_主动买成交额_近似"] = row["_外盘手"] * 100 * px
            row["_主动卖成交额_近似"] = row["_内盘手"] * 100 * px
        if name_map:
            row["名称"] = name_map.get(code)
        for k in ("_pre_close", "_amount", "_hand", "_外盘手", "_内盘手"):
            row.pop(k, None)
    return res, reqs


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--full-market" in sys.argv

    t0 = time.time()
    with TdxClient.from_hosts(pool_size=4, server_count=2, probe_hosts=False,
                              timeout=10) as c:
        if full:
            codes = list(c.codes.all_a_shares())
            print(f"全市场 {len(codes)} 只")
        elif args:
            codes = [a if a[:2] in ("sz", "sh", "bj") else
                     ("sh" if a.startswith(("6", "5")) else "sz") + a
                     for a in args]
        else:
            codes = ["sz300207", "sh600519", "sz300001"]

        names = {s.full_code: s.name for s in c.codes.latest_stock_list()}
        res, reqs = build(c, codes, names)

    el = time.time() - t0
    print(f"\n产出 {len(res)} 只 / {el:.2f}s / 分类行情翻页 {reqs} 次\n")
    print("=" * 100)
    for code in codes[:6]:
        row = res.get(code, {})
        print(f"\n{code}  {row.get('名称','')}")
        print(f"  {'字段':<12}{'值':>20}   {'字段':<12}{'值':>20}")
        print("  " + "-" * 72)
        ks = TARGET_FIELDS
        for i in range(0, len(ks), 2):
            cells = []
            for k in ks[i:i + 2]:
                v = row.get(k)
                s = f"{v:,.4f}" if isinstance(v, float) else str(v)
                cells.append(f"{k:<12}{s:>20}")
            print("  " + "   ".join(cells))

    missing = [k for k in TARGET_FIELDS
               if all(res.get(cd, {}).get(k) in (None, "") for cd in res)]
    print(f"\n完全无值的字段: {missing if missing else '无'}")


if __name__ == "__main__":
    main()
