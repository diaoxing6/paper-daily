from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from paper_utils import ROOT, load_config, read_json


SOURCE_LABELS = {
    "arXiv": "ARXIV",
    "bioRxiv": "BIORXIV",
    "PubMed": "PUBMED",
    "Semantic Scholar": "S2",
}


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _paper_card(paper: dict[str, Any], index: int, highlighted: bool = False) -> str:
    topics = paper.get("matched_topics") or []
    topics_html = "".join(f'<span class="topic-pill">{_e(topic)}</span>' for topic in topics)
    authors = paper.get("authors") or []
    author_text = ", ".join(authors[:4])
    if len(authors) > 4:
        author_text += f" 等 {len(authors)} 位作者"
    summary = paper.get("summary") or {}
    links = [f'<a class="paper-link" href="{_e(paper.get("url"))}" target="_blank" rel="noreferrer">原文 ↗</a>']
    if paper.get("pdf_url"):
        links.append(f'<a class="paper-link secondary" href="{_e(paper["pdf_url"])}" target="_blank" rel="noreferrer">PDF</a>')
    if paper.get("doi"):
        links.append(f'<a class="paper-link secondary" href="https://doi.org/{_e(paper["doi"])}" target="_blank" rel="noreferrer">DOI</a>')
    mode_label = "AI 中文解读" if paper.get("summary_mode") == "openai" else "本地摘要"
    source = SOURCE_LABELS.get(paper.get("source"), paper.get("source", "SOURCE"))
    search_text = " ".join(
        [paper.get("title", ""), paper.get("abstract", ""), author_text, " ".join(topics)]
    ).casefold()
    card_class = "paper-card highlight-card" if highlighted else "paper-card"
    badge = '<span class="priority-badge">优先阅读</span>' if highlighted else ""
    return f"""
      <article class="{card_class}" id="paper-{index}" data-topics="{_e('|'.join(topics))}" data-search="{_e(search_text)}">
        <div class="card-rail"><span>{index:02d}</span></div>
        <div class="card-body">
          <div class="paper-kicker">
            <span class="source-badge source-{_e(paper.get('source', '').lower().replace(' ', '-'))}">{_e(source)}</span>
            <time>{_e(paper.get('published') or '日期未知')}</time>
            <span class="score">相关度 {_e(f"{float(paper.get('score', 0)):.1f}")}</span>
            {badge}
          </div>
          <h3><a href="{_e(paper.get('url'))}" target="_blank" rel="noreferrer">{_e(paper.get('title'))}</a></h3>
          <p class="authors">{_e(author_text or '作者信息暂缺')} · {_e(paper.get('venue') or paper.get('source'))}</p>
          <div class="topic-row">{topics_html}</div>
          <div class="summary-panel">
            <div class="summary-heading"><span>中文速读</span><em>{_e(mode_label)}</em></div>
            <p class="takeaway">{_e(summary.get('takeaway', '暂无摘要'))}</p>
            <dl>
              <div><dt>方法</dt><dd>{_e(summary.get('methods', '—'))}</dd></div>
              <div><dt>为什么值得看</dt><dd>{_e(summary.get('relevance', '—'))}</dd></div>
              <div><dt>阅读提示</dt><dd>{_e(summary.get('caveat', '—'))}</dd></div>
            </dl>
          </div>
          <div class="card-footer"><div>{''.join(links)}</div><a class="anchor-link" href="#paper-{index}">#{index:02d}</a></div>
        </div>
      </article>"""


def build_html(payload: dict[str, Any], config: dict[str, Any], archive_dates: list[str]) -> str:
    site = config["site"]
    papers = payload.get("papers") or []
    generated_at = payload.get("generated_at")
    try:
        generated = datetime.fromisoformat(generated_at).astimezone(ZoneInfo(site["timezone"]))
        generated_label = generated.strftime("%Y年%m月%d日 %H:%M")
        date_label = generated.strftime("%Y · %m · %d")
    except (TypeError, ValueError):
        generated_label = "尚未更新"
        date_label = "PAPER RADAR"

    report = payload.get("fetch_report") or {}
    source_reports = report.get("sources") or {}
    source_status = []
    for key, label in (("arxiv", "arXiv"), ("biorxiv", "bioRxiv"), ("pubmed", "PubMed"), ("semantic_scholar", "S2")):
        item = source_reports.get(key, {})
        status = item.get("status", "unknown")
        source_status.append(
            f'<span class="source-status status-{_e(status)}"><i></i>{label}<b>{int(item.get("count", 0))}</b></span>'
        )
    if not source_reports:
        source_status = ['<span class="source-status status-sample"><i></i>示例数据</span>']

    topic_counts: dict[str, int] = {}
    for paper in papers:
        for topic in paper.get("matched_topics") or []:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
    topic_buttons = "".join(
        f'<button class="filter-btn" data-topic="{_e(topic)}">{_e(topic)} <span>{count}</span></button>'
        for topic, count in sorted(topic_counts.items(), key=lambda item: (-item[1], item[0]))
    )

    highlight_count = min(int(site["highlight_count"]), len(papers))
    highlight_cards = "".join(_paper_card(paper, index + 1, True) for index, paper in enumerate(papers[:highlight_count]))
    remaining_cards = "".join(
        _paper_card(paper, index + 1, False) for index, paper in enumerate(papers[highlight_count:], start=highlight_count)
    )
    if not papers:
        highlight_cards = """
          <div class="empty-state">
            <span>NO SIGNAL YET</span>
            <h3>今天还没有匹配到论文</h3>
            <p>数据源可能暂时没有更新。页面会在下一次定时任务后自动重试。</p>
          </div>"""

    unique_sources = len({paper.get("source") for paper in papers if paper.get("source")})
    openai_count = sum(1 for paper in papers if paper.get("summary_mode") == "openai")
    archive_options = "".join(f'<option value="{_e(date)}">{_e(date)}</option>' for date in archive_dates[:30])
    data_json = json.dumps(
        {"generated_at": generated_at, "paper_count": len(papers), "topics": topic_counts},
        ensure_ascii=False,
    ).replace("</", "<\\/")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{_e(site['description'])}">
  <meta name="color-scheme" content="light">
  <title>{_e(site['title'])}</title>
  <style>
    :root {{ --ink:#13233a; --ink-soft:#526074; --paper:#f4f1e9; --card:#fffdf8; --line:#d9d4c8; --accent:#ef5d3f; --teal:#167e79; --lime:#d7ee72; --shadow:0 18px 55px rgba(19,35,58,.09); }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; color:var(--ink); background:var(--paper); font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,sans-serif; line-height:1.65; }}
    a {{ color:inherit; }}
    button,input,select {{ font:inherit; }}
    .page-shell {{ width:min(1180px,calc(100% - 40px)); margin:0 auto; }}
    .site-header {{ padding:24px 0 0; }}
    .nav {{ display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid var(--ink); padding-bottom:18px; gap:20px; }}
    .brand {{ display:flex; align-items:center; gap:12px; text-decoration:none; font-weight:900; letter-spacing:-.04em; }}
    .brand-mark {{ width:34px; height:34px; border-radius:50%; background:var(--accent); position:relative; box-shadow:inset -8px -8px 0 rgba(19,35,58,.12); }}
    .brand-mark:after {{ content:""; position:absolute; width:9px; height:9px; border-radius:50%; background:var(--lime); top:5px; right:4px; }}
    .brand small {{ display:block; font-size:10px; letter-spacing:.18em; color:var(--ink-soft); line-height:1; }}
    .nav-meta {{ display:flex; align-items:center; gap:18px; font-size:12px; font-weight:700; letter-spacing:.06em; }}
    .live-dot {{ display:inline-flex; align-items:center; gap:8px; }}
    .live-dot:before {{ content:""; width:8px; height:8px; border-radius:50%; background:var(--teal); box-shadow:0 0 0 5px rgba(22,126,121,.12); }}
    .hero {{ padding:68px 0 44px; display:grid; grid-template-columns:minmax(0,1.5fr) minmax(260px,.5fr); gap:50px; align-items:end; }}
    .eyebrow {{ display:flex; gap:12px; align-items:center; color:var(--accent); font-weight:900; font-size:12px; letter-spacing:.2em; }}
    .eyebrow:before {{ content:""; width:44px; height:2px; background:var(--accent); }}
    h1 {{ margin:16px 0 18px; max-width:800px; font-family:Georgia,"Noto Serif SC",serif; font-size:clamp(48px,8vw,92px); letter-spacing:-.065em; line-height:.92; font-weight:500; }}
    h1 em {{ font-style:italic; color:var(--teal); }}
    .hero-copy {{ max-width:690px; color:var(--ink-soft); font-size:17px; }}
    .date-block {{ border-left:1px solid var(--ink); padding-left:28px; }}
    .date-block span {{ display:block; font-size:12px; letter-spacing:.18em; font-weight:800; color:var(--ink-soft); }}
    .date-block strong {{ display:block; font-family:Georgia,serif; font-size:34px; font-weight:400; margin:6px 0; }}
    .date-block small {{ color:var(--ink-soft); }}
    .signal-strip {{ display:grid; grid-template-columns:repeat(3,1fr) minmax(280px,2.3fr); border:1px solid var(--ink); background:var(--card); box-shadow:var(--shadow); }}
    .stat {{ padding:22px 24px; border-right:1px solid var(--line); }}
    .stat b {{ display:block; font-family:Georgia,serif; font-size:34px; line-height:1; font-weight:400; }}
    .stat span {{ color:var(--ink-soft); font-size:11px; text-transform:uppercase; letter-spacing:.13em; font-weight:800; }}
    .source-line {{ padding:18px 22px; display:flex; gap:12px 18px; flex-wrap:wrap; align-items:center; }}
    .source-status {{ display:inline-flex; align-items:center; gap:7px; font-size:11px; font-weight:800; }}
    .source-status i {{ width:7px; height:7px; border-radius:50%; background:#9ca3af; }}
    .source-status b {{ font-weight:600; color:var(--ink-soft); }}
    .status-ok i {{ background:var(--teal); }} .status-error i {{ background:var(--accent); }} .status-sample i {{ background:#d6a928; }}
    .controls {{ margin:38px 0 30px; display:flex; gap:14px; flex-wrap:wrap; align-items:center; }}
    .search-wrap {{ position:relative; flex:1 1 300px; }}
    .search-wrap:before {{ content:"⌕"; position:absolute; left:17px; top:7px; font-size:25px; color:var(--ink-soft); }}
    #search {{ width:100%; height:48px; border:1px solid var(--ink); background:transparent; padding:0 18px 0 48px; outline:none; }}
    #search:focus {{ background:var(--card); box-shadow:0 0 0 3px rgba(22,126,121,.13); }}
    .archive-select {{ height:48px; min-width:180px; border:1px solid var(--ink); background:var(--card); padding:0 14px; }}
    .filter-row {{ display:flex; flex-wrap:wrap; gap:9px; margin-bottom:50px; }}
    .filter-btn {{ border:1px solid var(--line); border-radius:999px; padding:7px 13px; background:transparent; cursor:pointer; color:var(--ink-soft); font-size:12px; font-weight:700; transition:.2s ease; }}
    .filter-btn:hover,.filter-btn.active {{ color:var(--card); background:var(--ink); border-color:var(--ink); transform:translateY(-1px); }}
    .filter-btn span {{ opacity:.65; margin-left:4px; }}
    .section-title {{ display:flex; align-items:end; justify-content:space-between; gap:20px; margin:0 0 22px; }}
    .section-title h2 {{ margin:0; font-family:Georgia,"Noto Serif SC",serif; font-size:clamp(30px,4vw,48px); font-weight:400; letter-spacing:-.04em; }}
    .section-title p {{ margin:0 0 8px; color:var(--ink-soft); font-size:13px; }}
    .papers {{ display:grid; gap:20px; }}
    .paper-card {{ display:grid; grid-template-columns:62px 1fr; border-top:1px solid var(--ink); background:rgba(255,253,248,.55); transition:.25s ease; }}
    .paper-card:hover {{ background:var(--card); box-shadow:var(--shadow); transform:translateY(-2px); }}
    .card-rail {{ border-right:1px solid var(--line); padding:26px 13px; font-family:Georgia,serif; font-size:18px; color:var(--accent); }}
    .card-body {{ padding:24px 28px 22px; min-width:0; }}
    .paper-kicker {{ display:flex; align-items:center; flex-wrap:wrap; gap:10px; font-size:11px; font-weight:800; letter-spacing:.06em; color:var(--ink-soft); }}
    .source-badge {{ background:var(--ink); color:var(--card); padding:3px 8px; letter-spacing:.1em; }}
    .source-biorxiv {{ background:var(--teal); }} .source-pubmed {{ background:#355cb5; }} .source-semantic-scholar {{ background:#704f90; }}
    .score {{ margin-left:auto; }}
    .priority-badge {{ color:var(--accent); border:1px solid var(--accent); padding:2px 7px; }}
    .paper-card h3 {{ margin:15px 0 8px; font-family:Georgia,"Noto Serif SC",serif; font-size:clamp(23px,3vw,34px); line-height:1.18; letter-spacing:-.025em; font-weight:500; }}
    .paper-card h3 a {{ text-decoration:none; background:linear-gradient(var(--accent),var(--accent)) left bottom/0 2px no-repeat; transition:background-size .25s; }}
    .paper-card h3 a:hover {{ background-size:100% 2px; }}
    .authors {{ margin:0; color:var(--ink-soft); font-size:13px; }}
    .topic-row {{ display:flex; flex-wrap:wrap; gap:7px; margin:17px 0; }}
    .topic-pill {{ background:#e7eee9; color:#245c58; border-radius:999px; padding:4px 10px; font-size:11px; font-weight:750; }}
    .summary-panel {{ border-left:3px solid var(--lime); background:#f5f7e8; padding:18px 20px; margin-top:18px; }}
    .summary-heading {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:8px; }}
    .summary-heading span {{ font-size:12px; font-weight:900; letter-spacing:.12em; }}
    .summary-heading em {{ font-style:normal; color:var(--ink-soft); font-size:10px; }}
    .takeaway {{ margin:0; font-weight:650; }}
    dl {{ margin:13px 0 0; display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
    dl div {{ border-top:1px solid rgba(19,35,58,.16); padding-top:9px; }}
    dt {{ color:var(--teal); font-size:10px; font-weight:900; letter-spacing:.08em; }}
    dd {{ margin:3px 0 0; color:var(--ink-soft); font-size:12px; line-height:1.55; }}
    .card-footer {{ display:flex; justify-content:space-between; align-items:center; margin-top:18px; gap:12px; }}
    .paper-link {{ display:inline-block; margin-right:10px; padding:7px 12px; background:var(--accent); color:white; text-decoration:none; font-size:11px; font-weight:850; }}
    .paper-link.secondary {{ background:transparent; color:var(--ink); border:1px solid var(--line); }}
    .anchor-link {{ color:var(--ink-soft); text-decoration:none; font-family:monospace; font-size:11px; }}
    .highlight-card:first-child {{ border-top:3px solid var(--accent); }}
    .remaining-section {{ margin-top:72px; }}
    .empty-state {{ border:1px dashed var(--ink-soft); padding:70px 30px; text-align:center; }}
    .empty-state span {{ letter-spacing:.2em; font-size:11px; color:var(--accent); }} .empty-state h3 {{ font-family:Georgia,serif; font-size:32px; margin:10px 0; }}
    .no-results {{ display:none; text-align:center; padding:50px; color:var(--ink-soft); }}
    .site-footer {{ margin-top:90px; padding:34px 0 50px; border-top:1px solid var(--ink); display:flex; justify-content:space-between; gap:30px; color:var(--ink-soft); font-size:12px; }}
    .site-footer strong {{ color:var(--ink); }}
    [hidden] {{ display:none !important; }}
    @media (max-width:800px) {{
      .page-shell {{ width:min(100% - 24px,1180px); }} .hero {{ grid-template-columns:1fr; padding-top:45px; }} .date-block {{ border-left:0; border-top:1px solid var(--line); padding:18px 0 0; }}
      .signal-strip {{ grid-template-columns:repeat(3,1fr); }} .source-line {{ grid-column:1/-1; border-top:1px solid var(--line); }} .stat {{ padding:17px 12px; }}
      .paper-card {{ grid-template-columns:42px 1fr; }} .card-rail {{ padding:22px 9px; font-size:14px; }} .card-body {{ padding:21px 17px; }} dl {{ grid-template-columns:1fr; }}
      .nav-meta .update-label {{ display:none; }} .section-title {{ align-items:start; flex-direction:column; }}
    }}
    @media (max-width:480px) {{ .nav-meta {{ font-size:10px; }} .brand small {{ display:none; }} h1 {{ font-size:50px; }} .paper-card h3 {{ font-size:23px; }} .score {{ margin-left:0; }} .card-footer {{ align-items:flex-start; }} }}
  </style>
</head>
<body>
  <header class="site-header page-shell">
    <nav class="nav" aria-label="主导航">
      <a class="brand" href="#top"><span class="brand-mark" aria-hidden="true"></span><span>CELL PAPER RADAR<small>DAILY RESEARCH SIGNAL</small></span></a>
      <div class="nav-meta"><span class="live-dot">自动更新</span><span class="update-label">{_e(site['timezone'])}</span></div>
    </nav>
  </header>
  <main id="top" class="page-shell">
    <section class="hero">
      <div><div class="eyebrow">DAILY INTELLIGENCE</div><h1>把新论文变成<br><em>研究信号。</em></h1><p class="hero-copy">{_e(site['description'])} 先看相关性，再看方法与局限，把检索时间留给真正值得精读的工作。</p></div>
      <div class="date-block"><span>TODAY'S EDITION</span><strong>{_e(date_label)}</strong><small>更新于 {_e(generated_label)}</small></div>
    </section>
    <section class="signal-strip" aria-label="今日统计">
      <div class="stat"><b>{len(papers):02d}</b><span>相关论文</span></div>
      <div class="stat"><b>{unique_sources:02d}</b><span>有效来源</span></div>
      <div class="stat"><b>{openai_count:02d}</b><span>AI 解读</span></div>
      <div class="source-line">{''.join(source_status)}</div>
    </section>
    <section class="controls" aria-label="筛选论文">
      <label class="search-wrap"><span hidden>搜索论文</span><input id="search" type="search" placeholder="搜索标题、作者、摘要或主题…" autocomplete="off"></label>
      <select class="archive-select" id="archive" aria-label="历史归档"><option value="">最近归档</option>{archive_options}</select>
    </section>
    <div class="filter-row"><button class="filter-btn active" data-topic="">全部 <span>{len(papers)}</span></button>{topic_buttons}</div>
    <section>
      <div class="section-title"><h2>今日优先阅读</h2><p>按研究方向匹配、新鲜度与来源综合排序</p></div>
      <div class="papers" id="highlight-papers">{highlight_cards}</div>
    </section>
    <section class="remaining-section" {'hidden' if len(papers) <= highlight_count else ''}>
      <div class="section-title"><h2>更多相关论文</h2><p>继续浏览今天的候选工作</p></div>
      <div class="papers" id="remaining-papers">{remaining_cards}</div>
    </section>
    <div class="no-results" id="no-results">没有找到符合当前筛选条件的论文。</div>
  </main>
  <footer class="site-footer page-shell"><div><strong>CELL PAPER RADAR</strong><br>自动筛选仅用于文献发现，研究结论请以论文原文为准。</div><div>arXiv · bioRxiv · PubMed · Semantic Scholar<br>Generated by GitHub Actions</div></footer>
  <script type="application/json" id="radar-data">{data_json}</script>
  <script>
    (() => {{
      const search = document.querySelector('#search');
      const buttons = [...document.querySelectorAll('.filter-btn')];
      const cards = [...document.querySelectorAll('.paper-card')];
      const empty = document.querySelector('#no-results');
      let activeTopic = '';
      function applyFilters() {{
        const query = search.value.trim().toLocaleLowerCase();
        let visible = 0;
        cards.forEach(card => {{
          const topicMatch = !activeTopic || card.dataset.topics.split('|').includes(activeTopic);
          const textMatch = !query || card.dataset.search.includes(query);
          card.hidden = !(topicMatch && textMatch);
          if (!card.hidden) visible += 1;
        }});
        empty.style.display = visible ? 'none' : 'block';
        document.querySelectorAll('.remaining-section').forEach(section => {{
          section.hidden = ![...section.querySelectorAll('.paper-card')].some(card => !card.hidden);
        }});
      }}
      buttons.forEach(button => button.addEventListener('click', () => {{
        buttons.forEach(item => item.classList.remove('active'));
        button.classList.add('active');
        activeTopic = button.dataset.topic;
        applyFilters();
      }}));
      search.addEventListener('input', applyFilters);
      document.querySelector('#archive').addEventListener('change', event => {{
        if (event.target.value) window.open(`data/archive/${{event.target.value}}.json`, '_blank');
      }});
    }})();
  </script>
</body>
</html>
"""


def render_html(payload: dict[str, Any], config: dict[str, Any], output: str | Path) -> None:
    output_path = Path(output)
    archive_dir = output_path.parent / "data" / "archive"
    archive_dates = sorted((path.stem for path in archive_dir.glob("*.json")), reverse=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_html(payload, config, archive_dates), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the static paper radar page.")
    parser.add_argument("--config", default=ROOT / "config.yaml")
    parser.add_argument("--input", default=ROOT / "docs" / "data" / "latest.json")
    parser.add_argument("--output", default=ROOT / "docs" / "index.html")
    args = parser.parse_args()
    render_html(read_json(args.input, {"papers": []}), load_config(args.config), args.output)
    print(f"Rendered {Path(args.output)}")


if __name__ == "__main__":
    main()

