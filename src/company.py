"""Company profile pages — built from our own indexed data.

Validates the pipeline end-to-end: ingestion → storage → search → display.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.db import get_filing_count, search
from src.query import embed_query

logger = logging.getLogger("edgar-rag")
router = APIRouter()

COMPANIES = {
    "apple": {
        "name": "Apple Inc.",
        "ticker": "AAPL",
        "cik": "0000320193",
        "sector": "Technology",
        "description": "Consumer electronics, software, and services",
    },
}


def _search(query: str, company: str, top_k: int = 3) -> list[dict]:
    vec = embed_query(query)
    return search(query_vector=vec, top_k=top_k, company=company)


def _build_section(title: str, results: list[dict]) -> str:
    if not results:
        return ""
    cards = ""
    for r in results:
        text = r["text"][:600].replace("\n", " ")
        cards += f"""
        <div class="data-card">
            <div class="data-meta">{r['filing_type']} | {r['filing_date']} | {r['section']}</div>
            <p class="data-text">{text}</p>
            <a href="{r['source_url']}" class="data-source" target="_blank">View on SEC EDGAR</a>
        </div>"""
    return f"""
    <section class="section">
        <h2>{title}</h2>
        {cards}
    </section>"""


def render_company_page(request: Request, slug: str) -> str | None:
    company = COMPANIES.get(slug)
    if not company:
        return None

    name = company["name"]
    ticker = company["ticker"]
    base = str(request.base_url).rstrip("/")
    total_chunks = get_filing_count()

    # Query our own service for each section
    sections_config = [
        ("Revenue & Net Sales", f"{name} total revenue net sales by product segment"),
        ("Product Performance", f"{name} iPhone Mac iPad Wearables Services revenue breakdown"),
        ("Gross Margin", f"{name} gross margin products services profitability"),
        ("Geographic Segments", f"{name} operating income by segment Americas Europe Greater China Japan"),
        ("Risk Factors", f"{name} risk factors material risks"),
        ("Cash & Balance Sheet", f"{name} cash investments total assets balance sheet"),
        ("Share Repurchases & Dividends", f"{name} share repurchase buyback dividend program"),
        ("R&D and Operating Expenses", f"{name} research development operating expenses"),
        ("Management Discussion & Outlook", f"{name} management discussion analysis outlook forward looking"),
        ("Tariffs & Regulatory", f"{name} tariffs trade policy regulatory government"),
    ]

    sections_html = ""
    for title, query in sections_config:
        results = _search(query, company=name, top_k=2)
        sections_html += _build_section(title, results)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name} ({ticker}) SEC Filings — EDGAR RAG</title>
    <meta name="description" content="AI-powered analysis of {name} ({ticker}) SEC filings. Revenue, margins, risk factors, and more from 10-K, 10-Q, and 8-K filings. Powered by EDGAR RAG semantic search.">
    <meta name="keywords" content="{name}, {ticker}, SEC filings, 10-K, 10-Q, 8-K, financial analysis, revenue, earnings, risk factors, gross margin, balance sheet, AI research">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="{base}/company/{slug}">
    <meta property="og:title" content="{name} ({ticker}) SEC Filing Analysis — EDGAR RAG">
    <meta property="og:description" content="Semantic search over {name} SEC filings. Revenue, margins, risk factors from 10-K, 10-Q, 8-K.">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{base}/company/{slug}">
    <script type="application/ld+json">
    {{
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": "{name} ({ticker}) SEC Filing Analysis",
        "description": "AI-powered semantic search over {name} SEC filings",
        "url": "{base}/company/{slug}",
        "isPartOf": {{
            "@type": "WebSite",
            "name": "EDGAR RAG",
            "url": "{base}"
        }}
    }}
    </script>
    <style>
        :root {{
            --bg: #0a0a0a;
            --surface: #141414;
            --border: #262626;
            --text: #e5e5e5;
            --muted: #a3a3a3;
            --accent: #3b82f6;
            --green: #22c55e;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.7;
        }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem; }}
        h1 {{ font-size: 2.25rem; font-weight: 700; }}
        h2 {{ font-size: 1.4rem; font-weight: 600; margin: 2.5rem 0 1rem; color: var(--accent); }}
        p {{ margin-bottom: 0.75rem; color: var(--muted); }}
        a {{ color: var(--accent); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .section {{ margin: 2rem 0; }}
        .badge {{
            display: inline-block;
            padding: 0.2rem 0.6rem;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 9999px;
            font-size: 0.8rem;
            margin: 0.2rem;
        }}
        .badge.alpha {{ border-color: #f59e0b; color: #f59e0b; }}
        .stat-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 0.75rem;
            margin: 1.5rem 0;
        }}
        .stat {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 1rem;
        }}
        .stat-value {{ font-size: 1.25rem; font-weight: 700; color: var(--text); }}
        .stat-label {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.2rem; }}
        .data-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 1.25rem;
            margin: 0.75rem 0;
        }}
        .data-meta {{
            font-size: 0.75rem;
            color: var(--accent);
            font-weight: 500;
            margin-bottom: 0.5rem;
            font-family: monospace;
        }}
        .data-text {{
            color: var(--text);
            font-size: 0.9rem;
            line-height: 1.6;
            margin-bottom: 0.5rem;
        }}
        .data-source {{
            font-size: 0.75rem;
            color: var(--muted);
        }}
        .nav {{ margin-bottom: 1.5rem; font-size: 0.85rem; }}
        .disclaimer {{
            margin-top: 3rem;
            padding-top: 1.5rem;
            border-top: 1px solid var(--border);
            color: var(--muted);
            font-size: 0.8rem;
        }}
        pre {{
            background: #1a1a2e;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1rem;
            overflow-x: auto;
            font-size: 0.85rem;
            margin: 1rem 0;
        }}
        code {{ font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="nav">
            <a href="{base}/">EDGAR RAG</a> / <a href="{base}/company/{slug}">{ticker}</a>
        </div>

        <header>
            <h1>{name} <span style="color: var(--muted); font-weight: 400;">({ticker})</span></h1>
            <p style="color: var(--text); font-size: 1.05rem;">{company['sector']} — {company['description']}</p>
            <div style="margin-top: 0.75rem;">
                <span class="badge alpha">Alpha</span>
                <span class="badge">SEC EDGAR</span>
                <span class="badge">10-K</span>
                <span class="badge">10-Q</span>
                <span class="badge">8-K</span>
            </div>
        </header>

        <div class="stat-grid">
            <div class="stat">
                <div class="stat-value">{total_chunks}</div>
                <div class="stat-label">Indexed passages</div>
            </div>
            <div class="stat">
                <div class="stat-value">2 yrs</div>
                <div class="stat-label">Filing history</div>
            </div>
            <div class="stat">
                <div class="stat-value">$0.01</div>
                <div class="stat-label">Per query</div>
            </div>
        </div>

        <section class="section" style="background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border: 1px solid var(--accent); border-radius: 12px; padding: 1.5rem;">
            <p style="color: var(--text); margin: 0; font-size: 0.95rem;">
                Everything below was retrieved using our own API. Each section is a real query against our indexed {name} SEC filings.
                This page validates our pipeline end-to-end: ingestion, parsing, embedding, search, and display.
            </p>
        </section>

        {sections_html}

        <section class="section">
            <h2>Query This Data Yourself</h2>
            <p>All the data on this page is available via our API. Try it:</p>
<pre><code>curl -X POST {base}/v1/query \\
  -H "Content-Type: application/json" \\
  -d '{{"query": "Apple revenue by segment", "company": "{name}", "top_k": 3}}'</code></pre>
            <p>Or via MCP:</p>
<pre><code>POST {base}/mcp
{{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {{"name": "search_filings",
  "arguments": {{"query": "Apple gross margin", "company": "{name}"}}}}}}</code></pre>
        </section>

        <div class="disclaimer">
            <p>This page was generated automatically from SEC EDGAR public filings indexed by EDGAR RAG. All data is sourced from official SEC filings and is public domain (U.S. government work). This is not financial advice. Always verify against the <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={company['cik']}&type=&dateb=&owner=include&count=40">original filings on SEC.gov</a>.</p>
            <p style="margin-top: 0.5rem;"><a href="{base}/">Back to EDGAR RAG</a> | <a href="mailto:bilko@bilko.run">Feedback</a></p>
        </div>
    </div>
</body>
</html>"""


@router.get("/company/{slug}")
async def company_page(request: Request, slug: str):
    html = render_company_page(request, slug)
    if html is None:
        return HTMLResponse(
            content="<h1>Company not found</h1><p><a href='/'>Back to EDGAR RAG</a></p>",
            status_code=404,
        )
    return HTMLResponse(content=html)
