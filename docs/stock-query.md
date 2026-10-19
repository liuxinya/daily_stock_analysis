# 股票查询（仿百度金融）接入说明

> **现状提示（2026-10）**：「股票查询」页已改为自有行情 + 多周期 K 线，见
> [图表数据说明](chart-data.md)。本文描述的百度财经代理与 `acs-token` 签名链路
> **后端仍完整保留**（`api/v1/endpoints/baidu_finance.py`、`src/services/baidu_finance_token.py`、
> `BAIDU_FINANCE_*` 配置、桌面端 Electron 推 token），但**前端已无调用方** ——
> 概览/资金/简况/股评/资讯/财务/分析七个百度 tab 随该次改动一并移除。
> 保留后端是为了不误伤桌面端 token 链路与镜像构建；是否连同 Playwright / Chromium
> 依赖一起下线，属于单独的部署决策，尚未做。下文内容对后端代理本身仍然有效。

「股票查询」曾是 Web / 桌面端的个股看板（仿百度金融），数据来自百度财经
`finance.pae.baidu.com` 的一组非公开接口，由后端 `api/v1/endpoints/baidu_finance.py`
统一代理：上游路径走固定白名单，绝不接受调用方传入地址，避免变成任意地址转发（SSRF）；
上游按 TLS 指纹拦截客户端（httpx / requests 一律 403），因此走 `curl_cffi` 的浏览器指纹模拟。

本文件说明其中「资金」「分析」两个 tab 曾依赖的 `acs-token` 反爬签名：为什么需要、
两种 token 来源、寿命与降级行为、相关配置与排障。

## 为什么需要 acs-token

百度财经 `/vapi/v1/*` 接口（资金 `fundflow`、分析 `analysis` 等）对客户端做反爬校验，
裸调（只带 TLS 指纹、不带签名头）稳定返回 HTTP 403，body 形如：

```json
{"Result": {"code": 403, "isCaptchaEnabled": true, "msg": "hit risk"}}
```

实测结论：

- 真实浏览器访问同样接口全部 200，差别是每个 `/vapi/v1/` 请求都带了一个 `acs-token`
  请求头（百度混淆 JS 生成的签名，约 450~500 字符）。
- 只带 `acs-token`（不带 cookie）即可 200；只带 cookie（不带 token）仍 403。
  `acs-token` 是唯一闸门。
- token 跨接口、跨股票通用（`fundflow` 签出的 token 打 `analysis` / 其它股票均 200），
  寿命约 2 分钟。
- token 由混淆 JS 计算，纯 Python / curl 复刻不了，必须有浏览器引擎。
- 风控对请求频率敏感：稀疏调用稳定 200；短时间密集并发（约 10 秒内十余次）仍会全线 403。

前端实际只有「资金」(`fundflow`) 与「分析」(`analysis`) 两个 tab 会打被风控的接口，
各只发一个请求，正常使用不会触发频率风控；其它 tab 的接口本来不带 token 也 200。

## 两种 token 来源 → 一个进程内缓存

后端只维护一个进程内 TTL 缓存（`src/services/baidu_finance_token.py` 的模块级单例），
两种填充方式，谁可用用谁：

| 环境 | 来源 | 机制 |
| --- | --- | --- |
| 桌面客户端 | Electron 内置 Chromium | 主进程隐藏窗口加载百度个股页，抓 `/vapi/` 请求头里的 `acs-token`，POST 推给本地后端 `/_token` |
| 服务器 / Docker | Playwright + 镜像内 Chromium | 后端按需自签（惰性 import，未安装即视为不可用） |
| 都没有 | 降级 | 不带 token 调用；命中 403 时返回如实文案 |

代理端点取 token 的时机：收到前端请求时先取缓存 token 合并进请求头；若上游仍判风控
（HTTP 403 或 body `Result.code==403` / `isCaptchaEnabled` / `msg` 含 `risk`），且本次带了
token，则作废旧 token、强制刷新取新 token **重试一次**；仍失败才如实报错，不无限重试。

### 桌面客户端链路（Electron）

桌面端不打包 Playwright，沿用「分享」图片渲染的既有约定——用 Electron 自带的隐藏
Chromium 窗口完成，无需额外携带浏览器。签名器在 `apps/dsa-desktop/main.js`：

- 后端健康检查通过后，启动一个常驻定时器（约 60s，略小于 token 寿命）：隐藏 `BrowserWindow`
  加载百度个股页，用独立 `partition` 会话（不污染主窗口 session），在
  `webRequest.onBeforeSendHeaders` 上读 `/vapi/` 请求的 `acs-token`；
- 抓到后用 `http.request` POST 到本地后端 `/api/v1/baidu-finance/_token`，带共享密钥头
  `x-dsa-token-secret`；
- `window-all-closed` / `before-quit` 时清理定时器并销毁隐藏窗口。

共享密钥由桌面端启动时随机生成，经环境变量 `BAIDU_FINANCE_TOKEN_PUSH_SECRET` 传给内置后端，
仅在本机内存共享，不落盘、不需手动配置。

### 服务器 / Docker 链路（Playwright）

服务器镜像内置 Chromium（`docker/Dockerfile` 执行 `playwright install --with-deps chromium`，
浏览器装到共享目录 `/opt/ms-playwright`，非 root 的 `dsa` 用户只读即可）。后端
`_mint_via_playwright` 惰性 import `playwright.sync_api`：

- 浏览器解析顺序 `channel="chrome"` → `"msedge"` → Playwright 自带 chromium
  （本地有装 Chrome/Edge 时直接复用）；
- 启动一次后进程内复用 browser/context，用 `page.on("request")` 抓 `/vapi/` 请求的 `acs-token`；
- Playwright 未安装（桌面端产物已 `--exclude-module playwright`，或裸机未装）即判定不可用，
  直接返回 `None`，不报错；
- **自签失败后进入 60s 冷却**（浏览器装不上 / 落地页打不开 / 超时没抓到 token）：冷却期内
  直接降级，不再开浏览器。否则单次 20s 预算 × 串行锁会让一个页面的多个被风控面板白等几十秒。

> **本地直接起后端（非 Docker、非桌面）要先装依赖**：`python main.py --serve` / `uvicorn` 这类
> 裸机运行方式走的是服务器链路，但旧环境不会自动装上新依赖。若日志出现
> `Playwright 不可用，跳过百度财经 token 自签`，说明两个 token 来源都没有，资金 / 分析（ETF 还包括
> 日 K）会一律显示「上游触发风控限流」。按下面任一方式补齐后**重启后端**：
>
> ```bash
> pip install -r requirements.txt          # 装上 playwright（已在 requirements 中）
> python -m playwright install chromium    # 本机已装 Chrome / Edge 可跳过，自签会优先复用
> ```

### 降级行为与错误文案

两种来源都拿不到 token 时（或 `BAIDU_FINANCE_TOKEN_ENABLED=false`），后端退回不带 token 调用；
命中风控时返回 **HTTP 503 + `upstream_risk_control`**，文案如实说明「上游（百度财经）触发风控限流，
与本地网络 / 代理 / DNS 无关，请稍后重试」。前端 `apps/dsa-web/src/api/error.ts` 对该错误码有
专门分支，不再误报成「服务端无法访问外部依赖 / 请检查代理、DNS」。资金 / 分析 tab 在拿不到数据时
渲染「暂无数据」。

> 注意：`finance.pae.baidu.com` 是国内直连数据源，已在 `src/config.py` 的 NO_PROXY 列表加入
> `baidu.com`，避免用户配了代理时被代理劫走。

## 配置项

均为可选，不配置也能运行（见 `.env.example` 的「股票查询（仿百度金融）acs-token 配置」小节）：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `BAIDU_FINANCE_TOKEN_ENABLED` | `true` | 总开关；设为 `false` 即退回不带 token + 如实报错的现状 |
| `BAIDU_FINANCE_TOKEN_TTL_SEC` | `90` | 进程内 token 缓存时长（秒，范围 10~300）。上游寿命约 2 分钟，默认 90 留足刷新余量 |
| `BAIDU_FINANCE_TOKEN_PUSH_SECRET` | 空 | 桌面端推入 token 的共享密钥；桌面端自动随机生成。服务器模式**不要设置**，留空时 `/_token` 端点直接 404 禁用 |

`/_token` 端点双重保护：仅接受本机回环地址（`127.0.0.1` / `::1` / `localhost`）+ 密钥匹配；
未配置密钥时端点直接 404（不泄露其存在）。

## 排障

- **资金 / 分析 tab 报「上游触发风控限流」**：上游风控，稍后重试即可；若持续，确认 token 来源可用
  （桌面端看 `logs/desktop.log` 有无推入日志；服务器端确认镜像内 Chromium 可用）。
- **ETF 连「概览」的日 K 也报风控**：ETF 的日 K 走的是被风控的 `quotation`（个股走未被风控的
  `analysis-quotation`），所以**没有 token 时 ETF 比个股多坏一个面板**。按下面一条补齐 token 来源即可。
- **日志出现「Playwright 不可用」**：惰性 import 失败。裸机 / 桌面端产物属正常；若你是本地直接起后端
  又想看这几个 tab，按上文「本地直接起后端」装上 `playwright` 并重启。
- **日志出现「自签进入 60s 冷却期」**：浏览器起不来或落地页没发出 `/vapi` 请求。确认装了
  Chrome / Edge 或 `python -m playwright install chromium`，以及本机能打开 `finance.baidu.com`。
- **一键退回现状**：`BAIDU_FINANCE_TOKEN_ENABLED=false`。

## 风险

- **上游脆弱性未消除**：百度随时可能改签名算法 / 换头名 / 上滑块验证码，届时两条链路都要跟修；
  `acs-token` 寿命仅约 2 分钟，刷新节奏不能省。
- **密集并发仍会触发风控**：资金 / 分析各只发 1 个 gated 请求，正常使用没问题；若将来有页面并发打
  多个 gated 接口，靠「刷新重试 + 如实文案」兜底，必要时再加频率限制。
- **桌面端周期性访问百度**：每约 60s 加载一次百度页，有少量 CPU / 网络开销与隐私面。
- **Docker 镜像变大**：内置 Chromium 使镜像增加数百 MB，见 `docs/DEPLOY.md`。

