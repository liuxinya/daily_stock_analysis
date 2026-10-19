# 图表数据（多周期 K 线 / 分时 / 技术指标）

Web 端的行情图走项目自有接口 `/api/v1/chart/*`：后端从同花顺行情 CDN 取数、归一化字段，指标在服务端按项目既有口径计算。

与 `/api/v1/baidu-finance/*` 的分工：那条是路径白名单的**原样透传**（字段不归一化、依赖 acs-token 风控），服务「股票查询」页的百度看板；这条是**归一化接口**，上游换源只改 `data_provider` 一层，前端契约不变。

## 能力范围

| 能力 | 支持情况 |
| --- | --- |
| 分时 | ✅ 逐分钟价格 / 均价 / 成交量，含昨收与交易时段 |
| 日 K | ✅ 不复权 / 前复权 / 后复权；可回溯到上市首日 |
| 周 K / 月 K / 季 K | ✅ 全量；上游只有前复权一份 |
| MACD / KDJ / MA | ✅ 服务端计算 |
| 分钟 K 线（1/5 分钟） | ❌ 上游有，v1 未接（见「未覆盖」） |
| 港股 / 美股 / 指数 | ❌ v1 只支持 A 股与场内基金 |

## 接口

### `GET /api/v1/chart/kline`

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `code` | 必填 | A 股 / 场内基金 6 位代码，接受 `SH`/`SZ`/`BJ` 前缀与 `.SH`/`.SZ` 后缀 |
| `period` | `day` | `day` / `week` / `month` / `quarter` |
| `adjust` | `qfq` | `none` / `qfq` / `hfq`，**仅 `period=day` 生效** |
| `limit` | `250` | 1~1200 根 |
| `indicators` | 缺省算 `ma` | 逗号分隔，可选 `ma` / `macd` / `kdj`；传空串表示一个都不算 |
| `with_today` | `true` | 日 K 是否用盘口快照补上未走完的当日那一根 |

响应为 `KlineResponse`（`api/v1/schemas/chart.py`）：`rows` 按时间升序，每根带 OHLCV、换手率、`partial`，以及请求到的指标字段（`ma5/ma10/ma20`、`dif/dea/macd`、`k/d/j`）。预热不足的区间指标为 `null`，不会补 0。

`adjust` **回报的是实际生效的口径**：周 / 月 / 季请求 `hfq` 也会回 `qfq`，因为上游只有那一份。

`today_source` 说明末根的性质：

- `snapshot` —— 日 K 末根由盘口快照合成，尚未收盘
- `upstream` —— 周 / 月 / 季的本期还没走完，数据取自上游
- `null` —— 末根已收盘

### `GET /api/v1/chart/timeshare`

只接受 `code`。响应含 `prev_close`（画基准线用）、`trade_times`、`is_trading`、`suspended` 与逐分钟 `points`。

### 错误

- `422 invalid_request` —— 代码格式、周期、复权口径或指标名不合法
- `502 upstream_unavailable` —— 上游重试后仍不可用

## 当日那一根为什么要自己合成

上游的 `line` 系列在盘中给不出可用的当日行，这是实测结论（2026-10 对 002594）：

- 前复权文件**没有当日行**，末根是上一个交易日
- 不复权文件是 `20261019,,,,82.70,0,0.00` 这样的占位：OHLC 全空、成交量 0、收盘价等于昨收
- 后复权文件有数但滞后，成交量只有实际的约五分之一

所以日 K 的当日那一根改从盘口快照（`realhead`）合成。快照字段无文档，字段 id 经三路对账确认（与分时累计量 / 累计额、均价、涨跌停 ±10%）：`7` 今开、`8` 最高、`9` 最低、`10` 现价、`13` 累计成交量、`19` 累计成交额、`6` 昨收。

合成有三条保险：

- 停牌、尚未开盘（无开盘价或成交量为 0）、日期缺失时**不产出**这一根 —— 宁可少一根，不要给一根假的
- 后复权口径**不合成** —— 快照是不复权价，换算需要复权因子
- 合成的那一根打 `partial=true`，前端在图注里明示「末根为盘中实时合成，尚未收盘」

同一根 K 线的所有字段都从一次快照请求取，避免两个文件取数瞬间不同导致内部不一致。

## 指标口径

指标公式刻意复用既有实现，保证同一只股票在**分析报告、告警判定、K 线图**上看到的是同一个数：

- MACD —— `src/stock_analyzer.calculate_macd_series`（12/26/9，`ewm(adjust=False)`；DIF/DEA/BAR=(DIF-DEA)×2）。`StockTrendAnalyzer._calculate_macd` 现在也调它。
- KDJ —— `src/services/alert_indicators.compute_kdj`（RSV 9 / K 3 / D 3，Wilder SMMA；**J = 3K − 2D**）。告警的金叉死叉判定现在也调它。此前这里只算 `K−D` 的差值、不输出 K/D、也没有 J 线。
- MA —— 5/10/20 日简单均线。

`src/services/chart_service.py` 会多取 80 根做预热再裁剪，避免返回区间开头几十根指标为 `null` 画成断线。切换副图（MACD ↔ KDJ）会重新请求 —— 指标只在服务端算，不在前端另算一套。

## 上游特性与两个坑

同花顺行情 CDN（`d.10jqka.com.cn`）是静态 JSONP 文件：**无 token、无 cookie、不校验 Referer**，`Access-Control-Allow-Origin: *`。日 K 前复权与 akshare 东财 `stock_zh_a_hist(adjust="qfq")` 的收盘价、成交量实测逐笔一致。akshare 自身也在用这个 farm（`stock_board_industry_ths` 走 `v4/line/bk_*`）。

**坑一：会返回 HTTP 200 + 截断 body。** 实测撞到过 211 字节的响应，另有偶发 openresty 502/504。所以 `_parse_jsonp` 对每个响应校验四件事：JSONP 函数名前缀、括号配对、JSON 可解析、必需字段存在。四项缺一即判上游异常，不让空数据静默流下去。5xx 与截断都重试（共 3 次），404 不重试。

**坑二：`all.js` 是另一套格式。** 深度历史靠按年取 `<year>.js`（与 `last.js` 同为 CSV），刻意不碰 `all.js` 的压缩列式格式。日 K 的 `last.js` 只给最近约 140 根，不够时按年回溯；周 / 月 / 季的 `last.js` 本身就是全量，不做回溯（回溯纯属浪费请求，还会撞上偶发 502）。

## 为什么不落库

`stock_daily` 的唯一键 `(code, date)` 没有周期维度，周 K 会和日 K 互相 UPSERT 覆盖；`date` 是 `Date` 类型，也放不了盘中时间戳。图表按需拉取 + 进程内短 TTL 缓存即可，不值得为看图去改那张表的 schema。因此本模块走旁路，不接入 `DataFetcherManager`（它的 `_fetch_raw_data(code, start, end)` 抽象签名里也没有周期通道）。

## 配置

两项都可选，不配置也能运行（已同步 `.env.example`）：

| 环境变量 | 默认 | 范围 | 说明 |
| --- | --- | --- | --- |
| `THS_CHART_TIMEOUT_SEC` | `10` | 3~60 | 单次上游请求超时（秒） |
| `THS_CHART_CACHE_TTL_SEC` | `30` | 0~600 | 进程内缓存时长（秒）；置 0 关闭缓存 |

盘中末根随行情变化，30 秒兼顾实时性与上游压力。前端弹层与图表各按 30 秒自动刷新，页面切到后台时暂停。

## 前端

一份实现，两处复用 —— 所以页面与弹窗的指标口径、刷新节奏、空态与报错表现不会分叉。

- `apps/dsa-web/src/api/chart.ts` —— 类型化客户端，响应统一过 `toCamelCase`
- `components/stock-chart/Candle.tsx` —— 蜡烛形状（涨空心、跌实心，方向不只由颜色承载）
- `components/stock-chart/MultiPeriodChart.tsx` —— 周期切换（分时/日/周/月/季）+ 主图 + **MACD、KDJ 两栏常驻副图**；三栏共享时间轴与十字光标，各自独立 Y 轴（量级差两三个数量级的序列不塞进同一张图做双 Y 轴）
- `components/stock-chart/StockQuoteHeader.tsx` —— 实时行情头部，`page` / `modal` 两种排版共用一份取数
- `components/stock-chart/StockChartPanel.tsx` —— 行情头部 + 图表，**「看一只股票」的唯一一份实现**
- `components/stock-chart/StockChartModal.tsx` —— 全局行情弹窗，挂在 `App.tsx` 的 Routes 外（切路由不卸载）。标题栏带一个跳同花顺个股页的外链（`https://stockpage.10jqka.com.cn/<code>`，新标签打开）：弹窗里只有自有数据的图，想看资金/财务/股东那些就跳过去
- `stores/stockChartStore.ts` —— 全局弹窗 store

### 任何页面怎么接

```tsx
import { resolveStockChartCode, useStockChartStore } from '../../stores/stockChartStore';

const openStockChart = useStockChartStore((state) => state.open);
const code = resolveStockChartCode(item.code);   // null 表示不该给入口
// ...
{code ? <button onClick={() => openStockChart(code, item.name)}>…</button> : <span>{item.name}</span>}
```

代码校验与归一化收在 store 一处：先过项目既有的格式规则（`sanitizeFollowUpStockCode`），
再用 `normalizeStockCode` 剥掉交易所前后缀（与后端 `normalize_stock_code` 同口径），
仍不是 6 位数字就不给入口 —— 这些代码不少来自模型推断，不能把不确定的文本丢给接口。

已接入的入口：

| 位置 | 文件 | 点什么 |
| --- | --- | --- |
| 股票查询页（整页内容区） | `pages/StockQueryPage.tsx` | 直接内嵌 `StockChartPanel`，不是弹窗 |
| 新闻分析 → AI汇总 → 关注股票 | `components/market-brief/MarketBriefPanel.tsx` | 个股名 |
| 新闻分析 → CCTV → 关注个股 | `components/cctv-news/CctvNewsAnalysisPanel.tsx` | 个股名（操作列的「问股 / 查询」保持不变） |
| 行业资金 → 关注股票 | `components/industry-funds/ConclusionSection.tsx` | 代码 + 名称单元格 |

股票查询页的当前标的直接从 `?code=` 推导，不另存 state —— 页面内查询与外部深链走同一条
路径，地址可分享、刷新后仍停在同一只股票上。搜索框用全站共用的 `StockAutocomplete`，
只承载「正在搜什么」、选中后清空（当前标的由下方行情头部的名称与代码交代）。

该下拉覆盖港股 / 美股 / 日韩股，而图表链路只验证过 A 股与场内基金，所以页面按候选的
`market` 字段挡一道并明确提示 —— **不能只看位数**：三星 `005930.KS` 去掉非数字也是 6 位，
只按位数判会把它当成 A 股的 005930 画出来。

### 图表交互与读法

一次取回 500 根（`BAR_LIMIT`），首屏显示最近 120 根，缩放与平移都在已有数据里做，不边拖边请求：

| 操作 | 效果 |
| --- | --- |
| 拖动 | 左右平移。往右拖是把内容拉向右边，看到更早的日期 |
| 双指左右滑动（`deltaX`） | 同上，向右滑看更早的日期 |
| 双指上下滑动 / 鼠标滚轮（`deltaY`） | 缩放，锚定右端。**向上滑看到的日期区间变宽，向下滑变窄** |
| 方向键 ← → | 平移一成；需先聚焦图表（可 Tab 到） |
| `+` / `-` | 缩放；`Home` 复位 |
| 「全览」/「复位」 | 一键铺满全部根数 / 回到首屏 120 根 |

视窗用「可见根数 + 右端偏移」记位置，不用起始下标：自动刷新时上游会在末尾追加一根，
右端偏移为 0 时视窗自然跟上最新那根，往左看历史时也不会被刷新拽回去。

三处与「跟手」直接相关的实现细节：

- **滚轮合并到一帧（`requestAnimationFrame`），拖动与捏合则同步提交**。滚轮（尤其触控板）
  一次手势能连发几十个事件，远快于一帧，不合并就是三栏图表每秒重绘上百次；而 pointermove
  本来就由浏览器对齐到帧派发，再过一层 rAF 只是白白多等一帧。
- **拖动标记只用 ref，不用 state，Tooltip 也不在拖动时摘掉**。state 一变就把三栏重渲染一遍，
  而那一帧正好挡在「第一次位移」前面 —— 表现就是「滑了一下 K 线才动」。
- **缩放用 `size *= exp(deltaY × 0.0015)`，不是固定倍数**。触控板一次手势连发几十个
  小 delta，固定 1.2 倍会瞬间缩到底；指数形式对鼠标滚轮（一格约 100 → 约 1.16 倍）
  和触控板都线性跟手。
- **recharts 的内置动画全部关掉**。`Tooltip` 默认 `animationDuration: 400`、`Line` 默认 1500ms，
  且 `isAnimationActive` 默认是 `"auto"` 而非 `false` —— 拖动时提示框与光标会带 400ms 缓动
  跟在后面，分时线每 30 秒刷新还会重放一次 1.5 秒入场动画。所有 `Tooltip` / `Line` / `Bar`
  都显式 `isAnimationActive={false}`。
- **三栏图表都用 `memo` 包一层**。只移动光标时 `rows` 不变，于是只有读数行重渲染，
  三张图不跟着重绘。蜡烛形状也不再套 `<g>`（recharts 本来就渲染在自己的 `<Layer>` 里），
  每屏少 100 多个节点。
- **拖动中不跟踪光标数值**。recharts 的 `mousemove` 与指针事件会在同一帧各发一次
  `setState`，拖动时直接跳过前者（state 控 Tooltip、ref 控短路，两份是因为 ref 要在
  `pointerdown` 同一拍内立刻生效）。

另外横滑的位移会累计到够一根才走（`panRemainder`），否则每次几个像素会被取整抹成 0。

十字光标的下标解析放在 `components/stock-chart/hover.ts`（单独成文件以便单测）：recharts v3 的
`activeTooltipIndex` 类型是 `number | TooltipIndex | undefined`，而 `TooltipIndex` 是**字符串** ——
只认 `number` 会让读数行永远停在最后一根上。副图各挂一个空内容的 Tooltip，只为借它的 cursor
画出贯穿三栏的竖线，数值则由各自的表头条承担。

> 方向按 macOS「自然滚动」默认值对齐（向上滑 = `deltaY > 0`）。关掉自然滚动的设备会上下相反，
> 这是浏览器拿不到的系统设置，没有可靠的检测方式。

**还剩一处固有的「步进感」**：recharts 用的是分类轴，平移只能按整根 K 线切片，所以一次最小
位移就是一个柱宽（默认 120 根铺满约 1000px 时约 8px，`Math.round` 下死区约 4px；缩到
`MIN_VISIBLE=20` 根时柱宽约 50px，死区约 25px，这时「先不动、再跳一格」最明显）。要做到亚
像素级连续跟手，得换成按像素偏移重绘的 canvas 方案（klinecharts / lightweight-charts），
当前刻意不引入。

读法上有两处刻意不只靠颜色（红绿在 deutan 下 ΔE≈8）：

- **蜡烛**：涨空心、跌实心。
- **MACD 柱**：颜色按正负（红涨绿跌），**柱体放大为实心、收缩为空心** —— 动能变化方向也有一层形状编码。

主图上方常驻一行逐根读数（日期 + 开/高/低/收 + 振幅 + 量 + 额 + 换手 + MA5/10/20），MACD 与 KDJ 各有一条表头条常驻数值（`DIF/DEA/MACD`、`K/D/J`），三者都跟随十字光标所在的那一根，
光标离开时回落到最后一根。这同时满足图表对比度的「可见标签」补偿要求。表头条是独立一行、
不是浮在图上的绝对定位层 —— 那样会压住上一栏的曲线。三栏之间留分隔线与间距（主图 300px、
MACD 128px、KDJ 160px，最后一栏承担共享的时间轴，所以给得高一点）。

> 注意：日 K 要 500 根意味着每次取数要按年回溯 2~3 个 `<year>.js`。配合 30 秒自动刷新，
> 单个打开的图表约每 30 秒 3~4 个上游请求（服务端有 30 秒缓存兜住重复请求）。实测上游无
> 限流，但如果以后要开很多图同时刷，这是第一个该调的旋钮。

## 未覆盖

- **分钟 K 线**：上游有 1 分钟（约 40 个交易日）与 5 分钟（约 10 个月），但 1 分钟的 `last.js` 盘中严重滞后，刷新节奏需盯一个完整交易日才能定；没有 15/30/60 分钟（上游 404），需自行聚合。
- **港股 / 美股 / 指数**：上游前缀（`hk_HK0700`、`usa_AAPL`、`16_000001`）实测可返回 200，但未做数值交叉验证，`normalize_symbol` 显式拒绝，不把未验证的映射当成可用能力。
- **边界场景**：停牌、除权日当天、新股首日、ST / 退市未实测。
- **压力位 / 支撑位**：那是百度 `analysis-quotation` 的 `analysisData` 字段，不在同花顺链路里，随百度 tab 一并下线。
- **原「股票查询」页的六个百度 tab**（资金分布 / 公司简况 / 股评 / 资讯 / 三表财务 / 评分分析）已移除，同花顺链路没有对应数据，短期也没有替代源。后端 `api/v1/endpoints/baidu_finance.py` 代理与 `BAIDU_FINANCE_*` 配置**仍保留**（见 `docs/stock-query.md` 顶部的现状提示），但前端已无调用方；是否连同 Playwright / Chromium 镜像依赖一起下线属于单独的部署决策，尚未做。
- **Web 设置页注册表**：两个新配置项只进了 `.env.example` 与本文档，未加入 `src/core/config_registry.py`（属内部调优项，不需要在设置页暴露）。
- **其他可接入但未接的入口**：选股候选表、回测结果表、决策信号卡片、首页报告头部、持仓表都有现成的代码+名称对，接法与上表一致，本次未动。
