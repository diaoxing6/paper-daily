from __future__ import annotations

import argparse
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_utils import ROOT, normalize_title, parse_date, read_json, write_json


TOPIC_CONTEXT_GATES = {
    "单细胞扰动": {
        "weak_terms": {"crispr screen", "cellular response prediction"},
        "required_context": {"single-cell", "single cell", "scrna-seq", "perturb-seq", "cell state"},
    },
    "GRN / Regulon": {
        "weak_terms": {"transcriptional regulation"},
        "required_context": {"gene regulatory network", "grn", "regulon", "perturb", "single-cell", "single cell"},
    },
}


def _dedup_key(paper: dict[str, Any]) -> str:
    doi = (paper.get("doi") or "").strip().casefold()
    if doi:
        return f"doi:{doi}"
    return f"title:{normalize_title(paper.get('title'))}"


def deduplicate(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    source_priority = {"PubMed": 4, "bioRxiv": 3, "arXiv": 2, "Semantic Scholar": 1}
    for paper in papers:
        if not paper.get("title"):
            continue
        key = _dedup_key(paper)
        current = grouped.get(key)
        if current is None:
            item = dict(paper)
            item["also_seen_in"] = [paper.get("source", "")]
            grouped[key] = item
            continue

        current_sources = set(current.get("also_seen_in", []))
        current_sources.add(paper.get("source", ""))
        use_new = source_priority.get(paper.get("source", ""), 0) > source_priority.get(
            current.get("source", ""), 0
        )
        if use_new:
            replacement = dict(paper)
            replacement["also_seen_in"] = sorted(filter(None, current_sources))
            for field in ("abstract", "doi", "url", "pdf_url", "venue", "published"):
                if not replacement.get(field) and current.get(field):
                    replacement[field] = current[field]
            current = replacement
            grouped[key] = current
        else:
            current["also_seen_in"] = sorted(filter(None, current_sources))
            if len(paper.get("abstract") or "") > len(current.get("abstract") or ""):
                current["abstract"] = paper["abstract"]
            if not current.get("doi") and paper.get("doi"):
                current["doi"] = paper["doi"]
        current["citation_count"] = max(
            int(current.get("citation_count") or 0), int(paper.get("citation_count") or 0)
        )
        current["influential_citation_count"] = max(
            int(current.get("influential_citation_count") or 0),
            int(paper.get("influential_citation_count") or 0),
        )
    return list(grouped.values())


def score_paper(
    paper: dict[str, Any], config: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    title = (paper.get("title") or "").casefold()
    abstract = (paper.get("abstract") or "").casefold()
    combined = f"{title} {abstract}"
    score = float(config["ranking"]["source_weights"].get(paper.get("source"), 0.0))
    matched_topics: list[str] = []
    matched_categories: list[str] = []
    matched_terms: list[str] = []
    breakdown: dict[str, float] = {}
    category_topic_scores: dict[str, float] = {}

    for topic in config["ranking"]["topics"]:
        title_hits = [term for term in topic["terms"] if term.casefold() in title]
        abstract_hits = [
            term for term in topic["terms"] if term.casefold() in abstract and term not in title_hits
        ]
        gate = TOPIC_CONTEXT_GATES.get(topic["name"])
        all_hits = title_hits + abstract_hits
        if gate and all_hits and all(term in gate["weak_terms"] for term in all_hits):
            if not any(term in combined for term in gate["required_context"]):
                continue
        if not title_hits and not abstract_hits:
            continue
        weight = float(topic["weight"])
        topic_score = min(7.0, len(title_hits) * 4.0 * weight + len(abstract_hits) * 1.5 * weight)
        score += topic_score
        matched_topics.append(topic["name"])
        category = topic.get("category", "uncategorized")
        if category not in matched_categories:
            matched_categories.append(category)
        category_topic_scores[category] = category_topic_scores.get(category, 0.0) + topic_score
        matched_terms.extend(title_hits + abstract_hits)
        breakdown[topic["name"]] = round(topic_score, 2)

    context_hits = [term for term in config["ranking"]["context_terms"] if term.casefold() in combined]
    if context_hits:
        context_score = min(2.0, 0.5 * len(context_hits))
        score += context_score
        breakdown["领域上下文"] = round(context_score, 2)

    negative_hits = [term for term in config["ranking"]["negative_terms"] if term.casefold() in combined]
    if negative_hits:
        penalty = 5.0 * len(negative_hits)
        score -= penalty
        breakdown["排除词"] = -penalty

    published = parse_date(paper.get("published"))
    if published:
        age_days = max(0.0, (now - published).total_seconds() / 86400)
        freshness = max(0.0, 2.5 - 0.35 * age_days)
        score += freshness
        breakdown["新鲜度"] = round(freshness, 2)

    citations = int(paper.get("citation_count") or 0)
    citation_score = min(2.0, math.log1p(citations) / 2.0)
    if citation_score:
        score += citation_score
        breakdown["引用"] = round(citation_score, 2)

    result = dict(paper)
    result["score"] = round(score, 2)
    result["matched_topics"] = matched_topics
    result["matched_categories"] = matched_categories
    result["matched_terms"] = sorted(set(matched_terms))
    result["score_breakdown"] = breakdown
    topic_total = sum(category_topic_scores.values())
    base_score = score - topic_total
    result["category_scores"] = {
        category: round(base_score + topic_score, 2)
        for category, topic_score in category_topic_scores.items()
    }
    return result


def rank_papers(
    papers: list[dict[str, Any]], config: dict[str, Any], now: datetime | None = None
) -> list[dict[str, Any]]:
    scored = [score_paper(paper, config, now=now) for paper in deduplicate(papers)]
    threshold = float(config["ranking"]["min_score"])
    relevant = [
        paper
        for paper in scored
        if paper["score"] >= threshold and paper["matched_topics"] and paper["matched_categories"]
    ]
    relevant.sort(
        key=lambda paper: (
            paper["score"],
            paper.get("published") or "",
            paper.get("citation_count") or 0,
        ),
        reverse=True,
    )

    # Reserve an independent quota for every research channel. Taking the union of
    # each channel's top papers keeps broad LLM traffic from crowding out smaller
    # fields such as continual learning or knowledge graphs.
    category_ids = [item["id"] for item in config["site"].get("categories", [])]
    per_category_limit = int(config["site"].get("per_category_limit", 7))
    max_papers = int(config["site"]["max_papers"])
    selected: dict[str, dict[str, Any]] = {}
    for category in category_ids:
        bucket = [paper for paper in relevant if category in paper["matched_categories"]]
        bucket.sort(
            key=lambda paper: (
                paper.get("category_scores", {}).get(category, 0.0),
                paper.get("published") or "",
                paper.get("citation_count") or 0,
            ),
            reverse=True,
        )
        for paper in bucket[:per_category_limit]:
            selected.setdefault(paper["id"], paper)

    for paper in relevant:
        if len(selected) >= max_papers:
            break
        selected.setdefault(paper["id"], paper)

    balanced = list(selected.values())
    balanced.sort(
        key=lambda paper: (
            paper["score"],
            paper.get("published") or "",
            paper.get("citation_count") or 0,
        ),
        reverse=True,
    )
    return balanced[:max_papers]


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank papers by configured research interests.")
    parser.add_argument("--config", default=ROOT / "config.yaml")
    parser.add_argument("--input", default=ROOT / "work" / "papers.raw.json")
    parser.add_argument("--output", default=ROOT / "work" / "papers.ranked.json")
    args = parser.parse_args()
    from paper_utils import load_config

    config = load_config(args.config)
    payload = read_json(args.input, {"papers": [], "fetch_report": {}})
    ranked = rank_papers(payload.get("papers", []), config)
    write_json(args.output, {**payload, "papers": ranked})
    print(f"Ranked {len(ranked)} papers -> {Path(args.output)}")


if __name__ == "__main__":
    main()
