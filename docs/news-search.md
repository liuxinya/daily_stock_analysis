# 新闻搜索（多平台站内搜索）

Web 端「新闻分析」页（`/news-analysis`）的「新闻搜索」tab（`/news-analysis?tab=search`）：
顶部一个搜索框，下面是五个平台 tab，把同一个关键词分别交给各站点**自己的站内搜索接口**，
只返回最近两天内、且确实提到该关键词的条目。

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
| GET | `/api/v1/news-search/items` | 参数 `q`（必填，最长 64）、`platform`（默认 `cls`）、`days`（1-30，默认 7）、`limit`（1-200，默认 50） |

`/items` 响应带 `query` 回显与 `pages_scanned`（本次向上游翻了几页），便于前端丢弃迟到响应
与排障。错误约定：未知平台或空关键词 400 `validation_error`；上游超时或响应异常 503
`upstream_unavailable`；其他 500 `internal_error`。鉴权由 `/api/v1/*` 的全局中间件覆盖。

## 新增一个平台

1. 在 `src/services/news_search_service.py` 的 `PLATFORMS` 里加一项（`time_ordered=False`
   表示上游按相关度排、不能提前停翻页）。
2. 写一个 `_fetch_<platform>_page(keyword, cursor, *, timeout) -> (entries, next_cursor)`，
   在 `_PAGE_FETCHERS` 里登记。`cursor` 传 `None` 表示首页，返回 `None` 表示没有下一页。
3. 在 `tests/test_news_search.py` 用固定 JSON 补一个归一化用例（打桩 `_http_get` / `_http_post`）。

前端不需要改动：tab 列表由 `/platforms` 接口驱动。

## 已知限制

- **上游是模糊匹配**，相关性过滤只是按关键词字面命中兜底；换了别名的关键词（如只写公司
  简称）可能命中不到。
- **单平台搜索不聚合**：每个 tab 各自打各自的上游，没有跨平台合并视图。
- **同花顺-问财返回的是综合搜索结果**，除新闻外还包含公告、研报与互动问答（由上游
  `channels` 决定），标题与摘要已归一化，但不保证都是新闻体裁。
- 各上游都可能随时加风控或改字段；命中异常时该 tab 报 503，不影响其它 tab。
