# 新闻搜索（多平台站内搜索）

Web 端「新闻分析」页（`/news-analysis`）的「新闻搜索」tab（`/news-analysis?tab=search`）：
顶部一个搜索框，下面是五个平台 tab，把同一个关键词分别交给各站点**自己的站内搜索接口**，
只返回最近七天内、且确实提到该关键词的条目。

首页股票分析报告的「相关资讯」面板也复用这套搜索：不再展示「报告页补充资讯」，而是拿
报告所属股票的名称（没有名称时回退到纯数字代码）去调全平台聚合接口，取实时搜索结果。

与 [实时新闻](realtime-news.md) 的区别：那边是各平台的市场级快讯 feed（不接受关键词），
这边是关键词搜索，因此每个平台的上游、参数与翻页方式都不一样。

## 平台与上游

上游都是各站点的非公开接口（浏览器抓包 + 直连验证得到），字段与可用性随时可能变化。

| tab | key | 上游 | 翻页 | 备注 |
| --- | --- | --- | --- | --- |
| 财联社 | `cls` | `POST https://www.cls.cn/api/sw` | `page`（0 起） | 签名只覆盖 `app`/`os`/`sv` 公共参数，关键词走 body；**电报（`telegram`）+ 资讯/深度（`depth`）两段各打一次再合并** |
| 同花顺-问财 | `ths` | `POST https://search.10jqka.com.cn/gateway/mobilesearch/comprehensive/search` | `offset` | body **必须**带 `channels`，缺省返回 `status_code=2` + 空列表；结果按**相关度**排 |
| 东方财富 | `eastmoney` | `GET https://search-api-web.eastmoney.com/search/jsonp` | `pageIndex`（1 起） | JSONP 外壳；结果按**相关度**排 |
| 华尔街见闻 | `wallstreetcn` | `GET https://api-one-wscn.awtmt.com/apiv1/search/live` | `cursor`（页码，首页留空） | 响应里给 `next_cursor` |
| 金十数据 | `jin10` | `GET https://search-open-api.jin10.com/offset/search` | `offset`（上一页最后一条的毫秒时间戳） | **必须**带 `x-app-id` / `x-version` 两个头，缺一即 502 |

### 东方财富对 TLS 指纹敏感

东财的搜索接口按 TLS 指纹分流：原生 `requests` 只会拿到只剩 `passportWeb` 的降级响应
（`hitsTotal=1`），`curl` 与 `curl_cffi`（Chrome 指纹）才拿得到 `cmsArticleWebOld`。
因此本模块统一走 `curl_cffi` + `impersonate="chrome"`，与百度财经代理同一套做法。
其余四家对指纹不敏感，用同一客户端只是为了保持一致。

## 归一化与筛选

1. **财联社两段合并**：`telegram`（电报快讯）与 `depth`（资讯/深度文章）要分别请求再合并。
   不能用 `type=all`——它会把 `rn` 摊薄到十几个 section 上（每段 2 条），拿不到完整的一页。
   合并后按时间倒序去重，任一段给满一页就继续翻下一页。
2. **剥高亮与标签**：上游正文里的 `<em>`（关键词高亮）与 HTML 标签先剥掉再压缩空白。
3. **相关性过滤**：各站点的搜索都是模糊匹配，翻到第二页起常混进无关快讯（见闻会退化成
   市场级直播流），因此按关键词做一次过滤——按空白切词后要求**全部**命中标题/摘要/来源。
4. **时间窗**：只保留最近 `days` 天（默认 7 天，上限 30 天）内的条目。
5. **排序与去重**：按发布时间倒序，再按标题归一化去重（同一条消息常被多站点同时发）。
6. **翻页上限**：每个平台单次最多翻 `_MAX_PAGES = 4` 页。凑满 `limit` 条或翻完就停；
   时间倒序的平台一旦翻到整页都早于窗口就提前停，相关度排序的平台没有这个保证，
   只能翻满再过滤。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/news-search/platforms` | 平台列表；前端 tab 由后端驱动 |
| GET | `/api/v1/news-search/items` | 参数 `q`（必填，最长 64）、`platform`（默认 `cls`）、`days`（1-30，默认 7）、`limit`（1-200，默认 50）；一次打一个平台 |
| GET | `/api/v1/news-search/aggregate` | 参数 `q`、`days`、`limit` 同上（无 `platform`）；并发打全部平台后合并成一个结果集 |

`/items` 响应带 `query` 回显与 `pages_scanned`（本次向上游翻了几页），便于前端丢弃迟到响应
与排障。错误约定：未知平台或空关键词 400 `validation_error`；上游超时或响应异常 503
`upstream_unavailable`；其他 500 `internal_error`。鉴权由 `/api/v1/*` 的全局中间件覆盖。

## 全平台聚合

`/aggregate` 供「只想拿一个跨平台结果」的场景使用（当前是首页「相关资讯」面板），
与单平台 `/items` 的三处关键差别：

1. **fail-open**：单个平台超时或上游异常只记入响应里的 `failed_platforms`（平台 key），
   其余平台的结果照常返回。因此这个接口**不会**因为上游问题返回 503，只有空关键词才 400。
2. **总预算 `_AGGREGATE_BUDGET_SEC = 30s`**：所有平台共享同一个墙钟 deadline，且收尾用
   `shutdown(wait=False)`，不等掉队线程。不用 `with ThreadPoolExecutor(...)` 是因为它的
   `__exit__` 会 join 全部线程，而单平台最坏要翻 4 页、每页都可能顶满 12s 超时，等下去
   会把调用方拖到近 48s。前端 `searchAll` 的超时设成 45s（单平台是 40s）。
3. **合并语义**：各平台先各自完成翻页、关键词过滤与时间窗过滤，再合并、按发布时间倒序、
   按标题去重（复用 `_dedupe_by_title`），最后切到 `limit`。因此 `total` 是切片后的条数，
   与 `items` 长度一致；`pages_scanned` 是各平台之和。

`platform` / `platform_label` 在聚合响应里固定回显 `all` / `全部平台`，它不是一个真实平台。
列表里每条带自己的 `platform` 与 `platform_label`，前端据此标出来源站点。

## 作为分析证据来源（免 Key 兜底）

站内搜索同时是分析链路的**免 Key 新闻渠道**：`src/search_service.py` 的
`SiteNewsSearchProvider` 会在「一个付费搜索渠道都没配」时注册进 `SearchService._providers`，
让 `is_available` 为 True。此前没有任何 key 时 `_providers` 为空，主流程会整段跳过情报检索、
Agent 工具直接返回 `No search engine available`，报告只能挂「⚠️ 未配置搜索渠道」。

它**不是**常驻渠道：一旦配置了任意付费渠道（Bocha / Tavily / Brave / SerpAPI / MiniMax /
Anspire / SearXNG），它就不注册——避免给这些用户每次分析多打 5 家站点，也避免两套来源混进
同一次检索。

与其他 provider 的差异：

| 维度 | 说明 |
| --- | --- |
| 可用性 | 免 Key，`is_available` 恒为 True；绕开基类的 key 轮询与 `_execute_search` |
| 维度覆盖 | **只服务 `latest_news`（最新消息）**。站内搜索是关键词字面命中检索，接不了「风险排查 / 业绩预期 / 机构分析 / 行业分析 / 公告」这类需要语义的维度——硬套会把普通新闻冒充成公告或研报。没有付费渠道时，报告里只有「最新消息」一个维度有数据 |
| 关键词 | 调用方显式传股票名（`search_stock_news` 有 `focus_keywords` 时用第一个关键词）；未传时回退到查询串的第一个空白分隔 token。**不能把整条维度查询串打给站点**——后端按关键词字面命中过滤，长查询会一条都命中不了 |
| 时间预算 | `_ANALYSIS_SITE_NEWS_BUDGET_SEC = 10s`（页面聚合是 30s）。分析是同步链路，等不起；站点第一页通常 1~3s 返回，这个预算只在某家挂死时兜底 |
| 失败语义 | 全部站点都失败才 `success=False`（对应「搜过但没拿到」→ 计数记 0），部分失败照常成功并在日志里列出失败站点 |

条目映射：`title→title`、`summary→snippet`、`url→url`、`platform_label→source`、
`published_at→published_date`。`published_at` 是带时区偏移的 ISO8601，下游
`SearchService._normalize_news_publish_date` 能直接解析，不需要改格式。

维度派发上，`search_comprehensive_intel` 会按 `provider.serves(dim['name'])` 过滤候选
provider；某个维度没有能服务的 provider 时**跳过该维度并继续后面的维度**，而不是中断整轮检索。
`BaseSearchProvider.served_dimensions` 默认 None（不限），因此所有付费渠道行为不变。

## 新增一个平台

1. 在 `src/services/news_search_service.py` 的 `PLATFORMS` 里加一项（`time_ordered=False`
   表示上游按相关度排、不能提前停翻页）。
2. 写一个 `_fetch_<platform>_page(keyword, cursor, *, timeout) -> (entries, next_cursor)`，
   在 `_PAGE_FETCHERS` 里登记。`cursor` 传 `None` 表示首页，返回 `None` 表示没有下一页。
3. 在 `tests/test_news_search.py` 用固定 JSON 补一个归一化用例（打桩 `_http_get` / `_http_post`）。

前端不需要改动：tab 列表由 `/platforms` 接口驱动。

## 已知限制

- **上游是模糊匹配**，相关性过滤只是按关键词字面命中兜底；换了别名的关键词（如只写公司
  简称）可能命中不到。正因为如此，首页「相关资讯」优先拿股票名称而不是纯数字代码去搜：
  新闻里通常写公司名，写代码的很少，用代码当关键词会被相关性过滤几乎清空。
- **聚合视图**只覆盖 `search_all` 这一条链路，页面上的五个 tab 仍是各打各的上游；聚合把
  失败面从 1 家放大到 5 家，靠 fail-open + `failed_platforms` 兜住，前端只提示「部分平台
  未返回」而不逐个列举。
- **同花顺-问财返回的是综合搜索结果**，除新闻外还包含公告、研报与互动问答（由上游
  `channels` 决定），标题与摘要已归一化，但不保证都是新闻体裁。
- 作为分析证据来源时**只覆盖「最新消息」维度**：没有付费渠道的用户，报告里其余维度仍是
  「未找到相关信息」占位，那是维度能力所限而不是检索失败。
- **港股/美股召回明显偏低**：站内搜索是中文财经站的关键词检索，英文公司名命中率低，
  这类标的仍建议配置付费渠道。
- 各上游都可能随时加风控或改字段；命中异常时该 tab 报 503，不影响其它 tab。
