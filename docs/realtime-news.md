# 实时新闻（多平台快讯浏览）

Web 端「实时新闻」页面按平台 tab 切换查看各财经站点的最新快讯。

与 [CCTV 新闻](cctv-news.md) 和本地资讯池（`NEWS_INTEL_*`）的分工：

| 模块 | 取数方式 | 是否落库 | 适用场景 |
| --- | --- | --- | --- |
| 实时新闻（本文档） | 请求时直连上游 + 进程内短 TTL 缓存 | 否 | 现在有什么新消息 |
| 本地资讯池 `IntelligenceService` | 定时/按需抓取后落库 | 是 | 按时间窗回溯消息面 |
| CCTV 新闻 | 按天抓取文字稿后落库 | 是 | 政策与产业解读 |

因为不落库，本页不配置也能用，全部配置项都有默认值。

## 平台与数据源

页面 tab 顺序即后端 `PLATFORMS` 注册表顺序（`src/services/realtime_news_service.py`）。

| tab | key | 取数方式 | 上游 |
| --- | --- | --- | --- |
| 财经热榜 | `hot` | 聚合 | 下列可用且带发布时间的平台 |
| 同花顺 | `ths` | akshare | `stock_info_global_ths` |
| 东方财富 | `eastmoney` | akshare | `stock_info_global_em` |
| 华尔街见闻 | `wallstreetcn` | NewsNow | `wallstreetcn-quick` |
| 雪球 | `xueqiu` | 直连雪球 | `/statuses/livenews/list.json`（7×24 快讯） |
| 金十数据 | `jin10` | NewsNow | `jin10` |
| 财新 | `caixin` | akshare | `stock_news_main_cx` |
| 新浪财经 | `sina` | akshare | `stock_info_global_sina` |
| 第一财经 | `yicai` | 直连第一财经 | `/api/ajax/getbrieflist`（7×24 快讯） |
| 格隆汇 | `gelonghui` | NewsNow | `gelonghui` |

### 直连上游的共同约定

雪球与第一财经都没走 akshare / NewsNow，而是直连站点接口。两者共用同一套翻页与
超时语义（`_fetch_cursor_pages`），改动前请先确认这几条仍然成立：

- **超时语义与其他平台不同**：因为一次抓取可能要打多个请求，
  `REALTIME_NEWS_FETCH_TIMEOUT_SEC` 在这里是**整轮抓取的总预算**而不是单请求超时。
  预算耗尽或中途某页失败时返回已抓到的部分（可能少于配置条数），
  只有第一页就失败才算该平台不可用。
- **游标翻页**：首页不带游标，之后每页用上一页推出来的游标；游标不前进、
  没有新条目或够数了就停，并有每平台的最大页数兜底。
- **外链白名单**：链接会被前端直接渲染成 `<a href>`，适配器只放行 `https://`
  与站内相对路径（`http://` 升到 https），其余置空。

### 雪球 7×24 快讯的接口约定

选直连而不是上游聚合，是因为 akshare 与 NewsNow 给的都只是热门股榜单
（股票名 + 涨跌幅），不是新闻。

- **必须先取匿名 token**：快讯接口要求 `xq_a_token` cookie，缺失时返回 `400` +
  `error_code 400016`。该 cookie 由行情页 `https://xueqiu.com/hq` 下发，站点首页
  `/` 只下发 WAF 的 `acw_tc`，拿不到 token。会话在进程内复用，被拒时重建一次再重试。
  **不需要雪球账号，也不读取任何用户凭据**，因此没有对应配置项。
- **每页硬性 10 条**：`count` 参数无效（传 10 / 50 / 100 返回的都是 10 条），
  要凑够 `REALTIME_NEWS_MAX_ITEMS_PER_PLATFORM` 只能翻页，最多 10 页。
- **游标**：`max_id` 是开区间上界，取上一页响应里的 `next_max_id`。
- **字段映射**：上游只给一个 `text`（`【标题】正文` 形式，约半数条目没有 `【】`，
  这时按新浪的做法用正文开头截取标题）、`created_at`（epoch 毫秒）、
  `target`（永久链接，固定 `http://`）、`mark`
  （`1` = 站点标红的重要快讯，映射成列表里的 `重要` 徽标）。

### 第一财经 7×24 快讯的接口约定

- **无需鉴权**：`GET https://www.yicai.com/api/ajax/getbrieflist`，不需要 cookie，
  UA / `Referer` / `X-Requested-With` 也都不是必需的（仍按浏览器行为带上）。
- **`pagesize` 上限 50，且越界不报错**：传 >50 时上游仍返回 HTTP `200`，但 body 是
  把提示串逐字符拆开的 JSON 数组（`["p","a","g","e",...]`）。适配器按「首个元素不是
  对象」显式识别并报上游异常，否则会被当成一页合法但全被跳过的数据静默缓存成空列表。
  `REALTIME_NEWS_MAX_ITEMS_PER_PLATFORM` 超过 50 时按 50 分页，多打一次请求。
- **游标**：`nid` = 上一页最后一条的 `LiveID`，开区间；给了 `nid` 时上游按它定位窗口，
  `page` 只作陪衬。上游不给「还有没有下一页」的标记，所以靠「本页不足 `pagesize`」判断到底。
- **字段映射**：`LiveTitle`（缺失时退到 `NewsTitle`）、`LiveContent`（正文）、
  `CreateDate`（北京本地 naive，如 `2026-09-10T15:23:22`）、`url`（站内相对路径
  `/brief/<id>.html`，VIP 稿件是 `/vip/brief/...`，适配器补成绝对地址）、
  `IsImportant`（映射成 `重要` 徽标）。
- **VIP 稿件**（`LiveType=1`）正文为空、只有标题，仍然保留：标题本身是有效快讯，
  摘要留空由前端不渲染。

### 已知的上游限制

- **财新**：上游只返回 `tag / summary / url`，没有标题也没有发布时间。标题用摘要开头截取，并且**不参与「财经热榜」的时间倒序**（无时间无法排序）。
- **新浪财经**：只返回「时间 + 内容」，没有链接，标题从内容开头的 `【】` 中抽取；单次调用只覆盖约 10 分钟的消息量。
- **雪球**：内容以财联社快讯为主，与同花顺 / 新浪 / 东财重合度高，进「财经热榜」后大部分会被标题去重吃掉，这是预期行为。
- **第一财经**：单次调用（50 条）覆盖约 3 小时，是各平台里跨度较大的一个；其中约 1/4 带 `重要` 标记。上游单请求实测约 3~4 秒（雪球约 0.2 秒），因此把 `REALTIME_NEWS_MAX_ITEMS_PER_PLATFORM` 调到 50 以上会触发第二次请求，接近默认 8 秒的总预算，容易只拿到部分结果。
- 各平台单次调用覆盖的时间跨度差异很大（新浪约 10 分钟、财联社约 27 分钟、同花顺约 31 分钟、富途约 102 分钟、东财约 213 分钟）。本页是浏览用途不受影响，但**不要**把它当作增量采集的依据。

当前注册表里每个 tab 都有可用上游（原「界面新闻」tab 因既无 RSS 也不在 NewsNow 源列表内，已直接移除而不再占位）。`available: false` 的契约仍然保留：这类平台接口正常返回 200 与空列表，前端展示占位说明、不报错，后续要加「已知但暂无源」的平台时直接在注册表加一行即可。

## 时间基准

所有 `published_at` 都是 **timezone-aware**，序列化为带偏移的 ISO8601，例如 `2026-09-07T19:13:00+08:00`。

- akshare 返回北京本地 naive 时间，适配器补 `Asia/Shanghai`
- NewsNow 返回 epoch 毫秒（UTC 基准），适配器补 UTC
- 雪球 `created_at` 是 epoch 毫秒（UTC 基准），适配器落到 `Asia/Shanghai`
- 第一财经 `CreateDate` 是北京本地 naive ISO，适配器补 `Asia/Shanghai`

相对时间（「1小时前」）由前端按浏览器本地时间计算，后端不参与。

这样做是为了避免与本地资讯池同类问题重合：`IntelligenceService` 把 `published_at` 归一化成 naive UTC，而 `fetched_at` 和时间窗 cutoff 用的是本地 `datetime.now()`，两者混用会有 8 小时偏差。

## 「财经热榜」聚合规则

1. 并发拉取所有 `available` 且 `has_published_at` 的平台
2. **fail-open**：单平台失败只记入响应的 `failed_platforms`，不影响其他平台；前端顶部提示哪些平台没拉到
3. 先按发布时间倒序，再按标题归一化去重（同一条消息常被多平台同时发，且新浪不给 url），因此同一条消息保留最新的平台版本

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/realtime-news/platforms` | 平台列表；前端 tab 由后端驱动 |
| GET | `/api/v1/realtime-news/items` | 参数 `platform`（默认 `hot`）、`page`、`page_size`（1-100，默认 20） |

错误约定：未知平台 400 `validation_error`；上游超时或响应异常 503 `upstream_unavailable`；其他 500 `internal_error`。鉴权由 `/api/v1/*` 的全局中间件覆盖。

## 配置

全部可选，详见 `.env.example` 中「实时新闻（多平台快讯浏览）配置」一节。

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `REALTIME_NEWS_CACHE_TTL_SEC` | 60 | 单平台进程内缓存时长（秒）；0 表示每次都打上游 |
| `REALTIME_NEWS_FETCH_TIMEOUT_SEC` | 8 | 单平台上游超时（秒）；雪球 / 第一财经是整轮翻页的总预算 |
| `REALTIME_NEWS_MAX_ITEMS_PER_PLATFORM` | 50 | 单平台单次最多返回条数 |

akshare 接口本身不暴露 timeout，模块内用独立线程兜 deadline，超时按「该平台不可用」处理。

雪球与第一财经都没有独立配置项：前者的匿名 token 由代码自动 bootstrap，后者无需鉴权，都不需要账号。

## 页面与截图字段的差异

上游快讯接口只提供标题、摘要、发布时间、链接，**不提供缩略图、阅读数、点赞数和作者**，因此列表行不做这些字段的占位。

## 新增一个平台

1. 在 `src/services/realtime_news_service.py` 的 `PLATFORMS` 里加一项
2. akshare 源在 `_AKSHARE_COLUMN_SPECS` 里补列名映射；NewsNow 源只需填 `upstream` 为 source id；
   两者都覆盖不了时才新增 provider：`PROVIDER_*` 常量 + `normalize_*_payload` 纯函数 +
   `_fetch_*` 网络函数 + `_fetch` 里加一条分支。需要翻页的直连源复用
   `_fetch_cursor_pages`，只提供「取一页」与「推下一页游标」两个回调（参考雪球 / 第一财经）
3. 在 `tests/test_realtime_news.py` 里用固定 records 补一个归一化用例；自建 provider 还要在
   HTTP 边界打桩覆盖翻页、鉴权续期与失败降级

前端不需要改动：tab 列表由 `/platforms` 接口驱动。
