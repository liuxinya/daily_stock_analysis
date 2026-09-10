# -*- coding: utf-8 -*-
"""消息面汇总的回归测试。

grounding 与分段用固定数据做确定性断言，不打网络；analyzer 层 mock 到
``generate_text_with_metadata`` 这一层门面，覆盖缓存、降级、503 与 deadline。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from src.config import Config
from src.services.market_brief_analyzer import (
    BEGIN_ITEMS,
    END_ITEMS,
    BriefItem,
    MarketBriefAnalyzer,
    MarketBriefAnalyzerError,
    MarketBriefUnavailableError,
    build_chunk_prompt,
    merge_chunk_payloads,
    prepare_items,
    split_into_chunks,
    validate_grounding,
)
from src.services.market_brief_service import MarketBriefService
from src.services.market_brief_window import resolve_brief_window
from src.storage import DatabaseManager

SH = ZoneInfo("Asia/Shanghai")


def _window(hour: int = 21, day: int = 6):
    return resolve_brief_window(market="cn", current_time=datetime(2026, 9, day, hour, 0, tzinfo=SH))


def _items():
    return [
        BriefItem(id=1, title="工信部：全面建成新一代通信网", summary="工信部发文提出通信网建设目标。",
                  source="财联社电报", published_at="2026-09-06T10:00:00"),
        BriefItem(id=2, title="某公司股东拟减持", summary="减持不超过总股本 2%。",
                  source="财联社电报", published_at="2026-09-06T11:00:00"),
    ]


def _payload(**overrides):
    base = {
        "overall_tone": "bullish",
        "summary": "政策面偏积极。",
        "events": [{"topic": "工信部发文", "detail": "通信网", "category": "政策", "importance": "high",
                    "evidence_ids": [1], "mentioned_terms": ["通信网"]}],
        "sectors": [{"name": "通信", "direction": "bullish", "strength": "medium", "reason": "政策支持",
                     "evidence_ids": [1], "mentioned_terms": ["通信网"]}],
        "watch_list": [{"name": "中兴通讯", "code": "000063", "sector": "通信", "evidence_ids": [1]}],
        "takeaways": [{"topic": "关注通信", "detail": "看放量", "evidence_ids": [1], "mentioned_terms": ["通信网"]}],
        "risks": ["落地节奏不确定"], "keywords": ["通信"], "not_covered": ["海外"],
    }
    base.update(overrides)
    return base


def _stub_analyzer(text: str, *, model: str = "stub-model"):
    generation = SimpleNamespace(
        text=text, model=model, provider="stub",
        usage={"total_tokens": 900, "prompt_tokens": 800, "completion_tokens": 100},
    )
    analyzer = Mock()
    analyzer.generate_text_with_metadata.return_value = generation
    analyzer.get_generation_backend_config_error.return_value = None
    analyzer.is_available.return_value = True
    analyzer.get_generation_backend_identity.return_value = ("stub", model)
    return analyzer


class GroundingTest(unittest.TestCase):
    def test_terms_not_in_corpus_drop_the_whole_entry(self) -> None:
        payload = _payload(sectors=[{
            "name": "光伏", "direction": "bullish", "evidence_ids": [1],
            "mentioned_terms": ["硅料价格"],  # 原文里没有
        }])
        result, report = validate_grounding(payload, _items(), window=_window())
        self.assertEqual([s.name for s in result.sectors], [])
        self.assertIn("sector:光伏", report.dropped)
        self.assertIn("硅料价格", report.unverified_terms)

    def test_entry_without_evidence_is_dropped(self) -> None:
        payload = _payload(events=[{"topic": "无依据结论", "evidence_ids": [], "mentioned_terms": ["通信网"]}])
        result, report = validate_grounding(payload, _items(), window=_window())
        self.assertEqual(result.events, [])
        self.assertIn("event:无依据结论", report.dropped)

    def test_watch_item_is_dropped_when_its_sector_did_not_survive(self) -> None:
        """推断层必须挂在通过校验的板块上，否则依据已经被丢掉了。"""
        payload = _payload(watch_list=[
            {"name": "中兴通讯", "code": "000063", "sector": "通信", "evidence_ids": [1]},
            {"name": "孤儿标的", "code": "000001", "sector": "不存在的板块", "evidence_ids": [1]},
        ])
        result, report = validate_grounding(payload, _items(), window=_window())
        self.assertEqual([w.name for w in result.watch_list], ["中兴通讯"])
        self.assertIn("watch:孤儿标的", report.dropped)

    def test_invalid_code_is_nulled_and_out_of_range_ids_are_recorded(self) -> None:
        payload = _payload(watch_list=[
            {"name": "中兴通讯", "code": "ZTE", "sector": "通信", "evidence_ids": [1, 99]},
        ])
        result, report = validate_grounding(payload, _items(), window=_window())
        self.assertIsNone(result.watch_list[0].code)
        self.assertEqual(result.watch_list[0].evidence_ids, [1])
        self.assertIn(99, report.invalid_evidence_ids)

    def test_window_metadata_is_attached(self) -> None:
        window = _window()
        result, _ = validate_grounding(_payload(), _items(), window=window)
        self.assertEqual(result.mode, "weekend")
        self.assertEqual(result.mode_label, "周末")
        self.assertEqual(result.window_start, window.start.isoformat(timespec="seconds"))


class PromptTest(unittest.TestCase):
    def test_sentinels_in_content_are_escaped(self) -> None:
        items = [BriefItem(id=1, title=f"标题 {BEGIN_ITEMS}", summary=f"正文 {END_ITEMS}",
                           source="s", published_at=None)]
        prompt = build_chunk_prompt(items, window=_window())
        self.assertEqual(prompt.count(BEGIN_ITEMS), 1)
        self.assertEqual(prompt.count(END_ITEMS), 1)
        self.assertIn("[FILTERED]", prompt)

    def test_chunks_keep_global_ids(self) -> None:
        prepared = prepare_items(
            [{"title": f"第{i}条", "summary": "x" * 900, "source_name": "s"} for i in range(6)],
            max_items=10,
        )
        chunks = split_into_chunks(prepared, max_chars=1500)
        self.assertGreater(len(chunks), 1)
        ids = [item.id for chunk in chunks for item in chunk]
        self.assertEqual(ids, list(range(1, 7)))

    def test_mode_hint_matches_the_window(self) -> None:
        premarket = build_chunk_prompt(_items(), window=_window(hour=8, day=7))
        self.assertIn("盘前", premarket)
        self.assertIn("不得描述今日盘中走势", premarket)


class MergeTest(unittest.TestCase):
    def test_same_sector_across_chunks_merges_evidence_and_neutralizes_conflict(self) -> None:
        merged = merge_chunk_payloads([
            {"overall_tone": "bullish", "summary": "A",
             "sectors": [{"name": "通信", "direction": "bullish", "evidence_ids": [1], "mentioned_terms": ["通信网"]}]},
            {"overall_tone": "bearish", "summary": "B",
             "sectors": [{"name": "通信", "direction": "bearish", "evidence_ids": [2], "mentioned_terms": ["减持"]}]},
        ])
        self.assertEqual(len(merged["sectors"]), 1)
        self.assertEqual(merged["sectors"][0]["direction"], "neutral")
        self.assertEqual(merged["sectors"][0]["evidence_ids"], [1, 2])
        self.assertIn("A", merged["summary"])
        self.assertIn("B", merged["summary"])

    def test_identical_entries_across_chunks_are_deduped(self) -> None:
        """同一条消息会落在多个段里，各段都就它给一条结论；不去重前端会看到重复卡片。"""
        chunk = {
            "overall_tone": "neutral",
            "summary": "窗口内以公司公告为主。",
            "events": [{"topic": "商务部初步认定倾销", "evidence_ids": [1], "mentioned_terms": ["倾销"]}],
            "takeaways": [{"topic": "关注电子特气", "evidence_ids": [1], "mentioned_terms": ["电子特气"]}],
            "watch_list": [{"name": "某标的", "sector": "综合", "evidence_ids": [1]}],
        }
        second = json.loads(json.dumps(chunk))
        second["events"][0]["evidence_ids"] = [7]
        merged = merge_chunk_payloads([chunk, second, json.loads(json.dumps(chunk))])

        self.assertEqual(len(merged["events"]), 1)
        self.assertEqual(len(merged["takeaways"]), 1)
        self.assertEqual(len(merged["watch_list"]), 1)
        # 重复条目的证据要并上来，而不是丢掉
        self.assertEqual(merged["events"][0]["evidence_ids"], [1, 7])
        # 各段摘要相同时只保留一份，避免拼成一大段
        self.assertEqual(merged["summary"], "窗口内以公司公告为主。")

    def test_same_name_watch_items_in_different_sectors_are_kept(self) -> None:
        merged = merge_chunk_payloads([
            {"watch_list": [{"name": "某标的", "sector": "通信", "evidence_ids": [1]}]},
            {"watch_list": [{"name": "某标的", "sector": "半导体", "evidence_ids": [2]}]},
        ])
        self.assertEqual(len(merged["watch_list"]), 2)


class AnalyzerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))
        os.environ["DATABASE_PATH"] = os.path.join(self._temp_dir.name, "brief.db")
        Config._instance = None
        DatabaseManager.reset_instance()
        self.addCleanup(DatabaseManager.reset_instance)
        self.raw_items = [
            {"title": item.title, "summary": item.summary, "source_name": item.source,
             "published_at": item.published_at}
            for item in _items()
        ]

    def _analyzer(self, stub):
        return MarketBriefAnalyzer(analyzer=stub, config=Config._load_from_env())

    def test_result_is_cached_by_signature_and_force_bypasses_it(self) -> None:
        stub = _stub_analyzer(json.dumps(_payload(), ensure_ascii=False))
        analyzer = self._analyzer(stub)
        first = analyzer.analyze(window=_window(), items=self.raw_items)
        second = analyzer.analyze(window=_window(), items=self.raw_items)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(stub.generate_text_with_metadata.call_count, 1)

        analyzer.analyze(window=_window(), items=self.raw_items, force=True)
        self.assertEqual(stub.generate_text_with_metadata.call_count, 2)

    def test_signature_changes_when_item_content_is_rewritten_at_same_length(self) -> None:
        """资讯池条目会被 upsert 回填，只吃长度的指纹会错误命中缓存。"""
        stub = _stub_analyzer(json.dumps(_payload(), ensure_ascii=False))
        analyzer = self._analyzer(stub)
        analyzer.analyze(window=_window(), items=self.raw_items)
        rewritten = [dict(self.raw_items[0]), dict(self.raw_items[1])]
        rewritten[0]["summary"] = "工信部发文提出通信网建设目標。"  # 同长度，内容不同
        result = analyzer.analyze(window=_window(), items=rewritten)
        self.assertFalse(result["cached"])

    def test_empty_window_is_rejected_without_calling_the_model(self) -> None:
        stub = _stub_analyzer("{}")
        with self.assertRaises(MarketBriefAnalyzerError):
            self._analyzer(stub).analyze(window=_window(), items=[])
        stub.generate_text_with_metadata.assert_not_called()

    def test_missing_llm_config_fails_loudly(self) -> None:
        stub = _stub_analyzer("{}")
        stub.get_generation_backend_config_error.return_value = "LLM 未配置"
        with self.assertRaises(MarketBriefUnavailableError):
            self._analyzer(stub).analyze(window=_window(), items=self.raw_items)

    def test_unparsable_response_degrades_instead_of_raising(self) -> None:
        stub = _stub_analyzer("这不是 JSON")
        payload = self._analyzer(stub).analyze(window=_window(), items=self.raw_items)
        self.assertTrue(payload["degraded"])
        self.assertEqual(payload["result"]["sectors"], [])
        # 降级仍要给出确定性的关键词，而不是一片空白
        self.assertIn("通信", payload["result"]["keywords"])

    def test_reasoning_effort_is_forwarded_for_supported_models(self) -> None:
        """推理型模型不限 effort 会把输出预算全烧在 reasoning 上，正文返回空。"""
        stub = _stub_analyzer(json.dumps(_payload(), ensure_ascii=False), model="glm-5.3-flash")
        self._analyzer(stub).analyze(window=_window(), items=self.raw_items)
        self.assertEqual(
            stub.generate_text_with_metadata.call_args.kwargs.get("reasoning_effort"), "low",
        )

    def test_reasoning_effort_is_not_sent_to_models_that_do_not_support_it(self) -> None:
        stub = _stub_analyzer(json.dumps(_payload(), ensure_ascii=False), model="gpt-4o-mini")
        self._analyzer(stub).analyze(window=_window(), items=self.raw_items)
        self.assertNotIn("reasoning_effort", stub.generate_text_with_metadata.call_args.kwargs)

    def test_empty_content_reason_explains_the_reasoning_budget_cause(self) -> None:
        """上游只给错误码（all_models_failed），降级文案要给出可操作的排查方向。"""
        gen = SimpleNamespace(
            text="", model="glm-5.3-flash", provider="openai",
            usage={}, diagnostics={"reason": "all_models_failed"},
        )
        stub = _stub_analyzer("{}", model="glm-5.3-flash")
        stub.generate_text_with_metadata.return_value = gen
        payload = self._analyzer(stub).analyze(window=_window(), items=self.raw_items)
        self.assertTrue(payload["degraded"])
        self.assertIn("MARKET_BRIEF_REASONING_EFFORT", payload["degraded_reason"])

    def test_hung_model_call_is_cut_off_by_the_deadline(self) -> None:
        def _hang(*_args, **_kwargs):
            time.sleep(30)

        stub = _stub_analyzer("{}")
        stub.generate_text_with_metadata.side_effect = _hang
        os.environ["MARKET_BRIEF_CALL_TIMEOUT_SEC"] = "10"
        Config._instance = None
        started = time.monotonic()
        payload = self._analyzer(stub).analyze(window=_window(), items=self.raw_items)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 25)
        self.assertTrue(payload["degraded"])
        self.assertIn("超时", payload["degraded_reason"])


class EnsureItemsTest(unittest.TestCase):
    """窗口为空时先就地采集一轮，别把显式点按钮的用户直接挡在 400 上。"""

    def _service(self, item_batches, *, collect_enabled_value=False):
        intelligence = Mock()
        intelligence.list_items.side_effect = [{"items": batch, "total": len(batch)} for batch in item_batches]
        config = SimpleNamespace(
            market_brief_max_items=120, market_brief_min_window_minutes=90,
            news_intel_collect_enabled=collect_enabled_value,
        )
        return MarketBriefService(config=config, intelligence=intelligence, repository=Mock()), intelligence

    def test_no_collection_when_the_window_already_has_items(self) -> None:
        service, _ = self._service([[{"title": "有素材"}]])
        with patch("src.services.intelligence_collector.run_collect_once") as collect:
            items, info = service.ensure_items(_window())
        self.assertEqual(len(items), 1)
        self.assertFalse(info["collected"])
        collect.assert_not_called()

    def test_empty_window_triggers_a_forced_collection_then_retries(self) -> None:
        service, _ = self._service([[], [{"title": "采集后拿到的"}]])
        with patch(
            "src.services.intelligence_collector.run_collect_once",
            return_value={"ok": True, "skipped": False, "sources": 9, "succeeded": 9},
        ) as collect:
            items, info = service.ensure_items(_window())
        self.assertEqual(len(items), 1)
        self.assertTrue(info["collected"])
        # force=True：显式用户意图不该被 NEWS_INTEL_COLLECT_ENABLED 定时开关挡住
        self.assertTrue(collect.call_args.kwargs["force"])

    def test_message_points_at_the_collect_switch_only_when_it_is_off(self) -> None:
        service, _ = self._service([[], []], collect_enabled_value=False)
        with patch("src.services.intelligence_collector.run_collect_once", return_value={"ok": True}):
            service.ensure_items(_window())
        message = service.empty_window_message(_window())
        self.assertIn("NEWS_INTEL_COLLECT_ENABLED", message)

        service, _ = self._service([[], []], collect_enabled_value=True)
        with patch("src.services.intelligence_collector.run_collect_once", return_value={"ok": True}):
            service.ensure_items(_window())
        message = service.empty_window_message(_window())
        self.assertNotIn("NEWS_INTEL_COLLECT_ENABLED", message)
        self.assertIn("上游暂时没有新内容", message)


class ShouldGenerateTest(unittest.TestCase):
    def _service(self, **config_overrides):
        base = {
            "market_brief_enabled": True,
            "market_brief_intraday_interval_minutes": 60,
            "market_brief_premarket_time": "08:40",
            "market_brief_closed_time": "09:30",
            "market_brief_max_items": 120,
            "market_brief_min_window_minutes": 90,
        }
        base.update(config_overrides)
        repo = Mock()
        repo.find_for_window.return_value = None
        repo.latest_created_at.return_value = None
        return MarketBriefService(config=SimpleNamespace(**base), repository=repo), repo

    def test_disabled_never_generates(self) -> None:
        service, _ = self._service(market_brief_enabled=False)
        should, reason, _ = service.should_generate()
        self.assertFalse(should)
        self.assertEqual(reason, "disabled")

    def test_premarket_waits_for_the_configured_time(self) -> None:
        service, _ = self._service()
        should, reason, _ = service.should_generate(current_time=datetime(2026, 9, 7, 8, 0, tzinfo=SH))
        self.assertFalse(should)
        self.assertEqual(reason, "time_not_reached")

        should, reason, window = service.should_generate(current_time=datetime(2026, 9, 7, 8, 45, tzinfo=SH))
        self.assertTrue(should)
        self.assertEqual(window.mode, "premarket")

    def test_already_generated_window_is_skipped(self) -> None:
        service, repo = self._service()
        repo.find_for_window.return_value = object()
        should, reason, _ = service.should_generate(current_time=datetime(2026, 9, 7, 8, 45, tzinfo=SH))
        self.assertFalse(should)
        self.assertEqual(reason, "already_generated")

    def test_intraday_respects_the_interval(self) -> None:
        service, repo = self._service()
        now = datetime(2026, 9, 7, 10, 30, tzinfo=SH)
        repo.latest_created_at.return_value = now.replace(tzinfo=None) - timedelta(minutes=20)
        should, reason, _ = service.should_generate(current_time=now)
        self.assertFalse(should)
        self.assertEqual(reason, "intraday_interval_not_reached")

        repo.latest_created_at.return_value = now.replace(tzinfo=None) - timedelta(minutes=90)
        should, _, _ = service.should_generate(current_time=now)
        self.assertTrue(should)

    def test_weekend_uses_the_closed_time(self) -> None:
        service, _ = self._service()
        should, _, window = service.should_generate(current_time=datetime(2026, 9, 5, 10, 0, tzinfo=SH))
        self.assertTrue(should)
        self.assertEqual(window.mode, "weekend")

    def test_unknown_mode_never_generates(self) -> None:
        """时段判定不出来时不出稿：宁可没有，也不要基于错误窗口出一份。"""
        service, _ = self._service()
        with patch("src.core.trading_calendar._XCALS_AVAILABLE", False):
            should, reason, _ = service.should_generate(current_time=datetime(2026, 9, 7, 8, 45, tzinfo=SH))
        self.assertFalse(should)
        self.assertEqual(reason, "mode_unknown")


if __name__ == "__main__":
    unittest.main()
