"""Company profile pages — built from our own indexed data.

Validates the pipeline end-to-end: ingestion → storage → search → display.
"""

from __future__ import annotations

import logging
import re

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
    "microsoft": {
        "name": "Microsoft Corp",
        "ticker": "MSFT",
        "cik": "0000789019",
        "sector": "Technology",
        "description": "Software, cloud computing, and enterprise services",
    },
    "google": {
        "name": "Alphabet Inc.",
        "ticker": "GOOGL",
        "cik": "0001652044",
        "sector": "Technology",
        "description": "Search, advertising, cloud, and AI",
    },
    "amazon": {
        "name": "Amazon.com Inc",
        "ticker": "AMZN",
        "cik": "0001018724",
        "sector": "Technology",
        "description": "E-commerce, cloud (AWS), and digital services",
    },
    "nvidia": {
        "name": "NVIDIA Corp",
        "ticker": "NVDA",
        "cik": "0001045810",
        "sector": "Technology",
        "description": "GPUs, AI accelerators, and data center computing",
    },
    "tesla": {
        "name": "Tesla Inc",
        "ticker": "TSLA",
        "cik": "0001318605",
        "sector": "Automotive",
        "description": "Electric vehicles, energy storage, and solar",
    },
    "meta": {
        "name": "Meta Platforms Inc",
        "ticker": "META",
        "cik": "0001326801",
        "sector": "Technology",
        "description": "Social media, advertising, VR/AR, and AI",
    },
    "jpmorgan": {
        "name": "JPMorgan Chase & Co",
        "ticker": "JPM",
        "cik": "0000019617",
        "sector": "Finance",
        "description": "Investment banking, commercial banking, asset management",
    },
    "goldman": {
        "name": "Goldman Sachs Group Inc",
        "ticker": "GS",
        "cik": "0000886982",
        "sector": "Finance",
        "description": "Investment banking, trading, and asset management",
    },
    "visa": {
        "name": "Visa Inc",
        "ticker": "V",
        "cik": "0001403161",
        "sector": "Finance",
        "description": "Payment technology and digital payments network",
    },
    "jnj": {
        "name": "Johnson & Johnson",
        "ticker": "JNJ",
        "cik": "0000200406",
        "sector": "Healthcare",
        "description": "Pharmaceuticals, medical devices, and consumer health",
    },
}

SECTIONS = [
    {
        "id": "revenue",
        "title": "Revenue & Net Sales",
        "icon": "📊",
        "summary": "Quarterly and annual revenue broken down by product line — iPhone, Mac, iPad, Wearables, and Services.",
        "query": "total net sales revenue iPhone Mac iPad Wearables Services breakdown by product",
    },
    {
        "id": "segments",
        "title": "Geographic Performance",
        "icon": "🌍",
        "summary": "Revenue and operating income by region — Americas, Europe, Greater China, Japan, and Rest of Asia Pacific.",
        "query": "net sales operating income Americas Europe Greater China Japan region segment",
    },
    {
        "id": "margins",
        "title": "Gross Margin",
        "icon": "📈",
        "summary": "Products and Services gross margin trends, with management's explanation of drivers.",
        "query": "gross margin percentage products services increased decreased due to",
    },
    {
        "id": "risks",
        "title": "Risk Factors",
        "icon": "⚠️",
        "summary": "Key risks disclosed in SEC filings — competitive, regulatory, supply chain, legal, and macroeconomic.",
        "query": "risk factors material adverse effect business reputation results",
    },
    {
        "id": "cash",
        "title": "Cash & Investments",
        "icon": "💰",
        "summary": "Cash position, marketable securities, and capital allocation.",
        "query": "cash equivalents marketable securities total assets balance",
    },
    {
        "id": "buybacks",
        "title": "Share Repurchases & Dividends",
        "icon": "🔄",
        "summary": "Stock buyback programs and dividend payments to shareholders.",
        "query": "share repurchase common stock buyback dividend program",
    },
    {
        "id": "rd",
        "title": "R&D and Operating Expenses",
        "icon": "🔬",
        "summary": "Research and development spending, selling/general/administrative costs.",
        "query": "research development operating expenses selling general administrative",
    },
    {
        "id": "outlook",
        "title": "Management Discussion",
        "icon": "💬",
        "summary": "Management's discussion and analysis — business outlook, seasonality, and strategy.",
        "query": "management discussion analysis business seasonality product introductions outlook",
    },
    {
        "id": "tariffs",
        "title": "Tariffs & Trade Policy",
        "icon": "🏛️",
        "summary": "Impact of U.S. tariffs, trade policy, and regulatory changes on operations.",
        "query": "tariffs trade policy imports regulatory government Section 232 semiconductor",
    },
    {
        "id": "legal",
        "title": "Legal Proceedings",
        "icon": "⚖️",
        "summary": "Ongoing litigation, regulatory investigations, and legal settlements.",
        "query": "legal proceedings litigation court appeal settled",
    },
]


def _search(query: str, company: str, top_k: int = 2) -> list[dict]:
    vec = embed_query(query)
    return search(query_vector=vec, top_k=top_k, company=company)


def _clean_text(raw: str) -> str:
    """Clean raw filing text into readable paragraphs."""
    text = raw.replace("\n", " ")
    # Collapse multiple spaces
    text = re.sub(r"\s{2,}", " ", text)
    # Remove page markers like "Apple Inc. | Q3 2025 Form 10-Q | 14"
    text = re.sub(r"Apple Inc\.\s*\|\s*(?:Q\d\s+)?\d{4}\s+Form\s+10-[KQ]\s*\|\s*\d+", "", text)
    # Remove XBRL artifacts
    text = re.sub(r"\b\d{10}\b\s+us-gaap:\w+", "", text)
    # Trim
    text = text.strip()
    # Take first ~400 chars at a sentence boundary
    if len(text) > 450:
        cut = text[:450].rfind(". ")
        if cut > 200:
            text = text[: cut + 1]
        else:
            text = text[:450] + "..."
    return text


def _build_section(section: dict, results: list[dict]) -> str:
    if not results:
        return ""

    cards = ""
    for r in results:
        clean = _clean_text(r["text"])
        filing_label = f"{r['filing_type']} — Filed {r['filing_date']}"
        cards += f"""
            <div class="data-card">
                <p class="data-text">{clean}</p>
                <div class="data-footer">
                    <span class="data-meta">{filing_label}</span>
                    <a href="{r['source_url']}" class="data-source" target="_blank" rel="noopener">Source →</a>
                </div>
            </div>"""

    return f"""
        <section class="section" id="{section['id']}">
            <h2>{section['icon']} {section['title']}</h2>
            <p class="section-summary">{section['summary']}</p>
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

    # Build table of contents
    toc_items = "".join(
        f'<a href="#{s["id"]}" class="toc-link">{s["icon"]} {s["title"]}</a>'
        for s in SECTIONS
    )

    # Query our own service for each section
    sections_html = ""
    for section in SECTIONS:
        results = _search(section["query"], company=name, top_k=2)
        sections_html += _build_section(section, results)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name} ({ticker}) SEC Filings — Financial Data from EDGAR RAG</title>
    <meta name="description" content="Comprehensive analysis of {name} ({ticker}) SEC filings. Revenue by product, gross margins, geographic segments, risk factors, cash position, buybacks, R&amp;D spending, and management outlook. Data from 10-K, 10-Q, and 8-K filings.">
    <meta name="keywords" content="{name}, {ticker}, SEC filings, 10-K, 10-Q, 8-K, revenue, iPhone, Mac, iPad, Services, gross margin, risk factors, balance sheet, share buyback, R&amp;D, tariffs">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="{base}/company/{slug}">
    <meta property="og:title" content="{name} ({ticker}) SEC Filing Analysis — EDGAR RAG">
    <meta property="og:description" content="Revenue, margins, risk factors, and more from {name} SEC filings. Powered by AI semantic search.">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{base}/company/{slug}">
    <script type="application/ld+json">
    {{
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": "{name} ({ticker}) SEC Filing Analysis",
        "description": "AI-powered analysis of {name} SEC filings — revenue, margins, risk factors, and more",
        "url": "{base}/company/{slug}",
        "about": {{
            "@type": "Corporation",
            "name": "{name}",
            "tickerSymbol": "{ticker}"
        }}
    }}
    </script>
    <style>
        :root {{
            --bg: #0a0a0a; --surface: #141414; --border: #262626;
            --text: #e5e5e5; --muted: #a3a3a3; --accent: #3b82f6; --green: #22c55e;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.7; }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem; }}
        h1 {{ font-size: 2.25rem; font-weight: 700; }}
        h2 {{ font-size: 1.35rem; font-weight: 600; margin: 0 0 0.5rem; color: var(--text); }}
        p {{ margin-bottom: 0.75rem; color: var(--muted); }}
        a {{ color: var(--accent); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .section {{ margin: 2.5rem 0; }}
        .section-summary {{ color: var(--muted); font-size: 0.95rem; margin-bottom: 1rem; }}
        .badge {{ display: inline-block; padding: 0.2rem 0.6rem; background: var(--surface); border: 1px solid var(--border); border-radius: 9999px; font-size: 0.8rem; margin: 0.2rem; }}
        .badge.alpha {{ border-color: #f59e0b; color: #f59e0b; }}
        .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; margin: 1.5rem 0; }}
        .stat {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1rem; }}
        .stat-value {{ font-size: 1.25rem; font-weight: 700; color: var(--text); }}
        .stat-label {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.2rem; }}
        .toc {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 1.5rem 0; }}
        .toc-link {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 0.4rem 0.8rem; font-size: 0.8rem; color: var(--muted); transition: border-color 0.2s; }}
        .toc-link:hover {{ border-color: var(--accent); color: var(--accent); text-decoration: none; }}
        .data-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1.25rem; margin: 0.75rem 0; }}
        .data-text {{ color: var(--text); font-size: 0.9rem; line-height: 1.7; margin: 0 0 0.75rem; }}
        .data-footer {{ display: flex; justify-content: space-between; align-items: center; }}
        .data-meta {{ font-size: 0.75rem; color: var(--muted); }}
        .data-source {{ font-size: 0.75rem; color: var(--accent); }}
        .nav {{ margin-bottom: 1.5rem; font-size: 0.85rem; }}
        pre {{ background: #1a1a2e; border: 1px solid var(--border); border-radius: 8px; padding: 1rem; overflow-x: auto; font-size: 0.85rem; margin: 1rem 0; }}
        code {{ font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; }}
        .disclaimer {{ margin-top: 3rem; padding-top: 1.5rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.8rem; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="nav">
            <a href="{base}/">EDGAR RAG</a> / <a href="{base}/company/{slug}">{ticker}</a>
        </div>

        <header>
            <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem;">
                <span class="badge alpha">Alpha</span>
                <span style="color: var(--muted); font-size: 0.85rem;">Updated from SEC EDGAR filings</span>
            </div>
            <h1>{name} <span style="color: var(--muted); font-weight: 400;">({ticker})</span></h1>
            <p style="color: var(--text); font-size: 1.05rem; margin-bottom: 0.25rem;">{company['sector']} — {company['description']}</p>
            <p style="font-size: 0.9rem;">This page is generated entirely from our indexed SEC filings using the EDGAR RAG search API. Every section below is a live query result.</p>
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
                <div class="stat-value">28</div>
                <div class="stat-label">Filings (10-K, 10-Q, 8-K)</div>
            </div>
            <div class="stat">
                <div class="stat-value">$0.01</div>
                <div class="stat-label">Per API query</div>
            </div>
        </div>

        <div class="toc">
            {toc_items}
        </div>

        {sections_html}

        <section class="section">
            <h2>🔍 Query This Data via API</h2>
            <p class="section-summary">Everything on this page is available programmatically. Your agent can search the same data:</p>
<pre><code>curl -X POST {base}/v1/query \\
  -H "Content-Type: application/json" \\
  -d '{{"query": "Apple revenue by product segment", "company": "{name}", "top_k": 3}}'</code></pre>
            <p>Or via <a href="{base}/#api-reference">MCP</a> for direct agent tool integration.</p>
        </section>

        <div class="disclaimer">
            <p>Generated from SEC EDGAR public filings. All data is public domain. This is not financial advice. <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={company['cik']}&type=&dateb=&owner=include&count=40">View original filings on SEC.gov</a></p>
            <p style="margin-top: 0.5rem;"><a href="{base}/">Back to EDGAR RAG</a> | <a href="mailto:bilko@bilko.run">Feedback</a></p>
        </div>
    </div>
</body>
</html>"""


@router.get("/companies")
async def companies_list(request: Request):
    """List all available company profile pages."""
    base = str(request.base_url).rstrip("/")
    total = get_filing_count()

    cards = ""
    for slug, c in COMPANIES.items():
        cards += f"""
        <a href="{base}/company/{slug}" style="text-decoration: none; color: inherit;">
            <div class="company-card">
                <h3>{c['name']} <span style="color: var(--muted);">({c['ticker']})</span></h3>
                <p>{c['sector']} — {c['description']}</p>
            </div>
        </a>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Companies — EDGAR RAG SEC Filing Search</title>
    <meta name="description" content="Browse SEC filing data for {len(COMPANIES)} public companies. Revenue, margins, risk factors from 10-K, 10-Q, 8-K filings.">
    <style>
        :root {{ --bg: #0a0a0a; --surface: #141414; --border: #262626; --text: #e5e5e5; --muted: #a3a3a3; --accent: #3b82f6; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.7; }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem; }}
        h1 {{ font-size: 2rem; font-weight: 700; margin-bottom: 0.5rem; }}
        h3 {{ font-size: 1.1rem; margin: 0 0 0.25rem; }}
        p {{ color: var(--muted); margin: 0; }}
        a {{ color: var(--accent); }}
        .nav {{ margin-bottom: 1.5rem; font-size: 0.85rem; }}
        .badge {{ display: inline-block; padding: 0.15rem 0.5rem; background: var(--surface); border: 1px solid #f59e0b44; border-radius: 6px; font-size: 0.75rem; color: #f59e0b; }}
        .company-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1.25rem; margin: 0.75rem 0; transition: border-color 0.2s; }}
        .company-card:hover {{ border-color: var(--accent); }}
    </style>
</head>
<body>
    <div class="container">
        <div class="nav"><a href="{base}/">EDGAR RAG</a> / <a href="{base}/companies">Companies</a></div>
        <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem;">
            <h1>Companies</h1>
            <span class="badge">Alpha — {len(COMPANIES)} companies</span>
        </div>
        <p style="color: var(--text); margin-bottom: 1.5rem;">{total:,} passages indexed from SEC EDGAR filings. Click a company to see its financial data.</p>
        {cards}
        <p style="margin-top: 2rem; font-size: 0.85rem;"><a href="{base}/data">View full data catalog</a> | <a href="mailto:bilko@bilko.run">Request a company</a></p>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/company/{slug}")
async def company_page(request: Request, slug: str):
    html = render_company_page(request, slug)
    if html is None:
        return HTMLResponse(
            content="<h1>Company not found</h1><p><a href='/'>Back to EDGAR RAG</a></p>",
            status_code=404,
        )
    return HTMLResponse(content=html)
