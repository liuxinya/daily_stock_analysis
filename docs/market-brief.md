# 消息面汇总（盘前 / 盘中 / 周末 / 节假日）

按当前时段对本地资讯池在对应时间窗内的消息做 AI 汇总，入口在 Web「实时新闻」页面的「消息面汇总」tab。

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

素材来自本地资讯池（`IntelligenceService`），查询用 `scope_type="market"`, `market="cn"` 加绝对时间窗。

两个前提：

1. **建议开启定时采集**。各家快讯接口单次只返回最近一小段，不持续增量落库，窗口内就没有素材。见 `NEWS_INTEL_COLLECT_ENABLED`。
   用户显式触发（页面按钮 / `POST /jobs`）时，窗口为空会**就地强制采集一轮再重试**（`force=True`，不受定时开关限制），
   与 CCTV 模块提交分析前先同步一次是同一套做法；仍为空才返回 400，且提示会区分「采集开关没开」与「上游暂时没有新内容」。
2. **查询开启 `undated_fallback`**。财联社热门、雪球热门股这类源不提供发布时间，默认的严格过滤会把它们整体排除（SQL 里 `NULL >= x` 不为真）。汇总用 `coalesce(published_at, fetched_at)` 让它们参与；个股/大盘分析仍走严格语义，不受影响。

## 输出契约

`src/schemas/market_brief.py`，分两层，前端也按两层呈现：

- **事实层** `events` / `sectors` / `takeaways`：每条都带 `evidence_ids`（引用条目编号）与 `mentioned_terms`（在原文中逐字出现的词）。服务端逐条校验，术语查不到或没有引用的**整条丢弃**并记入 `grounding.dropped`。
- **推断层** `watch_list`：个股大多不会被快讯点名，属于「板块 → 相关标的」的模型知识映射。必须挂在已通过校验的板块上 —— 板块被丢弃则挂在它下面的标的一起丢。`code` 只接受 6 位数字，否则置空，不编造。

## 已继承的坑

这些都是 CCTV 新闻分析模块踩过的，照抄而非重新发现：

- **哨兵双向转义**：资讯内容是不可信输入，`BEGIN_UNTRUSTED_NEWS_ITEMS` / `END_...` 都要转义，title 和 summary 都要过。
- **全局序号**：分段前统一编号，各段返回的 `evidence_ids` 才能直接合并。
- **调用自己兜 deadline**：`generate_text_with_metadata` 没有 timeout 参数，连接挂死会让后台任务永远停住。用 `ThreadPoolExecutor(1)` + `future.result(timeout=...)` + `shutdown(wait=False)`；加 `wait=True` 就退化成阻塞等待。
- **降级返回 200，配置问题抛 503**：全段失败走关键词兜底并标注 `degraded`；但 LLM 未配置必须抛 503，不能静默降级。
- **超时优先于「预算耗尽」**作为降级原因：预算常常就是被超时调用烧掉的。
- **推理型模型必须限制 reasoning effort**（`MARKET_BRIEF_REASONING_EFFORT`，默认 `low`，仅对 `glm-5` / `deepseek-v4` 系透传）。这不是省钱：实测 glm-5.3-flash 不限推理时会把整个输出预算（6144 token / reasoning 1.7 万字符）全烧在思考上，`finish_reason=length` 而 `content` 为空，上游包装层判定为空响应 → `all_models_failed` → 整次汇总降级成关键词。加上 `effort=low` 后reasoning 降到十几个字符、正文 6500+ 字符正常返回。输出预算也相应提到 8192 留缓冲。

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
| POST | `/api/v1/market-brief/jobs` | 提交后台任务；空窗口在这里就返回 400 |
| GET | `/api/v1/market-brief/jobs/{job_id}` | 轮询进度；过期返回 404 |

错误约定：入参/素材不足 400 `validation_error`；LLM 不可用 503 `analysis_unavailable`；其他 500 `internal_error`。鉴权由 `/api/v1/*` 全局中间件覆盖。

## 关于 GitHub Actions 部署

Actions runner 是一次性的，SQLite 文件不会保留到下一次运行，所以 `10-market-brief.yml` 用 `actions/cache` 在两次运行之间搬运数据库。缓存被驱逐（7 天未使用）或超配额时，本次从空库开始，汇总退化成「只看最近抓到的这批」，不会报错。

需要长期完整历史的部署应该用常驻服务（`--serve`）+ `NEWS_INTEL_COLLECT_ENABLED`，Actions 只作兜底。

## 配置

见 `.env.example` 的「消息面汇总」一节，全部可选。数据前提是 `NEWS_INTEL_COLLECT_ENABLED`（在「新闻与分析筛选配置」一节）。
