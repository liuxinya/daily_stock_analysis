# -*- coding: utf-8 -*-
"""百度财经 acs-token 进程内缓存与自签。

百度财经 ``/vapi/v1/*`` 接口（资金 ``fundflow`` / 分析 ``analysis`` 等）用一个
``acs-token`` 请求头做反爬校验，缺失即返回 HTTP 403
``{"Result":{"code":403,"isCaptchaEnabled":true,"msg":"hit risk"}}``。该 token 由
百度混淆 JS 生成，纯 Python / curl 复刻不了，必须有浏览器引擎。实测结论：

- token 跨接口、跨股票通用，寿命约 2 分钟；
- 只带 token（不带 cookie）即可 200，token 是唯一闸门；
- 风控对请求频率敏感：稀疏调用稳定 200，短时间密集并发仍会 403。

本模块维护一个进程内 TTL 缓存，两种填充来源，谁可用用谁：

- 桌面客户端：Electron 隐藏窗口抓 token 后 POST 推入（:meth:`push`）；
- 服务器 / Docker：Playwright + 镜像内 Chromium 按需自签（:meth:`_mint_via_playwright`）；
- 都不可用：:meth:`get` 返回 ``None``，调用方退回不带 token 调用并在 403 时如实报错。

:meth:`get` 永不抛异常——拿不到 token 一律返回 ``None`` 让调用方降级。
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# 任意会触发 /vapi 请求的个股页；token 与页面 / 股票无关，取一个稳定存在的即可。
_MINT_PAGE_URL = "https://finance.baidu.com/stock/ab-601058"

# 浏览器解析顺序：优先用机器上已装的 Chrome / Edge，最后退回 Playwright 自带 chromium。
# ``None`` 表示不指定 channel，交给 Playwright 用自带浏览器。
_BROWSER_CHANNELS = ("chrome", "msedge", None)

# 抓取 token 的请求特征。
_VAPI_MARKER = "/vapi/"
_TOKEN_HEADER = "acs-token"

# 自带 chromium 的默认 UA 会暴露 HeadlessChrome；统一成普通桌面 Chrome UA 降低识别面。
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 自签失败后的冷却期（秒）。自签要开浏览器、单次预算 20s 且被 ``_mint_lock`` 串行化：
# 若不冷却，浏览器装不上 / 落地页打不开时每个请求都要白等满一轮，一个页面并发几个
# 被风控的面板就会卡住几十秒。冷却期内直接返回 None，让调用方立刻降级并如实报错。
_MINT_FAILURE_COOLDOWN_SECONDS = 60.0


class BaiduFinanceTokenStore:
    """进程内 acs-token TTL 缓存，支持外部推入与 Playwright 自签两种来源。"""

    def __init__(self) -> None:
        # ``_lock`` 只保护 token / 过期时间这类快字段。
        self._lock = threading.Lock()
        # ``_mint_lock`` 串行化耗时的浏览器自签，避免并发开多个浏览器。
        self._mint_lock = threading.Lock()
        self._token: Optional[str] = None
        self._expires_at: float = 0.0
        # Playwright 资源：首次自签后进程内复用。
        self._playwright = None
        self._browser = None
        self._context = None
        # 惰性 import 失败（桌面端 / 未安装）后不再反复尝试。
        self._playwright_unavailable = False
        # 自签失败后的冷却截止时间；冷却期内 ``get`` 直接降级，不再开浏览器。
        self._mint_blocked_until: float = 0.0

    # ------------------------------------------------------------------
    # 外部推入 / 作废 / 测试隔离
    # ------------------------------------------------------------------
    def push(self, token: str, *, ttl: float) -> None:
        """接收外部（Electron）签出的 token，刷新缓存与有效期。"""
        token = (token or "").strip()
        if not token:
            return
        with self._lock:
            self._token = token
            self._expires_at = time.monotonic() + max(ttl, 0.0)
        logger.info("百度财经 token 已更新（有效期约 %.0fs）", max(ttl, 0.0))

    def invalidate(self) -> None:
        """作废当前缓存的 token（上游返回 403 风控时调用）。"""
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def reset(self) -> None:
        """清空缓存并释放浏览器资源（进程退出 / 测试隔离）。"""
        with self._lock:
            self._token = None
            self._expires_at = 0.0
        with self._mint_lock:
            self._playwright_unavailable = False
            self._mint_blocked_until = 0.0
            self._close_browser_locked()

    # ------------------------------------------------------------------
    # 取 token
    # ------------------------------------------------------------------
    def get(
        self,
        *,
        force_refresh: bool = False,
        ttl: float = 90.0,
        mint_timeout: float = 20.0,
    ) -> Optional[str]:
        """返回一个可用 token，拿不到返回 ``None``（永不抛异常）。

        命中且未过期直接返回缓存；否则尝试 Playwright 自签。桌面端没有
        Playwright，自签会直接判定不可用并返回 ``None`` —— 此时只能等
        Electron 下一次推入，调用方应退回不带 token 调用。
        """
        if not force_refresh:
            with self._lock:
                if self._token and time.monotonic() < self._expires_at:
                    return self._token
        token = self._mint_via_playwright(timeout=mint_timeout)
        if token:
            self.push(token, ttl=ttl)
            return token
        return None

    # ------------------------------------------------------------------
    # Playwright 自签
    # ------------------------------------------------------------------
    def _mint_via_playwright(self, *, timeout: float) -> Optional[str]:
        if self._playwright_unavailable:
            return None
        if time.monotonic() < self._mint_blocked_until:
            # 刚失败过：别再白等一轮浏览器，直接让调用方降级。
            return None
        try:
            from playwright.sync_api import sync_playwright  # 惰性 import
        except Exception:
            self._playwright_unavailable = True
            logger.info("Playwright 不可用，跳过百度财经 token 自签（桌面端或未安装环境属正常）")
            return None

        with self._mint_lock:
            # 等锁期间可能已有并发调用刷到新 token，复查一次缓存避免重复开浏览器。
            with self._lock:
                if self._token and time.monotonic() < self._expires_at:
                    return self._token
            # 等锁期间也可能已有并发调用失败并开启冷却，复查一次避免排队重试。
            if time.monotonic() < self._mint_blocked_until:
                return None
            try:
                token = self._capture_token_locked(sync_playwright, timeout=timeout)
            except Exception as exc:  # 浏览器异常类型随版本变化，统一按自签失败处理
                logger.warning("百度财经 token 自签失败: %s", exc)
                self._close_browser_locked()
                self._start_mint_cooldown_locked()
                return None
            if not token:
                logger.warning(
                    "百度财经 token 自签未抓到 acs-token（%.0fs 内落地页未发出 /vapi 请求）", timeout
                )
                self._start_mint_cooldown_locked()
                return None
            self._mint_blocked_until = 0.0
            return token

    def _start_mint_cooldown_locked(self) -> None:
        """自签失败后开启冷却（在 ``_mint_lock`` 下调用）。"""
        self._mint_blocked_until = time.monotonic() + _MINT_FAILURE_COOLDOWN_SECONDS
        logger.info("百度财经 token 自签进入 %.0fs 冷却期，期间直接降级", _MINT_FAILURE_COOLDOWN_SECONDS)

    def _ensure_browser_locked(self, sync_playwright) -> None:
        """确保有一个可用的浏览器 + context（在 ``_mint_lock`` 下调用）。"""
        if self._browser is not None and self._browser.is_connected():
            return
        self._close_browser_locked()
        self._playwright = sync_playwright().start()
        last_exc: Optional[Exception] = None
        for channel in _BROWSER_CHANNELS:
            launch_kwargs = {"headless": True}
            if channel:
                launch_kwargs["channel"] = channel
            try:
                self._browser = self._playwright.chromium.launch(**launch_kwargs)
                logger.info(
                    "百度财经 token 自签浏览器已启动（channel=%s）",
                    channel or "bundled-chromium",
                )
                break
            except Exception as exc:  # 该 channel 未安装则试下一个
                last_exc = exc
                continue
        if self._browser is None:
            raise RuntimeError(f"没有可用的 chromium channel: {last_exc}")
        self._context = self._browser.new_context(user_agent=_USER_AGENT, locale="zh-CN")

    def _capture_token_locked(self, sync_playwright, *, timeout: float) -> Optional[str]:
        """打开落地页，抓取其异步 ``/vapi`` 请求上的 ``acs-token``。"""
        from playwright.sync_api import Error as PlaywrightError

        self._ensure_browser_locked(sync_playwright)
        deadline = time.monotonic() + timeout
        page = self._context.new_page()
        captured: dict = {}

        def _on_request(request) -> None:
            if captured or _VAPI_MARKER not in request.url:
                return
            token = request.headers.get(_TOKEN_HEADER)
            if token:
                captured["token"] = token

        page.on("request", _on_request)
        try:
            try:
                page.goto(
                    _MINT_PAGE_URL,
                    wait_until="commit",
                    timeout=max(1.0, deadline - time.monotonic()) * 1000,
                )
            except PlaywrightError:
                pass  # 导航超时也可能已经抓到 token，继续轮询
            while not captured and time.monotonic() < deadline:
                page.wait_for_timeout(100)
        finally:
            try:
                page.remove_listener("request", _on_request)
            except Exception:
                pass
            try:
                page.close()
            except Exception:
                pass
        return captured.get("token")

    def _close_browser_locked(self) -> None:
        """释放浏览器资源（在 ``_mint_lock`` 下调用，或进程退出时）。"""
        for attr in ("_context", "_browser", "_playwright"):
            obj = getattr(self, attr, None)
            setattr(self, attr, None)
            if obj is None:
                continue
            try:
                obj.stop() if attr == "_playwright" else obj.close()
            except Exception:
                pass


# 模块级单例：全进程共享一个 token 缓存。
_store = BaiduFinanceTokenStore()


def get_token_store() -> BaiduFinanceTokenStore:
    """返回进程内共享的 token store。"""
    return _store


def reset_token_store() -> None:
    """测试隔离钩子：清空缓存并释放浏览器资源。"""
    _store.reset()


atexit.register(_store.reset)
