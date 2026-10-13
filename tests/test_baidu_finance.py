# -*- coding: utf-8 -*-
"""百度财经代理与 acs-token 缓存的回归测试。

覆盖三层，均不联网、不开浏览器：

- token store：push / TTL 过期 / invalidate / 自签不可用时返回 None 而非抛错；
- 代理端点：带 / 不带 token 的请求头组装，403 风控 → 刷新重试一次 → 仍失败 503；
- ``POST /_token``：未配置密钥 404、非回环 403、密钥不匹配 403、正确推入写缓存。
"""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from contextlib import ExitStack
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.v1.endpoints import baidu_finance
from src.services.baidu_finance_token import BaiduFinanceTokenStore


def _fake_playwright_modules() -> dict:
    """伪造 ``playwright.sync_api``，让惰性 import 成功而不真的装 Playwright。

    自签的浏览器交互由测试另行打桩（``_capture_token_locked``），这里只需要
    让 ``from playwright.sync_api import sync_playwright`` 这一行过得去，
    以便在未安装 Playwright 的环境里也能覆盖冷却逻辑。
    """
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: None  # type: ignore[attr-defined]
    sync_api.Error = RuntimeError  # type: ignore[attr-defined]
    package = ModuleType("playwright")
    package.sync_api = sync_api  # type: ignore[attr-defined]
    return {"playwright": package, "playwright.sync_api": sync_api}


def _config(**overrides):
    base = {
        "baidu_finance_token_enabled": True,
        "baidu_finance_token_ttl_sec": 90,
        "baidu_finance_token_push_secret": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeResponse:
    """最小化模拟 curl_cffi 响应。"""

    def __init__(self, status_code: int, payload=None, *, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


_RISK_BODY = {"Result": {"code": 403, "isCaptchaEnabled": True, "msg": "hit risk"}}


class TokenStoreTest(unittest.TestCase):
    """进程内 token 缓存：命中 / 过期 / 作废 / 自签不可用。"""

    def setUp(self) -> None:
        self.store = BaiduFinanceTokenStore()

    def tearDown(self) -> None:
        self.store.reset()

    def test_push_then_get_returns_cached_token(self) -> None:
        self.store.push("TOKEN", ttl=90)
        # 命中缓存时不应触发自签。
        with patch.object(self.store, "_mint_via_playwright", side_effect=AssertionError("mint 不应被调用")):
            self.assertEqual(self.store.get(), "TOKEN")

    def test_expired_token_triggers_mint(self) -> None:
        self.store.push("OLD", ttl=0.0)
        time.sleep(0.01)
        with patch.object(self.store, "_mint_via_playwright", return_value="FRESH") as mint:
            self.assertEqual(self.store.get(ttl=90), "FRESH")
        mint.assert_called_once()
        # 自签成功后应回填缓存，下一次直接命中。
        with patch.object(self.store, "_mint_via_playwright", side_effect=AssertionError("不应再签")):
            self.assertEqual(self.store.get(), "FRESH")

    def test_mint_failure_returns_none_without_raising(self) -> None:
        with patch.object(self.store, "_mint_via_playwright", return_value=None):
            self.assertIsNone(self.store.get(force_refresh=True))

    def test_invalidate_clears_cache(self) -> None:
        self.store.push("TOKEN", ttl=90)
        self.store.invalidate()
        with patch.object(self.store, "_mint_via_playwright", return_value=None):
            self.assertIsNone(self.store.get())

    def test_playwright_unavailable_short_circuits(self) -> None:
        self.store._playwright_unavailable = True
        # 不应尝试 import playwright，直接返回 None。
        self.assertIsNone(self.store._mint_via_playwright(timeout=0.1))

    def test_mint_failure_opens_cooldown_then_recovers(self) -> None:
        """自签失败后进入冷却：冷却期内不再开浏览器，冷却过期后可恢复。

        自签单次预算 20s 且被 ``_mint_lock`` 串行化，若每个请求都白等满一轮，
        一个页面并发几个被风控的面板就会卡住几十秒。
        """
        captures: list[float] = []

        def _capture_nothing(_sync_playwright, *, timeout):
            captures.append(timeout)
            return None  # 落地页始终没发出 /vapi 请求

        with patch.dict(sys.modules, _fake_playwright_modules()):
            with patch.object(self.store, "_capture_token_locked", side_effect=_capture_nothing):
                self.assertIsNone(self.store.get(force_refresh=True, mint_timeout=0.1))
                # 冷却期内直接降级，不再进 _capture_token_locked。
                self.assertIsNone(self.store.get(force_refresh=True, mint_timeout=0.1))
            self.assertEqual(len(captures), 1)

            # 冷却到期后恢复自签，成功后清掉冷却标记。
            self.store._mint_blocked_until = time.monotonic() - 1.0
            with patch.object(self.store, "_capture_token_locked", return_value="FRESH"):
                self.assertEqual(self.store.get(force_refresh=True, ttl=90), "FRESH")
        self.assertEqual(self.store._mint_blocked_until, 0.0)

    def test_mint_exception_also_opens_cooldown(self) -> None:
        with patch.dict(sys.modules, _fake_playwright_modules()):
            with patch.object(
                self.store, "_capture_token_locked", side_effect=RuntimeError("浏览器启动失败")
            ) as capture:
                # 浏览器异常不外抛，按自签失败处理。
                self.assertIsNone(self.store.get(force_refresh=True, mint_timeout=0.1))
                self.assertIsNone(self.store.get(force_refresh=True, mint_timeout=0.1))
            self.assertEqual(capture.call_count, 1)
        self.assertGreater(self.store._mint_blocked_until, time.monotonic())


class _FakeStore:
    """替身 token store：记录调用，不开浏览器、不碰真实单例。"""

    def __init__(self, tokens=None):
        self._tokens = list(tokens or [])
        self.get_calls = []  # 依次记录每次 get 的 force_refresh
        self.invalidated = 0
        self.pushed = []  # 依次记录 (token, ttl)

    def get(self, *, force_refresh=False, ttl=90.0, mint_timeout=20.0):
        self.get_calls.append(force_refresh)
        return self._tokens.pop(0) if self._tokens else None

    def invalidate(self):
        self.invalidated += 1

    def push(self, token, *, ttl):
        self.pushed.append((token, ttl))


class _FakeCurl:
    """替身 curl_cffi.get：按序回放响应并记录每次请求头。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []  # 依次记录每次请求的 headers 副本

    def __call__(self, url, *, params, headers, timeout, impersonate):
        self.calls.append(dict(headers))
        if not self._responses:
            raise AssertionError("curl 调用次数超出预期")
        return self._responses.pop(0)


def _app():
    app = FastAPI()
    app.include_router(baidu_finance.router)
    return app


class _FakeRequest:
    """伪造 Starlette Request：精确控制来源地址 / 请求头 / 请求体。

    测试工具的 ASGITransport 固定把 ``client.host`` 置为回环，无法覆盖
    非回环分支，故 ``_token`` 端点测试直接调用处理函数并注入本对象。
    """

    def __init__(self, *, host, headers=None, body=None):
        self.client = SimpleNamespace(host=host) if host is not None else None
        self.headers = headers or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no json body")
        return self._body


def _patch_endpoint(config, store, curl=None):
    stack = ExitStack()
    stack.enter_context(patch.object(baidu_finance, "get_config", lambda: config))
    stack.enter_context(patch.object(baidu_finance, "get_token_store", lambda: store))
    if curl is not None:
        stack.enter_context(patch.object(baidu_finance.curl_requests, "get", curl))
    return stack


class ProxyEndpointTest(unittest.TestCase):
    """代理端点：请求头组装 + 403 风控刷新重试。"""

    def test_token_disabled_sends_no_acs_token(self) -> None:
        store = _FakeStore()
        curl = _FakeCurl([_FakeResponse(200, {"ok": 1})])
        with _patch_endpoint(_config(baidu_finance_token_enabled=False), store, curl):
            resp = TestClient(_app()).get("/fundflow")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": 1})
        # 关闭 token 时既不取 token，也不在请求头里带 acs-token。
        self.assertEqual(store.get_calls, [])
        self.assertNotIn("acs-token", curl.calls[0])

    def test_token_enabled_attaches_acs_token(self) -> None:
        store = _FakeStore(tokens=["TOK"])
        curl = _FakeCurl([_FakeResponse(200, {"ok": 1})])
        with _patch_endpoint(_config(), store, curl):
            resp = TestClient(_app()).get("/fundflow")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(store.get_calls, [False])
        self.assertEqual(curl.calls[0]["acs-token"], "TOK")

    def test_risk_block_refreshes_token_once_then_succeeds(self) -> None:
        store = _FakeStore(tokens=["TOK1", "TOK2"])
        curl = _FakeCurl([
            _FakeResponse(403, _RISK_BODY),
            _FakeResponse(200, {"ok": 1}),
        ])
        with _patch_endpoint(_config(), store, curl):
            resp = TestClient(_app()).get("/fundflow")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": 1})
        # 第一次失败后作废旧 token，强制刷新取新 token 重试一次。
        self.assertEqual(store.get_calls, [False, True])
        self.assertEqual(store.invalidated, 1)
        self.assertEqual(curl.calls[0]["acs-token"], "TOK1")
        self.assertEqual(curl.calls[1]["acs-token"], "TOK2")

    def test_risk_block_twice_returns_503_risk_control(self) -> None:
        store = _FakeStore(tokens=["TOK1", "TOK2"])
        curl = _FakeCurl([
            _FakeResponse(403, _RISK_BODY),
            _FakeResponse(403, _RISK_BODY),
        ])
        with _patch_endpoint(_config(), store, curl):
            resp = TestClient(_app()).get("/fundflow")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"]["error"], "upstream_risk_control")
        # 只刷新一次（False→True），不无限重试。
        self.assertEqual(store.get_calls, [False, True])
        self.assertEqual(store.invalidated, 1)

    def test_risk_block_without_token_returns_503_immediately(self) -> None:
        # 关闭 token（降级模式）：命中风控直接如实报 503，不尝试刷新。
        store = _FakeStore()
        curl = _FakeCurl([_FakeResponse(403, _RISK_BODY)])
        with _patch_endpoint(_config(baidu_finance_token_enabled=False), store, curl):
            resp = TestClient(_app()).get("/fundflow")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"]["error"], "upstream_risk_control")
        self.assertEqual(store.get_calls, [])
        self.assertEqual(store.invalidated, 0)

    def test_non_risk_upstream_error_returns_502(self) -> None:
        store = _FakeStore(tokens=["TOK"])
        curl = _FakeCurl([_FakeResponse(500, {"whatever": True})])
        with _patch_endpoint(_config(), store, curl):
            resp = TestClient(_app()).get("/fundflow")
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["detail"]["error"], "upstream_error")


class PushTokenEndpointTest(unittest.TestCase):
    """``POST /_token``：密钥 / 回环双重保护与写缓存。

    直接调用处理函数并注入伪造 Request，以精确控制来源地址——测试工具的
    ASGITransport 固定把 ``client.host`` 置为回环，无法覆盖非回环分支。
    """

    def _call(self, request, config, store):
        with _patch_endpoint(config, store):
            return asyncio.run(baidu_finance.push_token(request))

    def test_missing_secret_returns_404(self) -> None:
        store = _FakeStore()
        req = _FakeRequest(host="127.0.0.1", body={"token": "ABC"})
        with self.assertRaises(HTTPException) as ctx:
            self._call(req, _config(baidu_finance_token_push_secret=""), store)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail["error"], "not_found")
        self.assertEqual(store.pushed, [])

    def test_non_loopback_client_returns_403(self) -> None:
        store = _FakeStore()
        req = _FakeRequest(
            host="10.0.0.5",
            headers={"x-dsa-token-secret": "s3cret"},
            body={"token": "ABC"},
        )
        with self.assertRaises(HTTPException) as ctx:
            self._call(req, _config(baidu_finance_token_push_secret="s3cret"), store)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(store.pushed, [])

    def test_wrong_secret_returns_403(self) -> None:
        store = _FakeStore()
        req = _FakeRequest(
            host="127.0.0.1",
            headers={"x-dsa-token-secret": "wrong"},
            body={"token": "ABC"},
        )
        with self.assertRaises(HTTPException) as ctx:
            self._call(req, _config(baidu_finance_token_push_secret="s3cret"), store)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(store.pushed, [])

    def test_correct_secret_pushes_token(self) -> None:
        store = _FakeStore()
        req = _FakeRequest(
            host="127.0.0.1",
            headers={"x-dsa-token-secret": "s3cret"},
            body={"token": "ABC", "ttl": 120},
        )
        result = self._call(req, _config(baidu_finance_token_push_secret="s3cret"), store)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(store.pushed, [("ABC", 120.0)])

    def test_missing_token_field_returns_400(self) -> None:
        store = _FakeStore()
        req = _FakeRequest(
            host="127.0.0.1",
            headers={"x-dsa-token-secret": "s3cret"},
            body={"ttl": 30},
        )
        with self.assertRaises(HTTPException) as ctx:
            self._call(req, _config(baidu_finance_token_push_secret="s3cret"), store)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail["error"], "invalid_body")
        self.assertEqual(store.pushed, [])


if __name__ == "__main__":
    unittest.main()
