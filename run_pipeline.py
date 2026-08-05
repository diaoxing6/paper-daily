from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fetch_papers import fetch_all
from paper_utils import ROOT, load_config, read_json, utc_now_iso, write_json
from rank_papers import rank_papers
from render_html import render_html
from summarize_papers import summarize_papers


def _load_fixture(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fixture = read_json(path, [])
    papers = fixture.get("papers", []) if isinstance(fixture, dict) else fixture
    return papers, {
        "window_start": "fixture",
        "window_end": "fixture",
        "sources": {},
        "total": len(papers),
        "fixture": True,
    }


def run_pipeline(config_path: str | Path, fixture: str | Path | None = None) -> dict[str, Any]:
    config = load_config(config_path)
    latest_path = ROOT / "docs" / "data" / "latest.json"
    if fixture:
        raw_papers, fetch_report = _load_fixture(fixture)
    else:
        raw_papers, fetch_report = fetch_all(config)

    all_failed = bool(fetch_report.get("sources")) and all(
        item.get("status") in {"error", "disabled"}
        for item in fetch_report["sources"].values()
    )
    previous = read_json(latest_path, {})
    if not raw_papers and all_failed and previous.get("papers"):
        preserved = dict(previous)
        preserved["generated_at"] = utc_now_iso()
        preserved["fetch_report"] = fetch_report
        preserved["notice"] = "所有数据源暂时不可用，已保留上次成功结果。"
        write_json(latest_path, preserved)
        render_html(preserved, config, ROOT / "docs" / "index.html")
        return preserved

    ranked = rank_papers(raw_papers, config)
    summarized, summary_report = summarize_papers(ranked, config)
    generated_at = utc_now_iso()
    payload = {
        "generated_at": generated_at,
        "timezone": config["site"]["timezone"],
        "papers": summarized,
        "fetch_report": fetch_report,
        "summary_report": summary_report,
    }
    write_json(latest_path, payload)
    archive_date = datetime.fromisoformat(generated_at).astimezone(
        ZoneInfo(config["site"]["timezone"])
    ).date().isoformat()
    write_json(ROOT / "docs" / "data" / "archive" / f"{archive_date}.json", payload)
    render_html(payload, config, ROOT / "docs" / "index.html")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the complete daily paper radar pipeline.")
    parser.add_argument("--config", default=ROOT / "config.yaml")
    parser.add_argument("--fixture", help="Use a local JSON fixture instead of network sources.")
    args = parser.parse_args()
    payload = run_pipeline(args.config, fixture=args.fixture)
    report = payload.get("summary_report", {})
    print(
        f"Published {len(payload.get('papers', []))} papers "
        f"({report.get('openai', 0)} OpenAI, {report.get('fallback', 0)} fallback)."
    )


if __name__ == "__main__":
    main()
