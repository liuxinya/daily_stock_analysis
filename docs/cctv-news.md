# CCTV 新闻联播文字稿与 AI 分析

Web 端「CCTV 新闻」页面（`/cctv-news`）提供两件事：按天浏览近一个月的《新闻联播》文字稿，以及基于用户选定时间窗口内文字稿原文的 AI 政策与产业解读。

## 能力范围

- 按天抓取央视网《新闻联播》栏目下的单条新闻文字稿，落库到本地 SQLite。
- 列表页展示每条新闻的标题与**文字总结**，点开看**文字稿详情**全文。
- 支持在近 30 天窗口内做全文关键词搜索。
- AI 分析支持三档时间范围：最近一周 / 最近 10 天 / 最近 20 天。
- 分析结果分两层展示：**新闻事实层**（政策信号、行业方向，每条带可点开的原文依据）与**模型推断层**（关注个股）。

## 数据来源与时效

| 用途 | 地址 |
| --- | --- |
| 当日节目单 | `https://tv.cctv.com/lm/xwlb/day/YYYYMMDD.shtml` |
| 单条新闻页 | 节目单中的条目链接，正文位于 `div.content_area` |

- 文字稿通常在当天 **20:00 之后**才陆续上线，个别条目会延到次日。因此同步当天可能返回 0 条，属正常现象。
- 仅支持 `2016-02-03` 及之后的日期；更早的内容在已下线风险较高的旧站（`cctv.cntv.cn`），页面结构不同，不做兼容。
- 节目单中的纯视频条目（无文字稿）会被记为 `skipped`，不当作失败。

### 抓取完整性

单日抓取结果用三个标记显式区分状态，避免"抓了一半"被当成"当天就这么多"：

| 字段 | 含义 |
| --- | --- |
| `index_available` | 节目单页是否成功取回 |
| `failed_count` | 抓取或解析失败的条目数 |
| `is_complete` | 节目单可达 + 无失败条目 + 至少抓到一条正文 |

只有 `is_complete=true` 的日期才会在后续同步中跳过；未完整的日期在 30 分钟冷却后自动重试。

## 事实约束（AI 分析不许臆测）

分析要求"只依据用户选定范围内的文字稿"，这条不是靠 prompt 里的一句话保证的，而是四层措施：

1. **输入隔离**：prompt 只包含窗口内的文字稿（带编号），不注入行情、研报或任何外部数据。
2. **提示注入防护**：文字稿视为不可信输入，用哨兵包裹并转义正文里的同名哨兵。
3. **服务端逐条校验**：模型输出的每条政策信号 / 行业方向 / 股民要点必须带 `evidence_ids`（引用的新闻编号）和 `mentioned_terms`（在原文中逐字出现的词）。归一化后在原文里查不到、或编号越界的条目**直接丢弃**，并记入 `grounding.dropped` 由前端如实展示。
4. **个股单独成层**：《新闻联播》基本不点名上市公司，所以个股属于"行业 → 龙头公司"的模型知识推断。这一层强制挂在已通过校验的行业上（行业被丢弃则个股一并丢弃），并带免责声明；股票代码不满足 6 位数字时置空而不是编造。

因此"文字稿里没提到的行业"不会出现在结果里；而"关注个股"是明确标注的推断，不会伪装成新闻事实。

## 分析输出结构

| 分层 | 字段 | 说明 |
| --- | --- | --- |
| 新闻事实层 | `policy_signals` | 政策、会议、表态信号 |
| 新闻事实层 | `industries`（`direction=bullish`） | 利好行业方向 |
| 新闻事实层 | `industries`（`direction=bearish`） | 利空行业方向（被限制、成本上升、监管收紧等） |
| 新闻事实层 | `investor_takeaways` | 对股民有参考价值的要点：总量数据、时间表、开工/投产节奏、价格与产能变化等 |
| 模型推断层 | `stocks` | 行业龙头个股，`is_leader` 标记龙头，`direction` 与所属行业一致 |
| 边界说明 | `risks` / `not_covered` / `stock_disclaimer` | 风险点、文字稿未涉及的方向、个股免责声明 |

### 关注个股的下一步操作

个股表每行带两个跳转入口，用于接着往下看，不改变本模块的任何数据：

| 操作 | 目标 | 行为 |
| --- | --- | --- |
| 问股 | `/chat?stock=<code>&name=<name>&q=<问题>&send=1` | 带着「`名称(代码)`现在适合购买吗？」跳到问股页；Agent 就绪后自动发出，Agent 不可用时问题留在输入框由用户决定是否发送 |
| 查询 | `/stock-query?code=<code>` | 跳到股票查询看板并直接查这只票 |

代码是模型推断出来的，因此先按项目既有代码规则校验：校验不过的行不给入口（显示"暂无代码"），
不把不确定的文本丢给问股或行情接口。股票查询页只支持 A 股 6 位裸代码，
港股 / 美股候选只保留"问股"。

## 长耗时与容错设计

一次分析要串行发多次模型请求，网络抖动时单次可能耗时数分钟。为此：

- **分段解读**：按 `CHUNK_MAX_CHARS`（3500 字）把窗口切成多段，每段一次调用。段越小，单段失败时损失越少。
- **拒答后逐条重试**：某段拿不到内容时改为逐条各调一次，只跳过真正拿不到内容的条目，跳过的 id 在 `coverage.skipped_item_ids` 里如实回传。刻意**不用递归二分**——实测一天 15 条里可能有一半触发内容策略，二分的调用量会指数放大并很快烧完预算（曾出现 24 次调用只覆盖 6 条）；"整段一次 + 失败后逐条一次"的调用量上界是 `1 + 每段条数`，可预测且覆盖率更高。逐条重试时产业信息更丰富的条目排在前面，预算不足时优先保住它们。
- **输入规范化**：部分国内模型通道对涉及领导人的文本会拒答并返回空 `content`，而《新闻联播》头条几乎每天涉及领导人。送入模型前会把这类词替换为角色占位（`CCTV_NEWS_LLM_REDACT_TERMS`），**只改送给模型的副本**，入库原文与前端展示的文字稿完全不变，也不会给模型引入任何新事实。
- **长窗口裁剪**：20/30 天的总字数远超单次上限时，按"有产业信息优先、日期新优先"裁剪，丢弃条数记在 `coverage.over_budget_dropped`。
- **调用预算**：`CCTV_NEWS_ANALYSIS_MAX_LLM_CALLS` 兜住成本，达到上限后剩余段落跳过并标注 `coverage.budget_exhausted`。
- **绝不报错**：全部段落都失败时返回 `degraded=true` + `degraded_reason`，并给出确定性的关键词覆盖统计，HTTP 仍为 200。只有"LLM 未配置"这类配置错误才返回 503，因为那需要用户去改配置。

## 后台任务与进度

分析走后台任务，不占用 HTTP 请求生命周期，切换路由也不会中断：

1. `POST /analysis/jobs` 提交，立即返回 `job_id`。
2. `GET /analysis/jobs/{job_id}` 轮询，返回 `phase`（`reading` / `analyzing` / `finalizing`）、`phase_label`、`total_items`、`current_chunk`/`total_chunks` 和 `progress`（0~1）。前端据此显示"共 N 篇新闻 · 正在分析第 x/y 段"。
3. 终态为 `succeeded`（`result` 内含完整分析）或 `failed`（`error` 为脱敏后的原因）。
4. 前端把任务状态放在跨路由的 zustand store 里，轮询定时器挂在模块作用域，因此用户切到任意页面进度都不丢；完成时由全局挂载的通知组件弹出提示，点击可跳回分析页。

任务只存在进程内存中，服务重启即清空，30 分钟后自动回收。

## 配置项

```env
# 本地文字稿保留天数；只清理 cctv_news_* 三张表
CCTV_NEWS_RETENTION_DAYS=60
# 单个央视网页面抓取超时（秒）
CCTV_NEWS_FETCH_TIMEOUT_SEC=10
# 送入 LLM 的文字稿总字数上限，超出时按产业信息优先裁剪
CCTV_NEWS_ANALYSIS_MAX_CHARS=60000
# 同一批文字稿 + 同一模型的分析结果复用时长（小时）；0 表示每次都重新调用 LLM
CCTV_NEWS_ANALYSIS_CACHE_HOURS=12
# 单次分析允许的模型调用次数上限（分段 + 逐条重试会放大调用量）
CCTV_NEWS_ANALYSIS_MAX_LLM_CALLS=40
# 单次模型调用超时（秒）。底层 LLM 门面未暴露 timeout，连接挂死会让后台任务永远卡住
CCTV_NEWS_ANALYSIS_CALL_TIMEOUT_SEC=120
# 送入 LLM 前需替换的词，格式 词:占位,词；留空用内置词表
CCTV_NEWS_LLM_REDACT_TERMS=
```

七项都有默认值，不配置也能用。

## API

所有接口在 `/api/v1/cctv-news` 前缀下，受与其他 v1 接口一致的中间件级鉴权保护（`ADMIN_AUTH_ENABLED` 开启时需要登录）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/days?days=30` | 按天聚合的目录（日期、条数、字数） |
| GET | `/items?news_date=&days=&query=&page=&page_size=&include_content=` | 分页查询文字稿，默认只返回摘要 |
| GET | `/items/{item_id}` | 单条文字稿全文 |
| POST | `/sync` | 同步最近 N 天（`{"days": 30, "force": false}`），已完整的日期自动跳过 |
| POST | `/analysis` | 同步分析（`{"range_days": 1\|3\|7\|10\|20\|30}` 或 `{"news_date": "2026-08-25"}`），长窗口容易超时，前端不用它 |
| POST | `/analysis/jobs` | **提交后台分析任务**，参数同上，立即返回 `job_id` |
| GET | `/analysis/jobs/{job_id}` | 轮询任务进度与结果 |
| GET | `/analysis/latest?range_days=` | 最近一次分析结果 |

给了 `news_date` 就只分析那一天（`range_days` 被忽略），这样"每天的新闻都能单独分析"。

错误语义：入参非法或窗口内没有文字稿返回 400；找不到条目/分析/任务返回 404；LLM 未配置返回 **503**（`analysis_unavailable`）。模型拒答、网络失败、解析失败都**不报错**，走降级结果。

## 落库结构

| 表 | 用途 |
| --- | --- |
| `cctv_news_items` | 单条文字稿，`url` 全局唯一作为幂等去重键 |
| `cctv_news_days` | 单日抓取状态，决定是否需要重抓 |
| `cctv_news_analyses` | 分析结果 + `evidence_signature`（同一批新闻 + 同一模型 + 同一 prompt 版本的指纹），用于命中复用避免重复调用 LLM |

新表由 `Base.metadata.create_all` 自动创建，无需迁移脚本。保留期清理由 `CctvNewsService.apply_retention()` 触发。

## 代码位置

| 层 | 文件 |
| --- | --- |
| 抓取与解析 | `src/services/cctv_news_fetcher.py` |
| 同步与查询 | `src/services/cctv_news_service.py` |
| 输入规范化（脱敏、预算裁剪） | `src/services/cctv_news_redaction.py` |
| AI 分析与事实校验 | `src/services/cctv_news_analyzer.py` |
| 后台任务与进度 | `src/services/cctv_news_jobs.py` |
| 输出契约 | `src/schemas/cctv_news_analysis.py` |
| 数据访问 | `src/repositories/cctv_news_repo.py` |
| API | `api/v1/endpoints/cctv_news.py`、`api/v1/schemas/cctv_news.py` |
| 前端页面 | `apps/dsa-web/src/pages/CctvNewsPage.tsx`、`apps/dsa-web/src/components/cctv-news/` |
| 前端跨路由任务 store | `apps/dsa-web/src/stores/cctvAnalysisStore.ts` |
| 跳转问股的文案与参数校验 | `apps/dsa-web/src/utils/chatFollowUp.ts` |
| 测试 | `tests/test_cctv_news.py`、`apps/dsa-web/src/pages/__tests__/CctvNewsPage.test.tsx`、`apps/dsa-web/src/components/cctv-news/__tests__/` |

## 使用注意

- 首次同步 30 天需要一到两分钟（每天十几个页面，串行抓取并保留礼貌间隔）；之后只补缺失的日期，很快。
- 文字稿版权归央视网所有，本模块只做本地缓存供个人研究分析使用，不做再分发。
- 个股相关输出仅供研究参考，不构成投资建议。
