from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from paper_utils import ROOT, compact_space, load_config, read_json, write_json


METHOD_LABELS = {
    "transformer": "Transformer",
    "foundation model": "基础模型",
    "perturb-seq": "Perturb-seq",
    "crispr": "CRISPR 筛选",
    "causal": "因果建模",
    "graph neural": "图神经网络",
    "gene regulatory network": "基因调控网络",
    "single-cell rna": "单细胞转录组",
    "scrna-seq": "scRNA-seq",
    "multi-omics": "多组学",
    "multimodal": "多模态学习",
    "diffusion": "扩散模型",
    "variational autoencoder": "变分自编码器",
    "benchmark": "基准评测",
    "large language model": "大语言模型",
    "retrieval-augmented": "检索增强生成",
    "knowledge graph": "知识图谱",
    "reinforcement learning": "强化学习",
    "continual learning": "持续学习",
    "self-supervised": "自监督学习",
    "vision-language": "视觉语言模型",
    "contrastive learning": "对比学习",
}


def _abstract_excerpt(abstract: str, limit: int = 360) -> str:
    abstract = compact_space(abstract)
    if not abstract:
        return "论文未提供摘要，建议打开原文查看研究设计与结论。"
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    excerpt = " ".join(sentences[:2])
    if len(excerpt) > limit:
        excerpt = excerpt[: limit - 1].rstrip() + "…"
    return excerpt


def fallback_summary(paper: dict[str, Any]) -> dict[str, str]:
    topics = "、".join(paper.get("matched_topics") or ["配置主题"])
    combined = f"{paper.get('title', '')} {paper.get('abstract', '')}".casefold()
    methods = [label for term, label in METHOD_LABELS.items() if term in combined]
    return {
        "takeaway": f"这项研究聚焦于{topics}。原文摘要要点：{_abstract_excerpt(paper.get('abstract', ''))}",
        "methods": "、".join(dict.fromkeys(methods)) if methods else "摘要中未识别出明确的方法标签",
        "relevance": f"命中当前论文雷达的主题：{topics}。",
        "caveat": "当前为无 API Key 的自动提取结果；关键结论、实验规模和局限请以原文为准。",
    }


def _parse_json_response(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("papers", [])
    if not isinstance(payload, list):
        raise ValueError("Summary response is not a JSON list")
    return payload


def _summarize_batch(
    client: Any,
    papers: list[dict[str, Any]],
    model: str,
    provider: str,
    reasoning_effort: str,
    thinking_mode: str = "disabled",
) -> dict[str, dict[str, str]]:
    items = [
        {
            "id": paper["id"],
            "title": paper.get("title", ""),
            "abstract": (paper.get("abstract") or "")[:5000],
            "matched_topics": paper.get("matched_topics", []),
        }
        for paper in papers
    ]
    instructions = (
        "你是谨慎的科研论文编辑，熟悉计算机科学与计算生物学。根据标题和摘要生成简明中文解读，"
        "不得添加摘要中没有的结果、"
        "数值或因果结论。仅返回合法 JSON 对象，不要 Markdown。顶层格式必须为 {\"papers\": [...]}，"
        "papers 数组中的每项必须包含 id、takeaway、"
        "methods、relevance、caveat 五个字符串字段。takeaway 用 1–2 句说明核心贡献；methods 概括"
        "方法和数据；relevance 说明它为什么与给定研究主题相关；caveat 指出从摘要可见的限制，"
        "若信息不足就明确说明。保留常用英文模型名和数据集名。"
    )
    if provider == "deepseek":
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            stream=False,
            extra_body={"thinking": {"type": thinking_mode}},
        )
        output_text = response.choices[0].message.content or ""
    else:
        response = client.responses.create(
            model=model,
            reasoning={"effort": reasoning_effort},
            instructions=instructions,
            input=json.dumps(items, ensure_ascii=False),
        )
        output_text = response.output_text
    parsed = _parse_json_response(output_text)
    required = ("takeaway", "methods", "relevance", "caveat")
    summaries: dict[str, dict[str, str]] = {}
    for item in parsed:
        if not item.get("id") or not all(isinstance(item.get(field), str) for field in required):
            continue
        summaries[item["id"]] = {field: compact_space(item[field]) for field in required}
    return summaries


def summarize_papers(
    papers: list[dict[str, Any]], config: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    options = config["summary"]
    provider = (os.getenv("SUMMARY_PROVIDER", "").strip() or options.get("provider", "openai")).casefold()
    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    else:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("SUMMARY_MODEL", "").strip() or options["model"]
    max_papers = min(len(papers), int(options["max_papers"]))
    report: dict[str, Any] = {
        "requested": max_papers,
        "provider": provider if api_key and options.get("enabled", True) else None,
        "model": model if api_key and options.get("enabled", True) else None,
        "openai": 0,
        "fallback": 0,
        "errors": [],
    }

    openai_summaries: dict[str, dict[str, str]] = {}
    if api_key and options.get("enabled", True) and max_papers:
        try:
            from openai import OpenAI

            client_options: dict[str, Any] = {"api_key": api_key}
            if provider == "deepseek":
                client_options["base_url"] = options.get("base_url", "https://api.deepseek.com")
            client = OpenAI(**client_options)
            batch_size = int(options.get("batch_size", 8))
            for offset in range(0, max_papers, batch_size):
                batch = papers[offset : offset + batch_size]
                try:
                    openai_summaries.update(
                        _summarize_batch(
                            client,
                            batch,
                            model=model,
                            provider=provider,
                            reasoning_effort=options.get("reasoning_effort", "low"),
                            thinking_mode=options.get("thinking_mode", "disabled"),
                        )
                    )
                except Exception as exc:
                    report["errors"].append(f"batch {offset // batch_size + 1}: {type(exc).__name__}: {str(exc)[:200]}")
        except Exception as exc:
            report["errors"].append(f"client: {type(exc).__name__}: {str(exc)[:200]}")

    summarized: list[dict[str, Any]] = []
    for paper in papers:
        item = dict(paper)
        if paper["id"] in openai_summaries:
            item["summary"] = openai_summaries[paper["id"]]
            item["summary_mode"] = "openai"
            report["openai"] += 1
        else:
            item["summary"] = fallback_summary(paper)
            item["summary_mode"] = "fallback"
            report["fallback"] += 1
        summarized.append(item)
    return summarized, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Chinese summaries with an offline fallback.")
    parser.add_argument("--config", default=ROOT / "config.yaml")
    parser.add_argument("--input", default=ROOT / "work" / "papers.ranked.json")
    parser.add_argument("--output", default=ROOT / "work" / "papers.summarized.json")
    args = parser.parse_args()
    config = load_config(args.config)
    payload = read_json(args.input, {"papers": []})
    papers, report = summarize_papers(payload.get("papers", []), config)
    write_json(args.output, {**payload, "papers": papers, "summary_report": report})
    print(f"Summarized {len(papers)} papers -> {Path(args.output)}")


if __name__ == "__main__":
    main()
