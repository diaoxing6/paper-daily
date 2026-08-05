from __future__ import annotations

import argparse
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import feedparser
import requests

from paper_utils import ROOT, compact_space, iso_date, load_config, write_json


Paper = dict[str, Any]


def _paper(**values: Any) -> Paper:
    base: Paper = {
        "id": "",
        "title": "",
        "abstract": "",
        "authors": [],
        "published": "",
        "updated": "",
        "url": "",
        "pdf_url": "",
        "doi": "",
        "venue": "",
        "source": "",
        "citation_count": 0,
        "influential_citation_count": 0,
    }
    base.update(values)
    base["title"] = compact_space(base.get("title"))
    base["abstract"] = compact_space(base.get("abstract"))
    base["doi"] = compact_space(base.get("doi")).removeprefix("https://doi.org/")
    base["published"] = iso_date(base.get("published"))
    base["updated"] = iso_date(base.get("updated"))
    return base


def _session(config: dict[str, Any]) -> requests.Session:
    client = requests.Session()
    client.headers.update({"User-Agent": config["fetch"]["user_agent"]})
    return client


def fetch_arxiv(config: dict[str, Any], start: datetime, end: datetime) -> list[Paper]:
    options = config["fetch"]["sources"]["arxiv"]
    category_query = " OR ".join(f"cat:{category}" for category in options["categories"])
    interest_terms = [
        "single cell", "single-cell", "perturb-seq", "perturbation prediction", "virtual cell",
        "cell foundation model", "gene regulatory network", "regulon", "drug perturbation",
        "multi-omics perturbation", "mechanistic inference", "counterfactual prediction",
    ]
    interest_query = " OR ".join(f'all:"{term}"' for term in interest_terms)
    query = f"({category_query}) AND ({interest_query})"
    response = _session(config).get(
        "https://export.arxiv.org/api/query",
        params={
            "search_query": query,
            "start": 0,
            "max_results": options["max_results"],
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
        timeout=config["fetch"]["request_timeout_seconds"],
    )
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"arXiv feed parse failed: {feed.bozo_exception}")

    papers: list[Paper] = []
    for entry in feed.entries:
        published = iso_date(getattr(entry, "published", ""))
        published_dt = datetime.fromisoformat(published).replace(tzinfo=timezone.utc) if published else None
        if published_dt and not (start <= published_dt <= end + timedelta(days=1)):
            continue
        paper_id = entry.id.rsplit("/", 1)[-1]
        links = {link.get("type"): link.get("href", "") for link in getattr(entry, "links", [])}
        doi = getattr(entry, "arxiv_doi", "")
        papers.append(
            _paper(
                id=f"arxiv:{paper_id}",
                title=entry.title,
                abstract=entry.summary,
                authors=[author.get("name", "") for author in entry.authors],
                published=published,
                updated=getattr(entry, "updated", ""),
                url=entry.id,
                pdf_url=links.get("application/pdf", f"https://arxiv.org/pdf/{paper_id}"),
                doi=doi,
                venue="arXiv",
                source="arXiv",
            )
        )
    return papers


def fetch_biorxiv(config: dict[str, Any], start: datetime, end: datetime) -> list[Paper]:
    options = config["fetch"]["sources"]["biorxiv"]
    client = _session(config)
    cursor = 0
    papers: list[Paper] = []
    limit = int(options["max_results"])
    while len(papers) < limit:
        url = (
            "https://api.biorxiv.org/details/biorxiv/"
            f"{start.date().isoformat()}/{end.date().isoformat()}/{cursor}"
        )
        response = client.get(url, timeout=config["fetch"]["request_timeout_seconds"])
        response.raise_for_status()
        payload = response.json()
        collection = payload.get("collection", [])
        if not collection:
            break
        for item in collection:
            doi = item.get("doi", "")
            papers.append(
                _paper(
                    id=f"biorxiv:{doi or item.get('title', '')}",
                    title=item.get("title"),
                    abstract=item.get("abstract"),
                    authors=[name.strip() for name in item.get("authors", "").split(";") if name.strip()],
                    published=item.get("date"),
                    updated=item.get("date"),
                    url=f"https://www.biorxiv.org/content/{doi}",
                    pdf_url=f"https://www.biorxiv.org/content/{doi}.full.pdf" if doi else "",
                    doi=doi,
                    venue=item.get("category") or "bioRxiv",
                    source="bioRxiv",
                )
            )
            if len(papers) >= limit:
                break
        cursor += len(collection)
        total = int((payload.get("messages") or [{}])[0].get("total", cursor))
        if cursor >= total:
            break
        time.sleep(0.2)
    return papers


def _xml_text(element: ET.Element | None) -> str:
    return compact_space("".join(element.itertext())) if element is not None else ""


def _pubmed_date(article: ET.Element) -> str:
    for path in (".//DateCompleted", ".//DateRevised", ".//JournalIssue/PubDate"):
        node = article.find(path)
        if node is None:
            continue
        year = _xml_text(node.find("Year"))
        month = _xml_text(node.find("Month")) or "01"
        day = _xml_text(node.find("Day")) or "01"
        month_lookup = {
            "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
            "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
        }
        month = month_lookup.get(month[:3].title(), month.zfill(2))
        if year:
            return f"{year}-{month}-{day.zfill(2)}"
    return ""


def fetch_pubmed(config: dict[str, Any], start: datetime, end: datetime) -> list[Paper]:
    options = config["fetch"]["sources"]["pubmed"]
    topic_terms = [
        term
        for topic in config["ranking"]["topics"]
        for term in topic["terms"][:2]
    ]
    term_query = " OR ".join(f'"{term}"[Title/Abstract]' for term in topic_terms)
    date_query = f'("{start.date()}"[Date - Publication] : "{end.date()}"[Date - Publication])'
    query = f"({term_query}) AND {date_query}"
    client = _session(config)
    common = {"tool": "cell-paper-radar"}
    email = compact_space(options.get("email"))
    if email:
        common["email"] = email
    api_key = os.getenv("NCBI_API_KEY", "").strip()
    if api_key:
        common["api_key"] = api_key

    search_response = client.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
        params={
            **common,
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": options["max_results"],
            "sort": "pub date",
        },
        timeout=config["fetch"]["request_timeout_seconds"],
    )
    search_response.raise_for_status()
    ids = search_response.json().get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    fetch_response = client.get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params={**common, "db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
        timeout=max(60, config["fetch"]["request_timeout_seconds"]),
    )
    fetch_response.raise_for_status()
    root = ET.fromstring(fetch_response.content)
    papers: list[Paper] = []
    for article in root.findall(".//PubmedArticle"):
        medline = article.find("MedlineCitation")
        article_node = article.find(".//Article")
        if medline is None or article_node is None:
            continue
        pmid = _xml_text(medline.find("PMID"))
        abstract_parts = []
        for node in article_node.findall(".//Abstract/AbstractText"):
            label = node.attrib.get("Label", "")
            text = _xml_text(node)
            abstract_parts.append(f"{label}: {text}" if label else text)
        authors = []
        for author in article_node.findall(".//Author"):
            collective = _xml_text(author.find("CollectiveName"))
            full_name = compact_space(
                " ".join(filter(None, [_xml_text(author.find("ForeName")), _xml_text(author.find("LastName"))]))
            )
            if collective or full_name:
                authors.append(collective or full_name)
        identifiers = {
            node.attrib.get("IdType", ""): _xml_text(node)
            for node in article.findall("./PubmedData/ArticleIdList/ArticleId")
        }
        doi = identifiers.get("doi", "")
        papers.append(
            _paper(
                id=f"pubmed:{pmid}",
                title=_xml_text(article_node.find("ArticleTitle")),
                abstract=" ".join(abstract_parts),
                authors=authors,
                published=_pubmed_date(article),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                doi=doi,
                venue=_xml_text(article_node.find(".//Journal/Title")) or "PubMed",
                source="PubMed",
            )
        )
    return papers


def fetch_semantic_scholar(config: dict[str, Any], start: datetime, end: datetime) -> list[Paper]:
    options = config["fetch"]["sources"]["semantic_scholar"]
    client = _session(config)
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        client.headers.update({"x-api-key": api_key})
    fields = ",".join(
        [
            "paperId", "title", "abstract", "authors", "publicationDate", "url", "openAccessPdf",
            "externalIds", "venue", "citationCount", "influentialCitationCount",
        ]
    )
    papers: list[Paper] = []
    for query in options["queries"]:
        response = client.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": query,
                "limit": options["max_results_per_query"],
                "fields": fields,
                "publicationDateOrYear": f"{start.date()}:{end.date()}",
            },
            timeout=config["fetch"]["request_timeout_seconds"],
        )
        response.raise_for_status()
        for item in response.json().get("data", []):
            external = item.get("externalIds") or {}
            open_pdf = item.get("openAccessPdf") or {}
            paper_id = item.get("paperId", "")
            papers.append(
                _paper(
                    id=f"s2:{paper_id}",
                    title=item.get("title"),
                    abstract=item.get("abstract"),
                    authors=[author.get("name", "") for author in item.get("authors") or []],
                    published=item.get("publicationDate"),
                    url=item.get("url") or f"https://www.semanticscholar.org/paper/{paper_id}",
                    pdf_url=open_pdf.get("url", ""),
                    doi=external.get("DOI", ""),
                    venue=item.get("venue") or "Semantic Scholar",
                    source="Semantic Scholar",
                    citation_count=item.get("citationCount") or 0,
                    influential_citation_count=item.get("influentialCitationCount") or 0,
                )
            )
        time.sleep(0.5 if api_key else 1.0)
    return papers


FETCHERS: dict[str, Callable[[dict[str, Any], datetime, datetime], list[Paper]]] = {
    "arxiv": fetch_arxiv,
    "biorxiv": fetch_biorxiv,
    "pubmed": fetch_pubmed,
    "semantic_scholar": fetch_semantic_scholar,
}


def fetch_all(config: dict[str, Any], now: datetime | None = None) -> tuple[list[Paper], dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    end = now.astimezone(timezone.utc)
    start = end - timedelta(days=int(config["fetch"]["lookback_days"]))
    papers: list[Paper] = []
    report: dict[str, Any] = {
        "window_start": start.date().isoformat(),
        "window_end": end.date().isoformat(),
        "sources": {},
    }
    for source_name, fetcher in FETCHERS.items():
        options = config["fetch"]["sources"].get(source_name, {})
        if not options.get("enabled", False):
            report["sources"][source_name] = {"status": "disabled", "count": 0}
            continue
        try:
            fetched = fetcher(config, start, end)
            papers.extend(fetched)
            report["sources"][source_name] = {"status": "ok", "count": len(fetched)}
        except Exception as exc:  # A single upstream must not stop the daily page.
            report["sources"][source_name] = {
                "status": "error",
                "count": 0,
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            }
    report["total"] = len(papers)
    return papers, report


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch recent papers from configured sources.")
    parser.add_argument("--config", default=ROOT / "config.yaml")
    parser.add_argument("--output", default=ROOT / "work" / "papers.raw.json")
    args = parser.parse_args()
    config = load_config(args.config)
    papers, report = fetch_all(config)
    write_json(args.output, {"papers": papers, "fetch_report": report})
    print(f"Fetched {len(papers)} papers -> {Path(args.output)}")


if __name__ == "__main__":
    main()
