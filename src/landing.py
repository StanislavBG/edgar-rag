"""HTML landing page for SEO.

Serves rich HTML to browsers, JSON to agents.
"""

from __future__ import annotations


def render_landing(
    base_url: str,
    filing_count: int,
    companies: list[str],
    tool_schema: dict,
) -> str:
    companies_html = ", ".join(companies) if companies else "Loading first batch..."
    company_count = len(companies) if companies else 0

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EDGAR RAG — SEC Filing Search API for AI Agents | $0.01/query</title>
    <meta name="description" content="Semantic search API over SEC EDGAR filings (10-K, 10-Q, 8-K). AI agents pay $0.01 per query via x402 micropayments. No API keys, no accounts. Instant access to financial data from {company_count}+ public companies.">
    <meta name="keywords" content="SEC EDGAR, API, RAG, AI agents, financial data, 10-K, 10-Q, 8-K, micropayments, x402, USDC, semantic search, SEC filings, stock research, financial analysis, machine learning, LLM tools, MCP server">
    <meta name="robots" content="index, follow">
    <meta name="author" content="BGLabs">
    <link rel="canonical" href="{base_url}/">

    <!-- Open Graph -->
    <meta property="og:title" content="EDGAR RAG — SEC Filing Search for AI Agents">
    <meta property="og:description" content="Semantic search over SEC EDGAR filings. AI agents pay $0.01/query via x402. No accounts needed.">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{base_url}/">

    <!-- Twitter -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="EDGAR RAG — SEC Filing Search for AI Agents">
    <meta name="twitter:description" content="$0.01/query semantic search over 10-K, 10-Q, 8-K filings. Pay with USDC via x402.">

    <!-- Schema.org structured data -->
    <script type="application/ld+json">
    {{
        "@context": "https://schema.org",
        "@type": "WebAPI",
        "name": "EDGAR RAG",
        "description": "Semantic search API over SEC EDGAR filings for AI agents. Pay per query via x402 micropayments.",
        "url": "{base_url}",
        "provider": {{
            "@type": "Organization",
            "name": "BGLabs"
        }},
        "documentation": "{base_url}/",
        "termsOfService": "{base_url}/#terms",
        "offers": {{
            "@type": "Offer",
            "price": "0.01",
            "priceCurrency": "USD",
            "description": "Per query"
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
            --code-bg: #1a1a2e;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.7;
        }}
        .container {{ max-width: 900px; margin: 0 auto; padding: 2rem 1.5rem; }}
        h1 {{ font-size: 2.5rem; font-weight: 700; margin-bottom: 0.5rem; }}
        h2 {{ font-size: 1.5rem; font-weight: 600; margin: 3rem 0 1rem; color: var(--accent); }}
        h3 {{ font-size: 1.1rem; font-weight: 600; margin: 2rem 0 0.75rem; }}
        p {{ margin-bottom: 1rem; color: var(--muted); }}
        a {{ color: var(--accent); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .tagline {{ font-size: 1.25rem; color: var(--green); margin-bottom: 2rem; font-weight: 500; }}
        .badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 9999px;
            font-size: 0.85rem;
            margin: 0.25rem;
        }}
        .badge.live {{ border-color: var(--green); color: var(--green); }}
        .stat-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin: 1.5rem 0;
        }}
        .stat {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.25rem;
        }}
        .stat-value {{ font-size: 1.75rem; font-weight: 700; color: var(--text); }}
        .stat-label {{ font-size: 0.85rem; color: var(--muted); margin-top: 0.25rem; }}
        pre {{
            background: var(--code-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.25rem;
            overflow-x: auto;
            font-size: 0.875rem;
            line-height: 1.6;
            margin: 1rem 0;
        }}
        code {{ font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; }}
        .endpoint {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.5rem;
            margin: 1rem 0;
        }}
        .method {{ color: var(--green); font-weight: 700; font-family: monospace; }}
        .path {{ color: var(--text); font-family: monospace; }}
        .cost {{ color: var(--accent); font-size: 0.85rem; }}
        .step {{
            display: flex;
            gap: 1rem;
            margin: 1rem 0;
            align-items: flex-start;
        }}
        .step-num {{
            background: var(--accent);
            color: white;
            width: 2rem;
            height: 2rem;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 0.85rem;
            flex-shrink: 0;
        }}
        .companies {{ font-size: 0.85rem; color: var(--muted); line-height: 1.8; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 1rem 0;
        }}
        th, td {{
            text-align: left;
            padding: 0.75rem;
            border-bottom: 1px solid var(--border);
            font-size: 0.9rem;
        }}
        th {{ color: var(--muted); font-weight: 500; }}
        .section {{ margin: 3rem 0; }}
        .divider {{ border: none; border-top: 1px solid var(--border); margin: 3rem 0; }}
        footer {{ margin-top: 4rem; padding-top: 2rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.85rem; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>EDGAR RAG</h1>
            <p class="tagline">SEC filings for AI agents. $0.01 per query. No account needed.</p>
            <div>
                <span class="badge live">Live</span>
                <span class="badge">x402 Micropayments</span>
                <span class="badge">MCP Compatible</span>
                <span class="badge">SEC EDGAR</span>
            </div>
        </header>

        <div class="stat-grid">
            <div class="stat">
                <div class="stat-value">{filing_count:,}</div>
                <div class="stat-label">Filing passages indexed</div>
            </div>
            <div class="stat">
                <div class="stat-value">{company_count}</div>
                <div class="stat-label">Companies available</div>
            </div>
            <div class="stat">
                <div class="stat-value">$0.01</div>
                <div class="stat-label">Per query (USDC)</div>
            </div>
            <div class="stat">
                <div class="stat-value">~200ms</div>
                <div class="stat-label">Response time</div>
            </div>
        </div>

        <section class="section">
            <h2>What is EDGAR RAG?</h2>
            <p style="color: var(--text);">
                EDGAR RAG is a semantic search API over SEC EDGAR filings — the official repository of corporate filings
                with the U.S. Securities and Exchange Commission. We index 10-K annual reports, 10-Q quarterly reports,
                and 8-K current event filings from major public companies.
            </p>
            <p>
                Send a natural language query like "What are Apple's risk factors?" and get back the exact relevant
                passages from actual SEC filings, complete with company name, filing date, section label, and a direct
                link to the source document on SEC.gov.
            </p>
            <p>
                Built for AI agents. Payments via the <a href="https://docs.x402.org/">x402 protocol</a> — your agent
                pays $0.01 in USDC on Base L2 per query. No API keys, no subscriptions, no accounts. Just HTTP.
            </p>
        </section>

        <section class="section">
            <h2>Why Agents Use This</h2>
            <p style="color: var(--text);">
                Building your own SEC filing pipeline means downloading terabytes from EDGAR, parsing messy HTML and XBRL,
                chunking documents intelligently, embedding with a vector model, and maintaining a vector database.
                That costs ~$500/month in compute and takes weeks of engineering.
            </p>
            <p>We did it for you. Pay $0.01 per lookup instead.</p>

            <table>
                <tr><th>Approach</th><th>Cost</th><th>Setup Time</th></tr>
                <tr><td>Build your own EDGAR pipeline</td><td>~$500/month</td><td>Weeks</td></tr>
                <tr><td>Valyu financial data API</td><td>$8 per 1,000 tokens</td><td>Hours</td></tr>
                <tr><td><strong>EDGAR RAG</strong></td><td><strong>$0.01 per query</strong></td><td><strong>0 minutes</strong></td></tr>
            </table>
        </section>

        <section class="section">
            <h2>Quick Start</h2>
            <div class="step">
                <div class="step-num">1</div>
                <div><strong>Send a query</strong> — POST to <code>{base_url}/v1/query</code> with your search text</div>
            </div>
            <div class="step">
                <div class="step-num">2</div>
                <div><strong>Get a 402</strong> — Response includes payment details (price, wallet, network)</div>
            </div>
            <div class="step">
                <div class="step-num">3</div>
                <div><strong>Pay $0.01</strong> — Your x402 SDK signs a USDC payment on Base L2</div>
            </div>
            <div class="step">
                <div class="step-num">4</div>
                <div><strong>Retry</strong> — Same request with payment proof in the header</div>
            </div>
            <div class="step">
                <div class="step-num">5</div>
                <div><strong>Get results</strong> — Ranked passages with citations and source URLs</div>
            </div>
        </section>

        <section class="section">
            <h2>API Reference</h2>

            <div class="endpoint">
                <p><span class="method">POST</span> <span class="path">/v1/query</span> <span class="cost">— $0.01 USDC via x402</span></p>
                <p>Search SEC filings by natural language query. Returns ranked text passages with metadata.</p>

                <h3>Request Body</h3>
<pre><code>{{
  "query": "What was Apple's total revenue?",
  "filing_type": "10-Q",          // optional: "10-K", "10-Q", "8-K"
  "company": "Apple Inc.",         // optional: filter by company
  "top_k": 5                      // optional: 1-20, default 5
}}</code></pre>

                <h3>Response</h3>
<pre><code>{{
  "results": [
    {{
      "text": "Total net sales $94,036 $85,777...",
      "score": 0.40,
      "company": "Apple Inc.",
      "cik": "0000320193",
      "filing_type": "10-Q",
      "filing_date": "2025-08-01",
      "section": "10-Q - Full Text",
      "source_url": "https://www.sec.gov/Archives/edgar/data/320193/..."
    }}
  ]
}}</code></pre>

                <h3>Example (curl)</h3>
<pre><code>curl -X POST {base_url}/v1/query \\
  -H "Content-Type: application/json" \\
  -d '{{"query": "total revenue and net income", "top_k": 3}}'</code></pre>

                <h3>Error Responses</h3>
                <table>
                    <tr><th>Code</th><th>Meaning</th></tr>
                    <tr><td>402</td><td>Payment required — send x402 payment and retry</td></tr>
                    <tr><td>413</td><td>Request body exceeds 50KB</td></tr>
                    <tr><td>422</td><td>Validation error — invalid or extra fields</td></tr>
                    <tr><td>429</td><td>Rate limit exceeded (60 requests per minute)</td></tr>
                    <tr><td>503</td><td>Payment verification unavailable (fail-closed — no free access)</td></tr>
                </table>
            </div>

            <div class="endpoint">
                <p><span class="method">POST</span> <span class="path">/mcp</span> <span class="cost">— MCP Streamable HTTP</span></p>
                <p>Model Context Protocol endpoint for AI agent tool discovery and execution.</p>

                <h3>Free Methods</h3>
                <ul style="color: var(--muted); margin: 0.5rem 0 0.5rem 1.5rem;">
                    <li><code>initialize</code> — returns server info and capabilities</li>
                    <li><code>tools/list</code> — returns available tool schemas</li>
                </ul>

                <h3>Paid Methods ($0.01 via x402)</h3>
                <ul style="color: var(--muted); margin: 0.5rem 0 0.5rem 1.5rem;">
                    <li><code>tools/call search_filings</code> — execute a filing search</li>
                </ul>

                <h3>Example: Discover Tools</h3>
<pre><code>curl -X POST {base_url}/mcp \\
  -H "Content-Type: application/json" \\
  -d '{{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}}'</code></pre>
            </div>

            <div class="endpoint">
                <p><span class="method">GET</span> <span class="path">/health</span> <span class="cost">— Free</span></p>
                <p>Service health check. Returns filing count and timestamp.</p>
<pre><code>curl {base_url}/health</code></pre>
            </div>

            <div class="endpoint">
                <p><span class="method">GET</span> <span class="path">/api</span> <span class="cost">— Free</span></p>
                <p>Machine-readable JSON version of this documentation. For agents that prefer structured data.</p>
<pre><code>curl -H "Accept: application/json" {base_url}/</code></pre>
            </div>
        </section>

        <section class="section">
            <h2>How x402 Payments Work</h2>
            <p style="color: var(--text);">
                <a href="https://www.x402.org/">x402</a> is an open protocol that enables instant micropayments over HTTP,
                built on the HTTP 402 "Payment Required" status code. It was developed by Coinbase and is supported by
                Cloudflare, Google, and the broader agent economy ecosystem.
            </p>

            <table>
                <tr><th>Detail</th><th>Value</th></tr>
                <tr><td>Price per query</td><td>$0.01 USD</td></tr>
                <tr><td>Payment asset</td><td>USDC (stablecoin, 1 USDC = $1.00)</td></tr>
                <tr><td>Network</td><td>Base L2 (Coinbase Ethereum L2, chain ID 8453)</td></tr>
                <tr><td>Transaction cost</td><td>~$0.0001 per payment</td></tr>
                <tr><td>Latency overhead</td><td>~200ms for payment verification</td></tr>
                <tr><td>Facilitator</td><td><a href="https://x402.org/facilitator">Coinbase (free tier)</a></td></tr>
            </table>

            <h3>SDK Integration</h3>
            <table>
                <tr><th>Language</th><th>Install</th></tr>
                <tr><td>Python</td><td><code>pip install x402</code></td></tr>
                <tr><td>TypeScript</td><td><code>npm install @x402/fetch</code></td></tr>
                <tr><td>Specification</td><td><a href="https://docs.x402.org/">docs.x402.org</a></td></tr>
            </table>
        </section>

        <section class="section">
            <h2>Supported Filing Types</h2>
            <table>
                <tr><th>Type</th><th>Description</th><th>Frequency</th></tr>
                <tr><td><strong>10-K</strong></td><td>Annual report — comprehensive overview of business, financials, risk factors, MD&amp;A</td><td>Yearly</td></tr>
                <tr><td><strong>10-Q</strong></td><td>Quarterly report — interim financial statements, management discussion</td><td>3x per year</td></tr>
                <tr><td><strong>8-K</strong></td><td>Current event report — material events, leadership changes, acquisitions</td><td>As needed</td></tr>
            </table>
        </section>

        <section class="section">
            <h2>Companies Available</h2>
            <p class="companies">{companies_html}</p>
            <p>More companies added monthly. Data sourced from <a href="https://www.sec.gov/edgar/searchedgar/companysearch">SEC EDGAR</a> (public domain, U.S. government data).</p>
        </section>

        <section class="section">
            <h2>Use Cases</h2>

            <h3>Financial Research Agents</h3>
            <p>AI agents analyzing earnings, comparing companies, or building investment theses can query specific financial metrics across multiple companies and time periods.</p>

            <h3>Compliance & Risk Monitoring</h3>
            <p>Automated compliance tools can monitor risk factor disclosures, legal proceedings, and material events across portfolios of companies.</p>

            <h3>Trading Signal Generation</h3>
            <p>Quantitative strategies that incorporate fundamental data from SEC filings — revenue trends, margin changes, management commentary.</p>

            <h3>Due Diligence Automation</h3>
            <p>M&amp;A research agents can rapidly scan target company filings for risk factors, legal issues, and financial health indicators.</p>

            <h3>LLM Tool Integration</h3>
            <p>Add SEC filing search as a tool for ChatGPT, Claude, or any LLM via the MCP endpoint. The agent discovers tools for free, pays only when executing searches.</p>
        </section>

        <section class="section">
            <h2>Technical Details</h2>
            <table>
                <tr><th>Component</th><th>Detail</th></tr>
                <tr><td>Embedding model</td><td>BAAI/bge-small-en-v1.5 (384 dimensions)</td></tr>
                <tr><td>Vector database</td><td>LanceDB (embedded, on-disk)</td></tr>
                <tr><td>Chunking strategy</td><td>Section-aware: splits by 10-K Item sections, 512 token target</td></tr>
                <tr><td>Search method</td><td>Approximate nearest neighbor with metadata filters</td></tr>
                <tr><td>Data freshness</td><td>Monthly updates from SEC EDGAR full-index</td></tr>
                <tr><td>Rate limit</td><td>60 requests per minute per IP</td></tr>
                <tr><td>Security</td><td>HSTS, input validation (Pydantic), fail-closed payments</td></tr>
            </table>
        </section>

        <hr class="divider">

        <section class="section" id="terms">
            <h2>Terms of Service</h2>
            <p><strong>Acceptance:</strong> By sending requests to this API, you agree to these terms.</p>
            <p><strong>Service:</strong> EDGAR RAG provides semantic search over publicly available SEC EDGAR filings. Results are passages from public filings, not financial advice.</p>
            <p><strong>No Warranty:</strong> This service is provided "as is" without warranty. Filing data may be incomplete, delayed, or contain parsing artifacts. Always verify against the original SEC filing via the source_url provided in results.</p>
            <p><strong>Rate Limits:</strong> 60 requests per minute per IP address. Excessive use may result in temporary blocking.</p>
            <p><strong>Acceptable Use:</strong> Automated queries from AI agents and software are welcome. Do not use this service for market manipulation, fraud, or any illegal activity.</p>
            <p><strong>Liability:</strong> We are not liable for trading losses, investment decisions, or any damages arising from use of this service or its data.</p>
        </section>

        <section class="section" id="privacy">
            <h2>Data Privacy</h2>
            <p><strong>What we collect:</strong> Client IP address, payment transaction hashes, request timestamps, and response status codes.</p>
            <p><strong>What we do NOT collect:</strong> Query content, payment headers, wallet addresses, or any personally identifiable information.</p>
            <p><strong>Data source:</strong> All filing data comes from SEC EDGAR, which is public domain (U.S. government work, no copyright).</p>
            <p><strong>Payments:</strong> Processed on-chain via x402 on Base L2. We receive USDC. We do not store wallet private keys or payment credentials.</p>
            <p><strong>Retention:</strong> Server logs retained up to 30 days for operational monitoring, then deleted.</p>
        </section>

        <footer>
            <p>
                <a href="https://github.com/StanislavBG/edgar-rag">Source Code</a> &middot;
                <a href="{base_url}/health">Status</a> &middot;
                <a href="{base_url}/api">API (JSON)</a> &middot;
                <a href="https://docs.x402.org/">x402 Protocol</a> &middot;
                Contact: bilko@bilko.run
            </p>
            <p style="margin-top: 0.5rem;">Data sourced from SEC EDGAR (public domain). Not financial advice.</p>
        </footer>
    </div>
</body>
</html>"""
