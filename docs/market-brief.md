# 消息面汇总（盘前 / 盘中 / 周末 / 节假日）

按当前时段对对应时间窗内的消息做 AI 汇总，入口在 Web「实时新闻」页面的「消息面汇总」tab。
素材在汇总那一刻直接从各快讯接口拉（能拿多少拿多少），并与本地资讯池里已攒下的内容合并去重。

先只覆盖 A 股。

## 时段与时间窗

时段由 `src/core/trading_calendar.py` 的 `MarketPhase` 推导，窗口边界来自交易日历：

| 模式 | 判定 | 时间窗 |
| --- | --- | --- |
| `premarket` 盘前 | phase = premarket | 上一个交易日收盘 → 现在 |
| `intraday` 盘中 | phase ∈ {intraday, lunch_break, closing_auction} | 今日开盘 → 现在 |
| `postmarket` 盘后 | phase = postmarket | 今日开盘 → 现在 |
| `weekend` 周末 | phase = non_trading 且 `weekday() >= 5` | 上一个交易日收盘 → 现在 |
| `holiday` 节假日 | phase = non_trading 且 `weekday() < 5` | 上一个交易日收盘 → 现在 |
| `unknown` | 日历不可用或时段判定失败 | 退化为固定回看，结果里显式标注 |

几个刻意的设计：

- **周末与节假日必须分开**：`MarketPhase` 把两者都归成 `non_trading`，仓库里没有区分方法，只能靠 `weekday()`。也**不能只依赖 `is_market_open()`** —— 它是 fail-open 的，交易日历缺失时周六也返回 True。
- **不给 `MarketPhase` 加枚举值**。加了要同步 5 处白名单（`_phase_booleans`、`market_phase_prompt`、`market_phase_summary`、`analysis_context_pack_prompt`、`phase_decision_guardrail`），漏一处新 phase 就会被静默降级成 unknown。weekend / holiday 是 `market_brief_window` 自己的派生概念。
- **窗口边界 fail-closed**。`build_market_phase_context` 的语义是混合的：phase 走 fail-closed（UNKNOWN + warning），但同一对象里的 `effective_daily_bar_date` 走 fail-open（退化成自然日）。所以本模块自己检查 `warnings`，日历不可用时退化成固定回看并标注，而不是拿自然日当交易日边界。
- 长假天然产生长窗口（国庆后首个交易日的盘前窗口约 7.7 天），窗口上限 12 天，超出则截断并标注。
- **窗口下限 `MARKET_BRIEF_MIN_WINDOW_MINUTES`（默认 90 分钟）**：刚开盘时「今日开盘至今」可能只有几分钟，据此出稿没有意义，因此把起点往前放到至少这个长度 —— 对 09:35 的读者来说恰好把隔夜消息一起纳入。此时 `extended=true`，`window_hint` 会注明已延长。这不是降级，`degraded` 仍为 false。

为此在 `trading_calendar.py` 新增了两个公开函数：`get_previous_session_close()` 与 `get_session_open()`，都是 fail-closed 返回 `None`。

## 数据来源

素材有两路，汇总时合并去重：

| 路径 | 取数时机 | 覆盖能力 | 是否需要配置 |
| --- | --- | --- | --- |
| **直拉快讯接口**（主路径） | 汇总那一刻现问上游要一遍 | 雪球 / 第一财经可深翻回看一两天，其余平台只覆盖最近几小时 | 不需要，默认开启 |
| **本地资讯池** `IntelligenceService` | 事先持续采集后落库 | 取决于攒了多久，能覆盖直拉翻不到的更早内容与 RSS 类源 | 可选，`NEWS_INTEL_COLLECT_ENABLED` |

### 为什么必须有「直拉」这一路

只靠资讯池时，窗口能不能填满完全取决于「之前有没有常驻进程一直在采」。各家快讯接口单轮
只返回最近一小段且多数**没有历史翻页**，所以一旦没采，过去的窗口就永久空缺 ——
实测过一次：周日的周末窗口（周五 15:00 至今，52 小时）里只有 67 条素材，全部挤在
周五收盘后的 15:00–15:05，等于整个周末的消息面是空的，而且事后补不回来。

直拉这一路把「窗口内有多少消息」从「历史运维状态」变回「此刻上游能给多少」。

各平台单轮覆盖跨度（2026-10-11 实测，`limit=50`）：财联社 2.0h、同花顺 2.6h、
东方财富 3.6h、华尔街见闻 2.8h、金十数据 2.9h、新浪财经 1.0h、第一财经 4.3h、
格隆汇 0.5h、**雪球 19.9h**；放开翻页上限后雪球可回看约 86 小时、第一财经约 107 小时。
财联社的 `last_time` 参数被上游忽略（第二页与第一页完全相同），翻不动，两天内的
财联社系内容靠雪球带回来（雪球快讯本就以财联社为主）。

实现见 `RealtimeNewsService.fetch_window_entries`，与实时新闻页取数的差异见
[实时新闻文档](realtime-news.md#按时间窗回看消息面汇总用)。

### 取数细节

- **预算**：`MARKET_BRIEF_LIVE_FETCH_TIMEOUT_SEC`（默认 30 秒）是**整轮**预算，全平台并发；
  单平台超时或失败只跳过它自己（fail-open），失败平台名会写进空窗口提示。设为 `0` 关闭直拉、
  退回纯资讯池模式（此时 `POST /jobs` 恢复同步预检与空窗口 400，见下文）。
- **去重**：两路合并按标题归一化去重（同一条消息常被多平台同时发，且池里的 NewsNow 源与
  直拉的站点源经常是同一条内容的不同链接），直拉那份优先保留 —— 它的发布时间直接来自站点。
- **时间基准**：直拉条目是 aware 上海时间，落到资讯池的 naive 本地约定（与
  `IntelligenceService._parse_datetime` 同一个写法）后再排序分桶，避免两种基准混用。
- **超量时分层抽样**，不是截最新 N 条：把窗口切成 2 小时一个桶，轮转着每桶取一条
  （桶内「重要」标记优先、再按时间新），所以 52 小时窗口的每个时间片都会先被覆盖到，
  密集时段只在别的桶取空之后才拿到额外配额。见 `src/services/market_brief_material.py`。
- **分段数不会超过调用预算**：`split_within_call_budget` 在段数超过
  `MARKET_BRIEF_ANALYSIS_MAX_LLM_CALLS` 时放宽单段字数而不是丢素材 ——
  超预算的段是整段跳过的，而段按时间排序，丢掉的正好是窗口里最早那批，等于把分层抽样
  铺开的覆盖又砍回最近几小时。
- **进 prompt 的摘要截断到 300 字符**（`ITEM_SUMMARY_PROMPT_MAX_CHARS`）：资讯池单条
  `summary` 可达 2000 字符，不截断的话段数完全由少数长条目决定。信源抽屉展示的是未截断原文。
- **查询开启 `undated_fallback`**。财联社热门、雪球热门股这类源不提供发布时间，默认的严格过滤会把它们整体排除（SQL 里 `NULL >= x` 不为真）。汇总用 `coalesce(published_at, fetched_at)` 让它们参与；个股/大盘分析仍走严格语义，不受影响。
- 两路都空时，才会去动「就地强制采集一轮再重试」那条老兜底（`force=True`，不受定时开关限制，
  与 CCTV 模块提交分析前先同步一次是同一套做法）；仍为空才报错。

### 窗口为空时怎么查

直拉接上之后，「窗口为空」基本只会是网络问题。按下面的顺序看：

1. **先看提示里有没有点名失败平台**。整轮直拉失败会直接说「直拉快讯接口整轮失败」，
   部分失败会列出平台名（如「直拉快讯时 金十数据、华尔街见闻 没拉到内容」）。
2. **代理是常见元凶**。实时新闻那几个平台走环境代理，服务进程如果从带 `http_proxy` /
   `ALL_PROXY` 的 shell 启动就会被一起拖下水；`src/config.py` 会在检测到代理时把数据源与
   资讯源域名写进 `NO_PROXY`，但前提是**进程启动时**就能读到这些环境变量。
   详见[实时新闻的 503 排障](realtime-news.md#排障部分-tab-稳定-503)。
3. **再看资讯池那一路**。设置页的资讯源列表或直接查库：
   `sqlite3 data/stock_analysis.db "SELECT name, last_status, last_error, last_fetched_at FROM intelligence_sources;"`
   —— `last_fetched_at` 只在成功时更新，所以它停在很久以前就说明这个源已经连续失败。
   `last_error` 会指出失败类别：`proxy error` / `SOCKS proxy configured but PySocks is not installed` /
   `TLS error` / `timeout` / `HTTP 403` / `connection error`。完整异常链在
   `logs/stock_analysis_debug_*.log` 里（带 traceback）。
4. 以上都正常、两路都确实没有窗口内的条目，才是「上游暂时没有新内容」。

## 输出契约

`src/schemas/market_brief.py`，分两层，前端也按两层呈现：

- **事实层** `events` / `sectors` / `takeaways`：每条都带 `evidence_ids`（引用条目编号）与 `mentioned_terms`（在原文中逐字出现的词）。服务端逐条校验，术语查不到或没有引用的**整条丢弃**并记入 `grounding.dropped`。
- **推断层** `watch_list`：个股大多不会被快讯点名，属于「板块 → 相关标的」的模型知识映射。必须挂在已通过校验的板块上 —— 板块被丢弃则挂在它下面的标的一起丢。`code` 只接受 6 位数字，否则置空，不编造。

前端对 `watch_list` 额外给了一个行情入口：个股名可点，弹出侧滑看板，上半是实时行情（走项目自有数据源 `/api/v1/stocks/{code}/quote`），下半是日 K 蜡烛图与压力位/支撑位（走百度财经透传代理，与股票查询页同一条链路、同一个蜡烛图组件）。几点约定：

- **代码再校验一次**。`code` 是模型推断结果，服务端只放过 6 位数字；前端再过一遍项目既有的代码规则，非 A 股 6 位码（含空代码）保持纯文本、不给入口 —— K 线那条链路只覆盖 A 股（`market_type=ab`）。
- **两路独立失败**。行情挂了还能看 K 线，K 线被上游风控挡了还能看行情，各自就地报错，不互相拖垮。
- **打开即取数 + 每 30 秒自动刷新**，页面切到后台时暂停。日 K 是当前唯一已验证的粒度：后端没有分时/分钟线路由，百度代理白名单里虽然有 `getquotation`，但分时判别参数未在有效 token 下实测过，没有据此猜一套参数。

payload **顶层**还有一个 `evidence` 数组，供前端把 `evidence_ids` 解析成可展开的原文（事实层每条结论后的「信源 N」入口，点开侧滑抽屉看标题/来源/时间/摘要/原文链接）：

- 每个元素 `{id, title, summary, source, published_at, url}`，`id` 与 `evidence_ids` 对应。
- **只含被引用到的条目**（events/sectors/takeaways/watch_list 四者 `evidence_ids` 的并集，按 id 升序），不是全部素材 —— 控制 payload 体积与库存储大小。注意这与 `coverage.cited_items`（只统计 events+sectors，页头「X/Y 条被引用」口径）刻意分开。
- `url` 只保留真实 http(s) 链接；无原文链接的条目入库时 url 是伪链接 `no-url:intel:<hash>`，这里会被过滤成空串，前端据此不渲染外链。
- **可选字段**：本功能上线前生成的旧汇总（`latest` 读回、缓存命中）没有该字段，前端一律 `?? []` 兜底，表现为「暂时没有信源入口」，重新汇总即恢复。该字段不改 `result` 结构，故未 bump `MARKET_BRIEF_SCHEMA_VERSION`。

## 已继承的坑

这些都是 CCTV 新闻分析模块踩过的，照抄而非重新发现：

- **哨兵双向转义**：资讯内容是不可信输入，`BEGIN_UNTRUSTED_NEWS_ITEMS` / `END_...` 都要转义，title 和 summary 都要过。
- **全局序号**：分段前统一编号，各段返回的 `evidence_ids` 才能直接合并。
- **调用自己兜 deadline**：`generate_text_with_metadata` 没有 timeout 参数，连接挂死会让后台任务永远停住。用 `ThreadPoolExecutor(1)` + `future.result(timeout=...)` + `shutdown(wait=False)`；加 `wait=True` 就退化成阻塞等待。
- **降级返回 200，配置问题抛 503**：全段失败走关键词兜底并标注 `degraded`；但 LLM 未配置必须抛 503，不能静默降级。
- **超时优先于「预算耗尽」**作为降级原因：预算常常就是被超时调用烧掉的。
- **推理型模型必须限制 reasoning effort**（`MARKET_BRIEF_REASONING_EFFORT`，默认 `low`，仅对 `glm-5` / `deepseek-v4` 系透传）。这不是省钱：实测 glm-5.3-flash 不限推理时会把整个输出预算（6144 token / reasoning 1.7 万字符）全烧在思考上，`finish_reason=length` 而 `content` 为空，上游包装层判定为空响应 → `all_models_failed` → 整次汇总降级成关键词。加上 `effort=low` 后reasoning 降到十几个字符、正文 6500+ 字符正常返回。但 `effort=low` 并非每个模型都真的吃下：deepseek-v4.1-flash 在 `low` 下仍会用掉 5.4k–8k reasoning token，所以输出预算现为 16384。**截断比空响应更隐蔽**：`content` 被 `finish_reason=length` 切掉一半时非空，上游不判失败，`json_repair` 把半截 JSON 救成 prefix，排在 `sectors` 之后的 `watch_list` / `takeaways` / `risks` 就静默变空（2026-10-20 盘前那份即 events 12、sectors 8 而 `watch_list` 0）。

三处与 CCTV 不同：

- **签名吃内容哈希**。CCTV 只吃 `id` + `char_count`，而资讯池条目会被 `upsert_items` 回填 `summary` / `published_at`，同长度改写不会失效缓存。
- **job 去重**。同一模式同一窗口正在跑时复用已有 job，因为定时任务与手动按钮可能同时触发。
- **`invalid_evidence_ids` 如实写入**。CCTV 那份静默丢弃越界 id、该字段永远为空。

## 触发方式

**定时生成**（`MARKET_BRIEF_ENABLED=true`）：后台任务每 10 分钟检查一次，由 `should_generate()` 判断是否到点且本窗口未出稿。判重查 `market_brief_analyses` 表的 `mode` + `window_start`，不另存调度状态。盘中按间隔重复出稿，其余模式一个窗口只出一次。日历不可用（`mode=unknown`）时不出稿。

三个入口，都走同一个 builder：

- 常驻服务：`RuntimeSchedulerService._current_market_brief_background_tasks`
- CLI：`main.py --schedule`
- GitHub Actions：`.github/workflows/10-market-brief.yml` 的可选步骤，需要 `vars.MARKET_BRIEF_ENABLED=true` 并透传 LLM 渠道 env

**手动生成**：页面右上角按钮，不受 `MARKET_BRIEF_ENABLED` 限制。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/market-brief/window` | 当前时段、时间窗、日历降级 warning |
| GET | `/api/v1/market-brief/latest` | 最近一份汇总；未生成过返回 404 |
| POST | `/api/v1/market-brief/jobs` | 提交后台任务 |
| GET | `/api/v1/market-brief/jobs/{job_id}` | 轮询进度；过期返回 404 |

错误约定：入参/素材不足 400 `validation_error`；LLM 不可用 503 `analysis_unavailable`；其他 500 `internal_error`。鉴权由 `/api/v1/*` 全局中间件覆盖。

**`POST /jobs` 的空窗口语义取决于直拉开关**：直拉开着（默认）时这里**不做**同步预检 ——
预检要打一轮上游，点按钮得等满整轮预算才拿到 `job_id`；素材判定交给 job 自己，空素材
表现为 job `status=failed` + `error` 里的具体原因（前端已有「上一次汇总失败」渲染）。
把 `MARKET_BRIEF_LIVE_FETCH_TIMEOUT_SEC` 设为 `0` 关闭直拉时，恢复原来的同步预检 + 400。

job 的 `phase` 比以前多一档 `fetching`（「拉取新闻」，直拉阶段）：
`queued → fetching → reading → analyzing → finalizing → succeeded|failed`。
前端按后端下发的 `phase_label` 原样渲染，新增阶段不需要改前端。

## 关于 GitHub Actions 部署

Actions runner 是一次性的，SQLite 文件不会保留到下一次运行，所以 `10-market-brief.yml` 用 `actions/cache` 在两次运行之间搬运数据库。缓存被驱逐（7 天未使用）或超配额时，本次从空库开始，但汇总仍能直拉到窗口内能拿到的部分（雪球 / 第一财经可回看一两天），只是更早的内容缺失，不会报错。

需要长期完整历史的部署应该用常驻服务（`--serve`）+ `NEWS_INTEL_COLLECT_ENABLED`，直拉与 Actions 只作兜底。

## 配置

见 `.env.example` 的「消息面汇总」一节，全部可选，不配置也能出稿（直拉默认开启）。
想让汇总覆盖比直拉能翻到的更早的内容，再去开 `NEWS_INTEL_COLLECT_ENABLED`（在「新闻与分析筛选配置」一节）。
