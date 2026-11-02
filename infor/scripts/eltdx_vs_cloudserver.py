#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""eltdx vs cloud_server 数值对撞工具

目的：确认 eltdx 能否在**数值口径**上替代 cloud_server 的 tqcenter 公式链路。
      结构覆盖度已确认（36 字段里 eltdx 能产出 31 个），本工具验证口径是否一致。

【为什么必须收盘后跑】
  两边数据源不同、更新节奏不同（公式引擎 vs 7709 协议，后者资金流约 40s 一次）。
  盘中取数会有时间差，无法区分"口径不同"和"取数时刻不同"。
  收盘后两边当日数据都已定稿冻结，比对才干净。

三步用法
--------------------------------------------------------------------------------
  1) 在**采集机**（能访问 merge_server 的那台）导出 cloud_server 侧：
       python eltdx_vs_cloudserver.py --export-cs --out cs.json
     默认走 http://172.17.0.105:5000/query/latest；可用 --merge-url 覆盖。

  2) 在**装了 eltdx 的机器**上导出 eltdx 侧（收盘后）：
       .venv/bin/python eltdx_vs_cloudserver.py --export-eltdx --out eltdx.json

  3) 任意机器上对比：
       .venv/bin/python eltdx_vs_cloudserver.py --compare cs.json eltdx.json

判读
--------------------------------------------------------------------------------
  逐字段算相对误差，并按字段给出结论：
    误差 < 1%   -> 口径一致，可替代
    误差 1~5%   -> 轻微差异，需人工判断是否可接受
    误差 > 5%   -> 口径不同，不可直接替代
  重点看 主力净额 / 四档净额 / 四档主动买卖 这几组——它们是替代的硬门槛。
"""

import argparse
import json
import statistics
import sys
import time
from datetime import datetime

# cloud_server 的 36 个字段（中文键）
CS_FIELDS = [
    "现价", "涨幅", "涨速", "流通市值", "均价", "两分钟金额",
    "超大单净额", "超大单主动买", "超大单主动卖",
    "大单净额", "大单主动买", "大单主动卖",
    "中单净额", "中单主动买", "中单主动卖",
    "小单净额", "小单主动买", "小单主动卖",
    "主力净额", "主动买成交额", "主动卖成交额",
    "开盘金额", "开盘抢筹", "涨停委买额", "成交额", "成交量", "量涨速",
    "换手率", "三日换手率", "昨日涨幅", "昨日成交额",
    "短期形态", "中期形态", "名称", "净成占比", "竞成比",
]

# 替代门槛相关的关键字段（判定权重最高）
KEY_FIELDS = ["主力净额", "超大单净额", "大单净额", "中单净额", "小单净额",
              "超大单主动买", "超大单主动卖", "大单主动买", "大单主动卖",
              "成交额", "现价", "涨幅"]


# ---------------------------------------------------------------- cloud_server 侧
def export_cs(url, codes=None, out="cs.json"):
    import urllib.request
    base = url.rstrip("/")

    def get(path, timeout=60):
        req = urllib.request.Request(base + path,
                                     headers={"User-Agent": "eltdx-compare"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    print(f"[cs] 拉取 {base}/query/codes ...")
    try:
        allc = get("/query/codes")
    except Exception as e:
        print(f"[cs] /query/codes 失败: {type(e).__name__}: {e}")
        allc = None
    code_list = codes or (allc.get("codes") if isinstance(allc, dict) else None) or None
    if not code_list:
        print("[cs] 拿不到代码表，请用 --codes 指定")
        return 1
    print(f"[cs] 代码数 {len(code_list)}")

    data, B = {}, 500
    for i in range(0, len(code_list), B):
        batch = code_list[i:i + B]
        try:
            r = get("/query/latest?codes=" + ",".join(batch))
        except Exception as e:
            print(f"[cs] 批次 {i//B+1} 失败: {type(e).__name__}: {e}")
            continue
        rows = r.get("data") if isinstance(r, dict) else r
        if isinstance(rows, dict):
            data.update(rows)
        elif isinstance(rows, list):
            for row in rows:
                c = row.get("code") or row.get("代码")
                if c:
                    data[c] = row
        print(f"[cs] 批次 {i//B+1}/{(len(code_list)+B-1)//B} 累计 {len(data)}")
        time.sleep(0.2)

    payload = {"source": "cloud_server", "exported_at": datetime.now().isoformat(),
               "merge_url": base, "n": len(data), "data": data}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"[cs] 已写 {out}  ({len(data)} 只)")
    return 0


# ---------------------------------------------------------------- eltdx 侧
def export_eltdx(codes=None, out="eltdx.json", limit=None):
    sys.path.insert(0, "infor/scripts")
    try:
        from workplaces01.github_project.eltdx.infor.scripts.eltdx_tdx_adapter import build
    except ImportError:
        print("[eltdx] 找不到 eltdx_tdx_adapter.py，请在本仓库根目录运行")
        return 1
    from eltdx import TdxClient

    with TdxClient.from_hosts(pool_size=8, server_count=2, probe_hosts=False,
                              timeout=10) as c:
        code_list = codes or list(c.codes.all_a_shares())
        if limit:
            code_list = code_list[:limit]
        print(f"[eltdx] 目标 {len(code_list)} 只")
        names = {s.full_code: s.name for s in c.codes.latest_stock_list()}
        res, reqs = build(c, code_list, names)
        print(f"[eltdx] 产出 {len(res)} 只，分类行情翻页 {reqs} 次")

    payload = {"source": "eltdx", "exported_at": datetime.now().isoformat(),
               "n": len(res), "data": {k: v for k, v in res.items()}}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"[eltdx] 已写 {out}")
    return 0


# ---------------------------------------------------------------- 对比
def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def compare(cs_path, el_path):
    cs = json.load(open(cs_path, encoding="utf-8"))["data"]
    el = json.load(open(el_path, encoding="utf-8"))["data"]
    common = sorted(set(cs) & set(el))
    print(f"cloud_server {len(cs)} 只 / eltdx {len(el)} 只 / 共有 {len(common)} 只")
    if not common:
        print("无交集，检查代码格式（cs 可能是 300207.SZ，eltdx 是 sz300207）")
        return 1

    print(f"\n{'字段':<14}{'样本':>6}{'中位误差%':>11}{'最大误差%':>11}"
          f"{'完全一致':>10}  结论")
    print("-" * 74)
    verdicts = {}
    for fld in CS_FIELDS:
        errs, same, n = [], 0, 0
        for code in common:
            a, b = _num(cs[code].get(fld)), _num(el[code].get(fld))
            if a is None or b is None:
                continue
            n += 1
            if a == b:
                same += 1
            denom = max(abs(a), abs(b), 1e-9)
            errs.append(abs(a - b) / denom * 100)
        if n == 0:
            print(f"{fld:<14}{0:>6}{'--':>11}{'--':>11}{'--':>10}  两侧都无值")
            verdicts[fld] = "无值"
            continue
        med = statistics.median(errs)
        mx = max(errs)
        v = "口径一致" if med < 1 else ("轻微差异" if med < 5 else "口径不同")
        verdicts[fld] = v
        star = " *" if fld in KEY_FIELDS else ""
        print(f"{fld:<14}{n:>6}{med:>11.2f}{mx:>11.2f}{same:>10}  {v}{star}")

    print("\n" + "=" * 74)
    print("关键字段判定（* 标记者是替代的硬门槛）")
    print("=" * 74)
    bad = [f for f in KEY_FIELDS if verdicts.get(f) == "口径不同"]
    mid = [f for f in KEY_FIELDS if verdicts.get(f) == "轻微差异"]
    for f in KEY_FIELDS:
        print(f"  {f:<14} {verdicts.get(f,'--')}")
    print()
    if bad:
        print(f"  >>> 有 {len(bad)} 个关键字段口径不同，不能直接替代：{bad}")
        print("      建议：保留公式链路，或改造下游阈值后重新标定。")
    elif mid:
        print(f"  >>> {len(mid)} 个关键字段有轻微差异，需人工判断是否可接受。")
    else:
        print("  >>> 关键字段口径一致，eltdx 可替代公式链路。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export-cs", action="store_true")
    ap.add_argument("--export-eltdx", action="store_true")
    ap.add_argument("--compare", nargs=2, metavar=("CS_JSON", "ELTDX_JSON"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--merge-url", default="http://172.17.0.105:5000")
    ap.add_argument("--codes", default=None, help="逗号分隔；不传则全市场")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    codes = a.codes.split(",") if a.codes else None

    if a.export_cs:
        return export_cs(a.merge_url, codes, a.out or "cs.json")
    if a.export_eltdx:
        return export_eltdx(codes, a.out or "eltdx.json", a.limit)
    if a.compare:
        return compare(*a.compare)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
