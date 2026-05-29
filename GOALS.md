# Goals

## North star
Make SEC EDGAR the **pay-per-use data + intelligence source for AI agents** — a fleet
of x402-monetized MCP servers, hosted on Replit, registered everywhere agents look.

## Moat
Not the raw filings (public, commoditized). The **local pipeline** that turns filings
into (a) searchable vectors and (b) **pre-computed intelligence** an agent would
otherwise spend ~$500/mo building. Agents rent it instead. Local produces → Replit serves.

## Current state (2026-05)
- 1 MCP (`edgar-rag`), 4 raw-data tools: `search_filings`, `get_filing`, `list_companies`, `get_data_catalog`
- 11 companies (alpha batch), 409 filings, ~11.5k chunks, 2021→2026
- Free alpha (`X402_PRICE=$0.00`); x402 plumbed but not enforced on `/mcp`
- 1 trader agent ready to beta test
- Intelligence artifacts exist but are orphaned: `apple.json` (narrative) + `apple_charts.json`
  (structured metrics) — Apple only, served nowhere, no generator for the metrics

## Phase goal (~6 weeks)
**A trader agent can pull decision-grade, citeable EDGAR intelligence on the 11 tracked
companies through MCP — for free — and trust it's current.**

Decisions locked:
- **Focus:** depth — the intelligence layer (the differentiator), not breadth
- **Beta proves:** the data is good enough to *act on* (quality, not willingness-to-pay)
- **Pricing:** stay free, learn first

## Steps
1. **Freshness (prerequisite).** Idempotent ingest (skip already-indexed accessions) +
   local weekly cron refresh + ship-to-prod loop. Stale data fails the beta on day one.
2. **Expose intelligence as MCP tools.** `get_company_highlights` (narrative) and
   `get_company_metrics` (structured financials) — free, served from precomputed JSON,
   no per-call LLM cost. Regenerate for all 11 companies.
3. **Make it decision-grade.** A metrics-extraction stage in the local pipeline that
   pulls structured financials (revenue, segments, YoY) per filing — numbers a trader
   computes on, with citations. Generalizes `apple_charts.json` to all 11.
4. **Beta loop.** Point the trader agent at it; capture "was this enough to act on?";
   iterate on quality. This is what the phase is meant to prove.

## Explicitly deferred (do NOT spend time here yet)
- x402 pricing + `/mcp tools/call` gating — revisit after the free beta gives signal
- Expanding past 11 companies to the full ~50 — breadth is commoditized
- MCP directory/registry distribution — only matters once one MCP is genuinely good

## Operating model
- **Ingestion/indexing/intelligence generation run locally** (where torch + API keys live),
  on a weekly cron. See `src/refresh.py` and the `/refresh` flow in `.claude/CLAUDE.md`.
- **Prod (Replit Cloud Run) serves only.** Data ships as a read-only bundle baked into
  the build image via the `v0.1.0-data` GitHub Release; a redeploy picks up new data.
