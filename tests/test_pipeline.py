from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from paper_utils import load_config, read_json  # noqa: E402
from rank_papers import deduplicate, rank_papers  # noqa: E402
from render_html import build_html  # noqa: E402
from summarize_papers import _summarize_batch, fallback_summary, summarize_papers  # noqa: E402


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(ROOT / "config.yaml")
        cls.fixture = read_json(ROOT / "tests" / "fixtures" / "sample_papers.json", [])

    def test_ranker_prioritizes_relevant_papers(self) -> None:
        ranked = rank_papers(
            self.fixture,
            self.config,
            now=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
        self.assertGreaterEqual(len(ranked), 6)
        self.assertTrue(ranked[0]["matched_topics"])
        self.assertGreater(ranked[0]["score"], self.config["ranking"]["min_score"])

    def test_deduplicate_prefers_doi(self) -> None:
        papers = [dict(self.fixture[0], doi="10.1/example", source="arXiv"), dict(self.fixture[0], doi="10.1/example", source="PubMed")]
        unique = deduplicate(papers)
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0]["source"], "PubMed")

    def test_summary_falls_back_without_key(self) -> None:
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        old_deepseek_key = os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            ranked = rank_papers(self.fixture[:1], self.config, now=datetime(2026, 8, 5, tzinfo=timezone.utc))
            papers, report = summarize_papers(ranked, self.config)
        finally:
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key
            if old_deepseek_key is not None:
                os.environ["DEEPSEEK_API_KEY"] = old_deepseek_key
        self.assertEqual(report["openai"], 0)
        self.assertEqual(papers[0]["summary_mode"], "fallback")
        self.assertIn("研究", fallback_summary(ranked[0])["takeaway"])

    def test_deepseek_uses_chat_completions_json_mode(self) -> None:
        class FakeCompletions:
            def __init__(self) -> None:
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                summary = {
                    "papers": [{
                        "id": "sample:001",
                        "takeaway": "核心贡献",
                        "methods": "方法",
                        "relevance": "相关性",
                        "caveat": "局限",
                    }]
                }
                message = type("Message", (), {"content": __import__("json").dumps(summary)})()
                choice = type("Choice", (), {"message": message})()
                return type("Response", (), {"choices": [choice]})()

        completions = FakeCompletions()
        fake_client = type(
            "Client",
            (),
            {"chat": type("Chat", (), {"completions": completions})()},
        )()
        result = _summarize_batch(
            fake_client,
            [self.fixture[0]],
            model="deepseek-v4-flash",
            provider="deepseek",
            reasoning_effort="low",
            thinking_mode="disabled",
        )
        self.assertEqual(result["sample:001"]["takeaway"], "核心贡献")
        self.assertEqual(completions.kwargs["model"], "deepseek-v4-flash")
        self.assertEqual(completions.kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(completions.kwargs["extra_body"], {"thinking": {"type": "disabled"}})

    def test_renderer_escapes_untrusted_titles(self) -> None:
        paper = dict(self.fixture[0])
        paper.update(
            {
                "title": "<script>alert(1)</script>",
                "score": 9.0,
                "matched_topics": ["虚拟细胞"],
                "summary": fallback_summary({**paper, "matched_topics": ["虚拟细胞"]}),
                "summary_mode": "fallback",
            }
        )
        page = build_html({"generated_at": "2026-08-05T00:00:00+00:00", "papers": [paper]}, self.config, [])
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)


if __name__ == "__main__":
    unittest.main()
