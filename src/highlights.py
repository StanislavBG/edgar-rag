"""Generate LLM-powered company highlights from indexed SEC filing data.

Run locally after ingestion to regenerate highlights:
    ANTHROPIC_API_KEY=sk-... python src/highlights.py                    # all companies
    ANTHROPIC_API_KEY=sk-... python src/highlights.py --company apple    # single company

Outputs static JSON to data/highlights/{slug}.json, served by company pages.
Designed to run once per quarter (or whenever new data is ingested).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.db import _full_table_df, get_table, reload_db

logger = logging.getLogger("edgar-highlights")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HIGHLIGHTS_DIR = Path("data/highlights")

# Map company slugs to names (must match company.py)
COMPANY_NAMES = {
    "apple": "Apple Inc.",
    "microsoft": "Microsoft Corp",
    "google": "Alphabet Inc.",
    "amazon": "Amazon.com Inc",
    "nvidia": "NVIDIA Corp",
    "tesla": "Tesla Inc",
    "meta": "Meta Platforms Inc",
    "jpmorgan": "JPMorgan Chase & Co",
    "goldman": "Goldman Sachs Group Inc",
    "visa": "Visa Inc",
    "jnj": "Johnson & Johnson",
}

SYSTEM_PROMPT = """You are a senior equity analyst writing investor-facing executive summaries.

Style rules:
- Write as if briefing a CEO or board member — concise, data-driven, forward-looking.
- Lead with the most important number or trend, not background.
- Use specific dollar amounts, percentages, and growth rates from the filings.
- Highlight inflection points, risks, and strategic shifts.
- Keep each summary to 3-5 paragraphs.
- No hedging language ("it appears", "seems to"). State facts from the filings directly.
- End each summary with 2-3 bullet points: "Key metrics to watch."
- Do NOT use markdown headers or bullet formatting in the prose paragraphs — plain text only.
- Use bullet points ONLY in the "Key metrics to watch" section at the end.
"""


def _get_company_passages(company_name: str) -> dict[str, list[dict]]:
    """Group all passages for a company by filing (type + date)."""
    reload_db()
    table = get_table()
    if table is None:
        return {}

    df = _full_table_df(table)
    company_df = df[df["company_name"].str.contains(company_name.split()[0], case=False)]

    filings: dict[str, list[dict]] = {}
    for _, row in company_df.iterrows():
        key = f"{row['filing_type']} {row['filing_date']}"
        if key not in filings:
            filings[key] = []
        filings[key].append({
            "text": row["text"][:800],
            "section": row.get("section", ""),
        })

    return filings


def _build_context(filings: dict[str, list[dict]], scope: str) -> str:
    """Build filing context for a given scope (latest, 1yr, 3yr)."""
    sorted_keys = sorted(filings.keys(), reverse=True)

    if scope == "latest":
        # Just the most recent 10-K or 10-Q
        for key in sorted_keys:
            if "10-K" in key or "10-Q" in key:
                passages = filings[key]
                text = f"Filing: {key}\n"
                for p in passages[:15]:
                    text += f"[{p['section']}] {p['text']}\n\n"
                return text
        return ""

    elif scope == "1yr":
        # All filings from the last ~12 months
        texts = []
        cutoff_keys = sorted_keys[:8]  # ~4 10-Qs + 1 10-K + some 8-Ks
        for key in cutoff_keys:
            if "10-K" in key or "10-Q" in key:
                passages = filings[key]
                text = f"Filing: {key}\n"
                for p in passages[:8]:
                    text += f"[{p['section']}] {p['text']}\n\n"
                texts.append(text)
        return "\n---\n".join(texts)

    elif scope == "3yr":
        # All 10-K and 10-Q filings from last 3 years
        texts = []
        for key in sorted_keys:
            if "10-K" in key or "10-Q" in key:
                passages = filings[key]
                text = f"Filing: {key}\n"
                for p in passages[:6]:
                    text += f"[{p['section']}] {p['text']}\n\n"
                texts.append(text)
        return "\n---\n".join(texts)

    return ""


PROMPTS = {
    "last_filing": (
        "latest",
        "Write a 'Last Filing Highlight' summary based on this company's most recent SEC filing. "
        "Focus on: revenue performance vs prior quarter/year, product segment winners and losers, "
        "geographic trends (especially Greater China), margin changes, notable guidance or risk disclosures, "
        "and any material events. This should feel like the top-of-mind brief a CEO reads Monday morning.",
    ),
    "last_year": (
        "1yr",
        "Write a 'Last Year in Review' summary covering this company's performance over the past 4 quarters. "
        "Identify the narrative arc of the year: what improved, what declined, what strategic bets were made. "
        "Cover revenue trajectory, margin trends, Services vs Products mix shift, capital returns, "
        "and any regulatory or legal headwinds. A board member should walk away understanding whether "
        "the year was a success and what drove it.",
    ),
    "three_year": (
        "3yr",
        "Write a '3-Year Trend Analysis' covering this company's evolution over the past 3 fiscal years. "
        "Identify secular trends: Services growth vs hardware cyclicality, geographic diversification, "
        "R&D investment trajectory, capital allocation philosophy (buybacks vs dividends vs M&A), "
        "margin expansion or compression, and competitive positioning shifts. "
        "A long-term investor should understand whether this company is structurally strengthening or weakening.",
    ),
}


def generate_highlights(slug: str) -> dict:
    """Generate all 3 highlights for a company using Claude API."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set. Cannot generate highlights.")
        return {}

    company_name = COMPANY_NAMES.get(slug)
    if not company_name:
        logger.error(f"Unknown company slug: {slug}")
        return {}

    client = anthropic.Anthropic(api_key=api_key)
    filings = _get_company_passages(company_name)

    if not filings:
        logger.warning(f"No filings found for {company_name}")
        return {}

    logger.info(f"Generating highlights for {company_name} ({len(filings)} filings)")

    highlights = {}
    for key, (scope, prompt) in PROMPTS.items():
        context = _build_context(filings, scope)
        if not context:
            logger.warning(f"No context for {key} ({scope})")
            continue

        logger.info(f"  Generating {key} ({scope})...")
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Company: {company_name}\n\n{prompt}\n\nFiling data:\n{context[:12000]}",
            }],
        )
        highlights[key] = {
            "title": {
                "last_filing": "Last Filing Highlight",
                "last_year": "Last Year in Review",
                "three_year": "3-Year Trend Analysis",
            }[key],
            "content": message.content[0].text,
            "scope": scope,
        }

    result = {
        "company": company_name,
        "slug": slug,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "highlights": highlights,
    }

    HIGHLIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = HIGHLIGHTS_DIR / f"{slug}.json"
    out_path.write_text(json.dumps(result, indent=2))
    logger.info(f"Saved highlights to {out_path}")

    return result


def load_highlights(slug: str) -> dict | None:
    """Load pre-generated highlights from disk."""
    path = HIGHLIGHTS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        logger.debug(f"Failed to load highlights for {slug}")
        return None


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Generate company highlights")
    parser.add_argument("--company", type=str, help="Single company slug (e.g. apple)")
    args = parser.parse_args()

    if args.company:
        generate_highlights(args.company)
    else:
        for slug in COMPANY_NAMES:
            generate_highlights(slug)


if __name__ == "__main__":
    main()
