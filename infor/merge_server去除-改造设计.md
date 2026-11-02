# 去掉 merge_server —— 改造设计

> 设计文档 · 2026-09-22
> 前置：`eltdx替代cloud_server-TDX链路-计划.md`（采集端替换，已完成）
> 状态：**§5.1 抽库 + §5.2/5.3 接线全部完成并验证**（开关默认关闭，设 `TDX_STORE_INPROC=1` 启用）

---

## 实施进度

| 步骤 | 状态 | 说明 |
| --- | --- | --- |
| §5.1 抽库 `tdx_store.py` | ✅ **已完成** | merge_server 1403 → 薄壳 250 + 核心 1286；18 项回归全绿 |
| 薄壳保留 HTTP 全部行为 | ✅ 已完成 | 11 个端点端到端冒烟通过（含门控 422、轮次水位、日志回环） |
| §5.2 采集端直连写库 | ✅ **已完成** | `--store-path` / `TDX_STORE_PATH`；`upload()` 走 `write_round()` |
| §5.3 cloud_server 进程内查询 | ✅ **已完成** | `tdx_inproc.py` + `TDX_STORE_INPROC=1`；6 个方法各加分支 |
| 两份 merge_server 收敛 | ⚠️ **待定** | 见下 |

### 接线新增的三个实现要点

1. **开关默认关闭**：`TDX_STORE_INPROC` 不设或不为 `1` 时，**完全走原 HTTP 路径、
   行为零变化**。`tdx_inproc.inproc_enabled()` 只认显式开关 —— 不自动探测
   "URL 是不是本机"，因为 127.0.0.1 / 172.17.x / 主机名 / 端口转发都会误判。

2. **模块实例必须唯一**：采集端按平铺名 `import tdx_store`，而 `tdx_inproc` 走包内
   导入 `from ...tdx_bridge import tdx_store` —— 两者拿到的是**两个不同的模块对象**，
   模块级内存态（轮次水位 / 代码缓存 / 日志环）会分裂。已在 `get_store()` 开头
   优先复用 `sys.modules["tdx_store"]`。

3. **跨进程状态必须能落库**：采集端与 cloud_server 是**两个进程**，采集端直写后
   它的内存态对 cloud_server 不可见。`query_round_watermark` 已加数据库回退
   （`SELECT MAX(source_collected_at) FROM tdx_data WHERE trade_date=?`）——
   按轮写入、秒级精度，正是水位需要的粒度。**其余模块级内存态（见下）需要同样审视。**

### 独立审计发现并修掉的缺陷（2026-09-22）

接线完成后跑了一轮独立审计，找到 12 条，其中 4 条严重且**都是接线引入的**：

| # | 缺陷 | 修法 |
| --- | --- | --- |
| 1 | **状态码被丢弃** —— 分支写成 `body, _ = store.xxx()`，把 `({"error":...}, 503)` 当成"查询成功、结果为空" | 新增 `tdx_inproc.call()` 把 `(body,status)` 归一化为 `(body, ok)`；6 个分支全部改用它 |
| 2 | **负缓存** —— `TdxDataReader` 失败批次被写进 TTL 缓存，`ttl` 内一直"没有数据"（HTTP 路径是**不缓存、下轮重试**） | 失败返回 `None`，与 HTTP 一致 |
| 3 | **异常语义不对称** —— `query_window` 等 4 个函数 store 内无兜底，HTTP 由 Flask 转 503，进程内会抛进策略代码 | `call()` 统一收敛为 `ok=False` |
| 4 | **`sys.modules` 注册时机** —— 先注册再 `exec`，exec 失败会留下**半初始化模块**毒化平铺名 | 抽 `_load_by_path()`，注册移到 exec 成功之后 |
| 5 | 水位回退的 `MAX()+COUNT(*)` 无索引，却被 3 个服务以 **2 秒**间隔轮询 | 改用走 `(trade_date, updated_at)` 索引的 `ORDER BY ... LIMIT 1` |
| 6 | `_start_legacy_dates_scan()` 只在 merge_server 的 `__main__` 里调用，进程内模式下**永不播种** → 漏掉只存在于 legacy 的交易日 | `get_store()` 加载成功后补调 |
| 7 | `_codes_cache` 在 cloud_server 侧**永久冻结**（只有 `write_round` 会 `_note_codes`） | 加 300s TTL 重新播种 |
| 8 | `compress_window_response` 抽离时丢了 `Vary: Accept-Encoding` | 补回 |
| 9 | 失败后 `_failed=True` 永不复位，"开关开着"是假象 | 加 60s 冷却后重试 |

补了 **23 项进程内回归**（`test/test_tdx_inproc.py`）—— 上述 1~4 条此前**零覆盖**，
正是它们没被发现的原因。全量对比：**两种模式均 13 failed（与基线一致）/ 230 passed**。

### 已知残留（未修）

- **点号名 vs 点号名**仍可能有两个实例：`from ...tdx_bridge import tdx_store` 的目标
  命名空间跟着调用方自己的模块前缀走。生产两个入口都以 `workplaces01.` 为锚，
  所以实际只落一个；但这是设计上的残留风险。
- **入库日志流**：`write_round` 的"直写分库"日志写在采集端进程，管理端
  `/logs/recent` 看不到那一侧（`/logs/worker` 的轮次日志仍在）。

### 尚未处理的跨进程内存态

`tdx_store` 里还有这些模块级可变状态，进程内模式下 cloud_server 读到的是**自己那个
进程的**（通常是空的）：

| 状态 | 能否接受 | 说明 |
| --- | --- | --- |
| `_day_round_watermark` | ❌ 已修 | 已加数据库回退 |
| `_codes_cache` | ✅ 已修 | 加 300s TTL 重新播种（原先只有 `write_round` 会 `_note_codes`，进程内模式下会永久冻结） |
| `_dates_cache` | ✅ 已修 | `get_store()` 加载成功后补调 `_start_legacy_dates_scan()` |
| `_tdx_logs` / `_collect_states` | ⚠️ 需确认 | 日志环与采集状态原本由 merge_server 的内存提供；进程内模式下采集端写自己的、cloud_server 读自己的。`/logs/recent`、`/worker/status` 的**可观测性会退化**（看不到采集端那一侧） |

**抽库时改动的测试仅 3 处**，都是白盒 patch 指向新位置（内部全局搬到了 store，
薄壳上的同名再导出是值绑定，patch 它不影响 store 内的函数）：
`LEGACY_DIR`（2 处）、`open_day_db`（1 处）。**其余 15 项回归一字未改**，
正是它们证明了这次搬移是纯搬移。

⚠️ **`tdx/master/merge_server.py` 现在更不一致了**：它是重构前的单体版
（1323 行），而 `cloud_server/tdx_bridge/` 已变成「薄壳 + tdx_store」双文件结构。
两份不只是 132 行分叉，现在是**结构都不同**。收敛方式需要你定：
以 cloud_server 版为准（把 tdx/master 也拆成同样结构，或让它只留 service 文件）。

---

## 0. 结论先行

**推荐方案 B：保留数据库与查询语义，去掉 HTTP 跳与独立进程。**

理由：`merge_server` 承担两个角色 —— **传输**（HTTP 收/发）和**存储**（按日分库 + 历史查询）。
传输角色是纯开销（见 §3：它和 cloud_server 本来就在同一台机器上），可以去掉；
存储角色**不能被去掉**，因为回测 / 板块资金流 / 中证1000 全都要查历史分库。

方案 B 把 merge_server 从「服务」降级为「库」：查询函数直接 import 调用，
采集端直接写库。**下游约 20 处 `TdxQueryService(tdx_url)` 调用点一行都不用改** ——
构造签名和方法名保持不变，只换实现。

改造面比"17 个文件"小得多，因为绝大多数下游是**内联构造**而非持有依赖。

---

## 1. 现状：merge_server 的两个角色

| 角色 | 具体职责 | 能否去掉 |
| --- | --- | --- |
| **传输** | Flask 12 个路由；`/receive` 收全市场快照；`/query/*` 对外查询；IP 白名单；两道门控 | ✅ 可去（同机，纯开销） |
| **存储** | 按日分库 `tdx_YYYYMMDD.db`；36 字段 schema；`tdx_minute_snapshot` 分钟快照；legacy 回退合并 | ❌ **不可去**（历史查询依赖） |

**关键认识**：这次要动的是传输角色。存储角色必须原样保留。

---

## 2. 消费者画像（决定改造面）

按用法分三类：

| 类别 | 接口 | 消费者 | 数据来源 |
| --- | --- | --- | --- |
| **实时最新值** | `get_latest_data` / `query_latest` | `stock_service`、`tdx_enricher`、`strategy3`、`tdx_bridge_setup` | 当日分库最新一批 |
| **历史窗口** | `query_window` / `query` / `query_morning_window` | `csi1000_pct_service`、`csi1000_flow_service`、`plate_flow_service`、`plate_match_service`、`backtest_service` | 历史日分库 |
| **元数据/水位** | `get_round_watermark`、`get_worker_status`、`list_codes`、`list_trade_dates` | `plate_flow_service`、`csi1000_pct_service`、socket handlers | 分库 + 内存态 |

**第二类是存储角色的真正用户**——`backtest_service` 要查多日历史，`plate_flow` 要按分钟桶聚合。
这就是"不能简单删掉 merge_server"的原因。

---

## 3. 关键事实：它们已经同机

```ini
# tdx/master/merge-server.service
WorkingDirectory=/www/wwwroot/Acloud_server_v1_8095/tdx_bridge
ExecStart=/www/server/pyporject_evn/merger_server/bin/python3.12 -u merge_server.py
```

工作目录是 **`Acloud_server_v1_8095`** —— merge_server 就跑在 cloud_server 的目录树里。
而 cloud_server 的配置指向 `http://172.17.0.105:5000`（本机）。

**所以那个 HTTP 跳是机器内部的**：序列化 36 字段 × 5226 只、走 TCP、再反序列化，
全程没有跨机器的价值。实测每轮 5226 只的 payload 走这条路要几百毫秒到秒级。

> ⚠️ 这一条**需要你确认**：如果 cloud_server 与 merge_server 其实是两台机器，
> 方案 B 的收益评估要重算（但仍成立，只是少了"去掉本机跳"这个理由）。

---

## 4. 三个方案

### 方案 A：不动（现状）
- 成本 0。但保留了一个纯开销的同机 HTTP 跳，以及一个需要单独部署/监控的进程。

### 方案 B：降级为库（推荐）
- merge_server 的**查询核心本来就是纯函数**，已经可分离：
  `_day_db_path` / `_open_date_read` / `_query_latest_rows` /
  `_query_minute_snapshot_rows` / `_window_rows_to_data` / `_all_trade_dates`
- 把 12 个 Flask 路由里的**参数解析**与**查询核心**拆开，核心抽成 `tdx_store` 模块
- 采集端从 `POST /receive` 改为直接调 `tdx_store.write_round(...)`
- cloud_server 侧 `TdxQueryService` 换成本地实现，**方法名与签名不变**

### 方案 C：彻底重写
- eltdx 直接维护 `FullStockCache`，历史查询另建一套
- 改动面最大，且要重新发明分钟快照、代码规范化、legacy 回退这些已经调好的东西
- **不建议**

---

## 5. 方案 B 详细设计

### 5.1 抽库：`tdx_store.py`

从 merge_server 抽出，**不带 Flask 依赖**：

```python
# 存储层（原样搬，仅去掉 request 上下文）
open_day_db(trade_date, create=False)      # 已经是纯函数
_day_db_path / _day_file_ready / _all_trade_dates
_open_date_read / _open_date_read_all

# 写入层（原 /receive 的核心，抽出来）
write_round(trade_date, data, run_id, collected_at, ...) -> dict
    # 内含原有两道门控（交易日 / 时间窗），行为不变

# 查询层（原路由的核心，参数从函数入参来而非 request.args）
query_latest(codes) -> dict
query_raw(codes, date_from, date_to) -> dict
query_window(date, time_from, time_to, codes=None, fields=None,
             latest_per_minute=False, bucket_seconds=60, per_code_latest=False) -> dict
query_minute_snapshot(...)
get_round_watermark(date) -> dict
get_worker_status(date) -> dict
list_codes() / list_trade_dates()

# 可观测
recent_logs(limit) / append_log(level, message)
```

`merge_server.py` 保留为**薄壳**：只做 `request.args` → 函数入参的转换 + jsonify。
这样 HTTP 与进程内两条路可以**并存**，便于灰度切换。

### 5.2 采集端改造

```diff
- POST /receive  →  merge_server
+ tdx_store.write_round(trade_date, rows, run_id, collected_at)
```

门控逻辑**原样保留**在 `write_round` 内（周末拒绝、时间窗外拒绝、拒绝时不建库）。
`preflight.py` 的第 4 项检查相应改为"存储目录可写 + 门控行为正确"。

### 5.3 cloud_server 侧：换实现不换接口

这是改造面的关键。现状是所有消费者**内联构造**：

```python
svc = TdxQueryService(tdx_url)          # 约 20 处
reader = TdxDataReader(tdx_url)         # 2 处
```

**保持这两个构造函数签名不变**，让它们在 `tdx_url` 指向本机（或配置了
`TDX_STORE_INPROC=1`）时返回进程内实现：

```python
class TdxQueryService:
    def __new__(cls, query_url=""):
        if _use_inproc(query_url):
            return _InProcessQueryService()
        return super().__new__(cls)
```

`_InProcessQueryService` 实现同名方法（`query_window` / `query` /
`query_morning_window` / `list_codes` / `get_round_watermark` / `get_worker_status`），
**签名逐一对应**，内部直连 `tdx_store`。

→ **约 20 处调用点零改动。** 这 20 处正是当初看起来最吓人的部分。

同理 `TdxDataReader.get_latest_data` 直接读 `tdx_store.query_latest`，
保留 TTL 缓存与中英字段映射的逻辑（那部分是本地的，与传输无关）。

### 5.4 历史分库归属

分库由**采集端**创建（原来是 merge_server 收到第一轮时建）。
`tdx_store.open_day_db(trade_date, create=True)` 由采集端在写之前调用 —— 
也就是把 merge_server 里 `open_day_db(create=True)` 的时机前移，
`_gate_trade_date` 仍在其之前，保证"被拒的日期不建空库"（这条今天已验证过）。

### 5.5 监控

| 原 | 现 |
| --- | --- |
| `GET /worker/status` | `tdx_store.get_worker_status()` 直接调用 |
| `GET /logs/recent` | `tdx_store.recent_logs()` |
| `POST /logs/worker` | `tdx_store.append_log()` |
| cloud_server 轮询告警（`tdx_bridge_setup._start_worker_status_bridge`） | 改为进程内定时读，逻辑不变 |

`tdx_bridge_setup.py` 里那个 2 秒轮询 HTTP 的后台线程可以整个去掉，
换成直接读内存态的定时器。

---

## 6. 改造清单

| # | 文件 | 改动 | 量级 |
| --- | --- | --- | --- |
| 1 | **新增** `tdx_store.py` | 从 merge_server 抽查询/写入核心（约 700 行搬移） | 中 |
| 2 | `tdx_bridge/merge_server.py` ×2 | 瘦身为薄壳；两份分叉需先合并 | 中 |
| 3 | `server/data/tdx_query_service.py` | 加进程内实现分支 | 小 |
| 4 | `server/data/tdx_data_reader.py` | 同上 | 小 |
| 5 | `tdx/eltdx_slave/poll_loop.py` | `upload()` 从 HTTP 改直连 | 小 |
| 6 | `server/services/tdx_bridge_setup.py` | 去掉 HTTP 轮询线程 | 小 |
| 7 | `crawler/config/config.json` | `tdx_query_url` 语义变化 | 极小 |
| 8 | `test/test_merge_server.py` | 改为测 `tdx_store` + 薄壳两层 | 中 |
| 9 | 约 20 处 `TdxQueryService(tdx_url)` | **零改动**（见 §5.3） | — |
| 10 | 5 处 `TdxDataReader` | **零改动** | — |

**真实改动：8 个文件。** 不是 17 个。

---

## 7. 风险与前置条件

| # | 风险 | 对策 |
| --- | --- | --- |
| 1 | **两份 merge_server 已分叉 132 行** | 动之前必须先合并；否则抽出来的库是旧版 |
| 2 | 抽库时把 Flask 的隐式行为（`request.args` 默认值、错误码）漏掉 | 以现有 18 个 merge_server 测试为护栏，抽库后必须全绿 |
| 3 | 进程内查询是**同步阻塞**的 SQLite I/O，可能卡住 cloud_server 主循环 | 保留线程池/超时；必要时把慢查询（窗口聚合）放 executor |
| 4 | 失去进程隔离：采集端崩了会拖垮 cloud_server | 这正是**可选并存**的理由 —— 薄壳先在，确认稳定再切；必要时保 `TDX_STORE_INPROC=0` 回退 |
| 5 | 单机故障域扩大（原先一进程挂了服务还在） | 采集端异常必须被隔离（try/except 包住，不冒泡到主循环） |
| 6 | 去掉 HTTP 后无法把采集端放到别的机器 | ⚠️ **这是真实损失**。若将来要异地采集，薄壳保留就能回退 |

---

## 8. 不做什么

- **不重写查询语义**（分钟快照、代码规范化、legacy 回退都原样搬）
- **不改 36 字段 schema**
- **不动 `FullStockCache`**（`tdx_enricher` / `strategy3` 继续用 `TdxDataReader`）
- **不同时做别的优化**（性能、字段补全都另开）

---

## 9. 何时做

**前置条件（全部满足才动）**：
1. ✅ 采集端替换已在生产跑通
2. ⬜ **数值口径对撞已通过**（替换的硬门槛，见主计划 §5）
3. ⬜ **双跑已跑满至少一个完整交易日**
4. ⬜ 第 1、2 层删除已稳定（无回滚需求）

**建议顺序**：先做 §5.1 抽库（纯搬移 + 测试护栏，行为零变化），
观察无异常后再做 §5.2/5.3 的接线切换。

**回滚点**：薄壳保留期间，`TDX_STORE_INPROC=0` 即可退回原 HTTP 路径。
彻底删掉 merge_server 应该是**最后一个动作**，不是第一个。

---

## 10. 一句话总结

> **要去的不是 merge_server 这个"数据枢纽"，而是它那层同机的 HTTP 外壳。**
> 查询核心本来就是纯函数、消费者本来就是内联构造 —— 所以这是一次
> 「换实现不换接口」的降级，不是一次架构重写。真实改动面 8 个文件，
> 前提是先把两份分叉的 merge_server 合并、且对撞与双跑都已通过。
