"""Generate structured financial metrics from indexed SEC filings.

The decision-grade, *numbers* surface for trader agents — quarterly/annual
revenue, net income, EPS, margins, plus a free-form segment breakdown. Runs
locally after ingestion (needs ANTHROPIC_API_KEY), writes static JSON to
data/metrics/{slug}.json, served free over MCP by get_company_metrics.

    ANTHROPIC_API_KEY=sk-... python src/metrics.py                 # all companies
    ANTHROPIC_API_KEY=sk-... python src/metrics.py --company apple # one company

Schema (generic so it fits any company — banks, pharma, tech):
    {
      "company", "slug", "generated_at", "units", "note",
      "quarterly": [{quarter, filing_type, filing_date, revenue, net_income,
                     eps_diluted, gross_margin_pct, operating_income, breakdown}],
      "annual":    [{fiscal_year, filing_date, revenue, net_income, eps_diluted,
                     gross_margin_pct, operating_cash_flow, rd_expense}]
    }
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.highlights import COMPANY_NAMES, _get_company_passages

logger = logging.getLogger("edgar-metrics")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

METRICS_DIR = Path("data/metrics")

SYSTEM_PROMPT = """You extract structured financials from SEC filing text for quantitative analysts.

Rules:
- Output ONLY a single valid JSON object. No prose, no markdown fences.
- All monetary amounts in USD billions (e.g. 94.7 for $94.7B), rounded to one decimal.
- Percentages as plain numbers (41.8 means 41.8%).
- Use null for any field you cannot determine from the provided text — never guess.
- "breakdown" is an optional object of segment/product/geography -> amount (USD billions)
  using whatever segmentation the company actually reports; omit if unclear.
- Only include periods you can support with figures from the text.
"""

SCHEMA_HINT = """Return JSON exactly in this shape:
{
  "units": "USD billions",
  "note": "<one line on fiscal-year convention or caveats>",
  "quarterly": [
    {"quarter": "Q1 FY24", "filing_type": "10-Q", "filing_date": "YYYY-MM-DD",
     "revenue": 0.0, "net_income": 0.0, "eps_diluted": 0.0,
     "gross_margin_pct": 0.0, "operating_income": 0.0,
     "breakdown": {"<segment>": 0.0}}
  ],
  "annual": [
    {"fiscal_year": "FY2023", "filing_date": "YYYY-MM-DD",
     "revenue": 0.0, "net_income": 0.0, "eps_diluted": 0.0,
     "gross_margin_pct": 0.0, "operating_cash_flow": 0.0, "rd_expense": 0.0}
  ]
}"""


def _build_financial_context(filings: dict[str, list[dict]]) -> str:
    """Concatenate financial-statement / MD&A passages across filings, newest first."""
    keywords = (
        "revenue", "net income", "earnings per share", "diluted", "gross margin",
        "operating income", "cash flow", "segment",
    )
    sorted_keys = sorted(filings.keys(), reverse=True)
    parts: list[str] = []
    for key in sorted_keys:
        rows = filings[key]
        relevant = [
            p for p in rows
            if any(kw in p["text"].lower() for kw in keywords)
        ][:10]
        if not relevant:
            continue
        block = f"Filing: {key}\n" + "\n\n".join(
            f"[{p['section']}] {p['text']}" for p in relevant
        )
        parts.append(block)
    return "\n---\n".join(parts)


def _parse_json(raw: str) -> dict | None:
    """Tolerant parse: strip markdown fences and grab the outermost object."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if "```" in s[3:] else s.lstrip("`")
        s = s[4:] if s.lower().startswith("json") else s
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("Failed to parse metrics JSON")
        return None


def generate_metrics(slug: str) -> dict:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set. Cannot generate metrics.")
        return {}

    company_name = COMPANY_NAMES.get(slug)
    if not company_name:
        logger.error(f"Unknown company slug: {slug}")
        return {}

    filings = _get_company_passages(company_name)
    if not filings:
        logger.warning(f"No filings found for {company_name}")
        return {}

    context = _build_financial_context(filings)
    if not context:
        logger.warning(f"No financial context for {company_name}")
        return {}

    logger.info(f"Extracting metrics for {company_name} ({len(filings)} filings)")
    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Company: {company_name}\n\n{SCHEMA_HINT}\n\n"
                    f"Filing data:\n{context[:24000]}"
                ),
            }
        ],
    )
    parsed = _parse_json(message.content[0].text)
    if parsed is None:
        return {}

    result = {
        "company": company_name,
        "slug": slug,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **parsed,
    }
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = METRICS_DIR / f"{slug}.json"
    out_path.write_text(json.dumps(result, indent=2))
    logger.info(f"Saved metrics to {out_path}")
    return result


def load_metrics(slug: str) -> dict | None:
    path = METRICS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        logger.debug(f"Failed to load metrics for {slug}")
        return None


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Generate structured financial metrics")
    parser.add_argument("--company", type=str, help="Single company slug (e.g. apple)")
    args = parser.parse_args()

    if args.company:
        generate_metrics(args.company)
    else:
        for slug in COMPANY_NAMES:
            generate_metrics(slug)


if __name__ == "__main__":
    main()
