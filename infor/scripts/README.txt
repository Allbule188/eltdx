================================================================================
eltdx 实测脚本索引
================================================================================
来源      : 2026-09-17 ~ 2026-09-18 的 eltdx 实测，从 /tmp 转存
配套报告  : ../eltdx-实测与使用要点.txt
================================================================================

运行方式
--------------------------------------------------------------------------------
所有脚本都依赖已安装 eltdx 的虚拟环境：

  cd /Users/allbule/Developer/PythonProject/eltdx
  .venv/bin/python infor/scripts/<脚本名>.py

不要用系统 python3 直接跑（会 ModuleNotFoundError: No module named 'eltdx'）。

注意事项
--------------------------------------------------------------------------------
* 所有脚本都连真实通达信主站，会产生真实网络请求。
* 标注【盘中】的脚本，在非交易时段跑出来的数字没有参考价值
  （盘前/盘中速率差 1.8 倍，变化率差 24 倍）。
* 交易时段：09:30-11:30, 13:00-15:00（集合竞价 09:15-09:25）。
* 部分脚本对主站压强较大（全市场类每轮 56 个请求），
  别长时间反复跑，对免费公共服务器不礼貌。


脚本清单
--------------------------------------------------------------------------------

【并发 / 性能类】

eltdx_bench.py
  首版并发压测。6 种 pool_size / 线程组合，测 wall / cpu / 并行度 / p50 / p99。
  用法: .venv/bin/python infor/scripts/eltdx_bench.py 200     (200=股票数)

eltdx_bench2.py
  线程数越过核数（1/8/16/32/64）+ 批量 vs 单条对比。
  暴露了单次取样噪声大的问题（64线程跑出 39x 的不可信结果）。

eltdx_bench3.py
  加重复取样取中位数降噪 + 单次调用延迟 vs 批量大小。
  得到干净的线程扩展曲线（32线程/8核 = 31.97x）。

eltdx_bench4.py
  生产配置：全市场 5574 只分批拉取，对比顺序/4/8/16 线程。

【五档盘口类】

eltdx_depth.py
  单只股票（sz300207）完整五档 + 委买委卖/委差委比/内外盘。

eltdx_10x5.py
  10 轮五档 + 同轮内对比 1档(get_snapshots) vs 5档(full_quotes) 速度。

【全市场类】

eltdx_fullmarket.py
  全市场五档：refresh() 单趟 vs full_quotes() 双趟路径对比，
  含连续 5 轮扫描 + tracemalloc 内存测量。

eltdx_10sweeps.py
  全市场五档 × 10 轮速率测试，逐轮打印耗时/吞吐/覆盖/5档完整性/变化只数/内存。
  这个是测速率的主力脚本。建议盘中跑。

【数据粒度类 —— 本次最重要的发现】

eltdx_tick.py
  测 TDX 最小刷新粒度：0.25s 高频轮询 10 只活跃股 60 秒，
  观察服务端时间戳 update_time_raw 和版本号 active 的跳动。
  结论: 中位间隔 3027ms => 3 秒。

eltdx_recall.py
  测最小不丢数据的轮询周期。0.2s 基线采样 90 秒拿全部真实更新，
  再模拟 0.2/0.5/1/2/2.5/3/3.5/4/5 秒各周期的召回率和重复率。
  结论: 3 秒召回 93.9%、重复率 2%，是最优；>3.5 秒断崖。

eltdx_ticks.py
  逐笔成交(0x0fc5)的更新粒度，独立于快照协议验证。
  结论: 同样 3 秒（中位 3000ms），且时间戳只到分钟。

【资金流 money_flow 类】

eltdx_mf.py
  测 money_flow.daily 能否盘中更新。5s 采样跨过 11:30 上午收盘边界，
  观察总额/主力净额是否变化、收盘后何时定稿。
  【注意】必须盘中跑，非交易时段全程 0 变化。

eltdx_mftime.py
  逐字段追踪 money_flow 的变化时刻。印证 13 个数值字段 + buckets
  是否同步更新（结论：完全同步，不存在独立更新）。

eltdx_cmp.py
  同时采快照(0x0547) 和资金流(0x0ffc)，比较两者更新时刻。
  【注意】默认 pool_size=2 会引入 A/B 交替干扰，要用 pool_size=1 才准。

eltdx_mfrecon.py
  【测资金流刷新间隔的主力脚本】捕获变化点 + 逐位 diff raw/buckets 找计数器。
  已内置 pool_size=1。2026-09-21 用它测出：资金流中位 39.88s，快照 2.93s。
  同时会报告 8 个分档字段的同步性，以及 raw 里有无隐藏计数器。

eltdx_grid_probe.py
  【验证刷新是否落在固定墙钟网格上】2026-09-21 的遗留问题。
  0.1s 高频采样 + 圆统计 + GCD 式网格拟合，判定网格周期与相位稳定性。
  内置时钟校准（用快照 update_time_raw 对齐服务端时钟）。

  用法:
    .venv/bin/python infor/scripts/eltdx_grid_probe.py --selftest   # 先自检判据
    .venv/bin/python infor/scripts/eltdx_grid_probe.py 15           # 采 15 分钟

  [重要] 判据自检必须先过。开发这个脚本时踩过一个坑:
         容差 tol 的"平凡拟合下限"= 2×tol —— 任何小于该值的周期都能
         平凡拟合任意间隔。曾用 tol=1.5 得出"5秒网格"，实际下限 3.3s，
         5s 只是略高于下限的弱信号。现默认 tol=0.8（对应 0.1s 采样）。
         混用不同采样精度的数据也会污染结果，别把 0.5s 采的旧数据
         和 0.1s 采的新数据合并分析。


【主站优选（每次运行前跑）】

pick_fastest_host.py
  【重要】实测主站之间速度差可达 5 倍，选主站比优化架构更值钱。
  而 eltdx 自带的 probe_hosts 只测 TCP connect 延迟，connect 快 != 吞吐高，
  所以本脚本用**真实业务请求**测吞吐来挑主站。

  用法:
    .venv/bin/python infor/scripts/pick_fastest_host.py                # 探测+排名+缓存
    .venv/bin/python infor/scripts/pick_fastest_host.py --best-only    # 只输出最快那台

  作为库用（推荐，含配套的并行扫描）:
    from pick_fastest_host import (pick_fastest_host, cached_host,
                                   make_pinned_clients, sweep_parallel)
    host, report = pick_fastest_host(sample=20)      # 或 cached_host() 读缓存
    clients = make_pinned_clients(host, 10)          # 10 条连接全固定到这台
    data, secs = sweep_parallel(clients, codes)      # 全市场并行扫

  实测效果: 全市场 5575 只 money_flow，10 连接固定分工 -> 16~19 秒/轮
            （串行 240 秒/轮，加速 14.8x），覆盖 100%，A/B 回退 0 次。

  [两条硬约束]
    1. N 条连接必须全部固定到**同一台**主站 —— 否则跨连接的时刻偏差
       会让板块聚合（把多只股票相加）混入不同时刻的数据。
    2. 主站负载随时在变，缓存默认 2 小时过期；长跑任务应定期重测。


【代理 —— 让 7709 走代理】

tdx_proxy_relay.py
  【背景】eltdx **不支持代理**（已查源码：TdxClient/from_hosts 无 proxy 参数，
  Rust 引擎直接建 TcpStream；源码里的 "PinnedTransportProxy" 是"固定连接槽"
  不是网络代理）。只有 F10(7615) 走 urllib，会读 HTTPS_PROXY。

  但实测 eltdx **接受任意 host:port（含 127.0.0.1）**，所以用本地中转即可，
  **不用改 eltdx 一行代码**：

      N 个 TdxClient ──> 127.0.0.1:17709 ──[SOCKS5/HTTP代理]──> 主站:7709

  用法:
    pip install pysocks                       # SOCKS5 需要
    python infor/scripts/tdx_proxy_relay.py --proxy socks5://user:pass@1.2.3.4:1080
    python infor/scripts/tdx_proxy_relay.py --proxy http://1.2.3.4:8080
    python infor/scripts/tdx_proxy_relay.py --target 43.139.95.83:7709   # 直连映射

  然后:
    TdxClient(host="127.0.0.1:17709", probe_hosts=False, timeout=20)

  实测: 10 连接经 relay 跑全市场 21.9~23.3s/轮，直连同主站 22.2s
        → **relay 开销在噪声范围内**。4 条并发转发正常。

  [注意] 每条 eltdx 连接 = 一条出站代理连接，40 连接就是 40 条出站。
        走代理必然多一跳延迟；但主站之间本来就差 5 倍，代理选得好可能反而更快。


【网关 / 推送类】

eltdx_ws.py
  WebSocket quotes.subscribe 实测。需要先启动网关：
      .venv/bin/pip install "eltdx[http]"
      .venv/bin/eltdx-http --port 8000 &
  然后跑本脚本，观察 30 秒内能收到多少 quote 事件。
  结论: 只收到订阅基线，0 条增量事件。

  单独验证推送队列（不依赖网关）的最小片段：
      from eltdx import TdxClient
      with TdxClient(timeout=8) as c:
          c.quotes.refresh(["sz300207"], cursors={})
          print(c.transport.diagnostics.push_frames)   # 始终为 0


如何自己验证多连接不一致（pool_size 陷阱）
--------------------------------------------------------------------------------
对照跑两次，看 A/B 交替次数：

  from eltdx import TdxClient
  import time

  def probe(pool):
      with TdxClient.from_hosts(pool_size=pool, server_count=1,
                                probe_hosts=False, timeout=8) as c:
          c.money_flow.daily("sz300207")
          vals, t0 = [], time.perf_counter()
          while time.perf_counter() - t0 < 25:
              vals.append(c.money_flow.daily("sz300207").records[0].total_amount)
              time.sleep(0.5)
      alt = sum(1 for i in range(2, len(vals))
                if vals[i] == vals[i-2] and vals[i] != vals[i-1])
      print(f"pool_size={pool}  采样{len(vals)}  不同值{len(set(vals))}  "
            f"A/B交替 {alt} 次")

  probe(1)
  probe(2)

  预期: pool_size=1 交替接近 0；pool_size=2 会出现明显交替。
  【必须盘中跑】非交易时段数据冻结，两者都是 0，测不出东西。


如何自己验证推送是否恢复
--------------------------------------------------------------------------------
如果哪天 eltdx 或主站行为变了，用这个片段最快确认：

  from eltdx import TdxClient
  import time
  with TdxClient(timeout=8) as c:
      codes = list(c.codes.all_a_shares()[:100])
      c.quotes.refresh(codes, cursors={})
      t0 = time.perf_counter()
      while time.perf_counter() - t0 < 60:
          f = c.quotes.poll_push(timeout=0.5, parse=True)
          if f:
              print("收到推送!", f)
          time.sleep(0.1)
      print("push_frames =", c.transport.diagnostics.push_frames)

  60 秒后 push_frames 仍为 0 => 推送依然不可用。
  非 0 => 情况变了，值得重新评估架构。

================================================================================
