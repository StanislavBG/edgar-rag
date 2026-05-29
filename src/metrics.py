"""Structured financial metrics from SEC XBRL — exact, no LLM.

Numbers a trader acts on must be exact, so we pull them from SEC's structured
XBRL facts (data.sec.gov) rather than LLM-extracting from prose. Instant, free,
full history, zero hallucination. (Narrative analysis stays in highlights.py.)

    python src/metrics.py                 # all companies
    python src/metrics.py --company apple  # one company

Writes data/metrics/{slug}.json (served free over MCP by get_company_metrics):
    {company, slug, cik, generated_at, units, source,
     quarterly: [{quarter, fiscal_year, period_end, revenue, net_income,
                  eps_diluted, gross_margin_pct, operating_income}],
     annual:    [{fiscal_year, period_end, revenue, ...}]}
All monetary values in USD billions. Q4 is derived (annual minus Q1+Q2+Q3).
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.company import COMPANIES

logger = logging.getLogger("edgar-metrics")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

METRICS_DIR = Path("data/metrics")
COMPANYFACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# First matching tag wins — revenue tagging varies by industry.
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
]
NET_INCOME_TAGS = ["NetIncomeLoss"]
EPS_TAGS = ["EarningsPerShareDiluted"]
GROSS_PROFIT_TAGS = ["GrossProfit"]
OPERATING_INCOME_TAGS = ["OperatingIncomeLoss"]


def _client():
    from src.ingest import get_sec_client

    return get_sec_client()


def _merged_points(usgaap: dict, tags: list[str]) -> list[dict]:
    """Concatenate unit points across ALL present tags — companies switch tags
    across eras (e.g. SalesRevenueNet → RevenueFromContractWithCustomer...), so
    merging fills full history. Per-period dedup happens downstream (newest wins)."""
    out: list[dict] = []
    for tag in tags:
        node = usgaap.get(tag)
        if not node:
            continue
        units = node.get("units", {})
        ukey = next(iter(units), None)
        if ukey:
            out.extend(units[ukey])
    return out


def _duration_days(pt: dict) -> int | None:
    try:
        return (date.fromisoformat(pt["end"]) - date.fromisoformat(pt["start"])).days
    except (KeyError, ValueError):
        return None


def _dedup_by_end(points: list[dict], lo: int, hi: int, only_fy: bool = False) -> dict[str, float]:
    """period-end -> value for points whose duration is in [lo, hi] days.

    Keyed by END (the period's true identity), most-recently-FILED wins. XBRL
    fy/fp reflect the *filing's* context, not the period — the same quarter is
    re-reported as a comparative in later filings with a shifted fy/fp — so the
    fiscal NUMBER is unusable for labeling (we derive that from the end date).
    But fp=="FY" still reliably marks a *fiscal-year* period, so only_fy uses it
    to exclude trailing-12-month spans that also fall in the annual day-range.
    """
    out: dict[str, tuple[str, float]] = {}  # end -> (filed, val)
    for pt in points:
        d = _duration_days(pt)
        if d is None or not (lo <= d <= hi) or "val" not in pt:
            continue
        if only_fy and pt.get("fp") != "FY":
            continue
        filed = pt.get("filed", "")
        if pt["end"] not in out or filed >= out[pt["end"]][0]:
            out[pt["end"]] = (filed, pt["val"])
    return {end: v for end, (_, v) in out.items()}


def _fye_month(annual_ends: list[str]) -> int:
    """Detect the fiscal-year-end month (mode of annual period-end months)."""
    from collections import Counter

    months = [date.fromisoformat(e).month for e in annual_ends if e]
    return Counter(months).most_common(1)[0][0] if months else 12


def _fiscal_label(end_str: str, fye_month: int) -> tuple[int, str]:
    """(fiscal_year, 'Qn FYyyyy') derived from a period-end date + FYE month."""
    e = date.fromisoformat(end_str)
    fy = e.year + 1 if e.month > fye_month else e.year
    mdiff = (e.month - fye_month) % 12
    q = round(mdiff / 3) or 4  # 3→Q1, 6→Q2, 9→Q3, 0→Q4
    return fy, f"Q{q} FY{fy}"


def _b(val: float | None) -> float | None:
    """USD -> USD billions, 3 dp."""
    return None if val is None else round(val / 1e9, 3)


def _margin(gross: float | None, revenue: float | None) -> float | None:
    if gross is None or not revenue:
        return None
    return round(gross / revenue * 100, 1)


def generate_metrics(slug: str) -> dict:
    meta = COMPANIES.get(slug)
    if not meta:
        logger.error(f"Unknown company slug: {slug}")
        return {}
    cik = meta["cik"]

    client = _client()
    try:
        resp = client.get(COMPANYFACTS.format(cik=cik))
    finally:
        client.close()
    if resp.status_code != 200:
        logger.error("companyfacts HTTP %s for %s", resp.status_code, slug)
        return {}
    usgaap = resp.json().get("facts", {}).get("us-gaap", {})

    rev = _merged_points(usgaap, REVENUE_TAGS)
    ni = _merged_points(usgaap, NET_INCOME_TAGS)
    eps = _merged_points(usgaap, EPS_TAGS)
    gp = _merged_points(usgaap, GROSS_PROFIT_TAGS)
    oi = _merged_points(usgaap, OPERATING_INCOME_TAGS)

    # Quarterly (~90-day) and annual (~365-day) values, keyed by period end.
    rev_q, ni_q, eps_q, gp_q, oi_q = (_dedup_by_end(p, 80, 100) for p in (rev, ni, eps, gp, oi))
    rev_a, ni_a, eps_a, gp_a, oi_a = (
        _dedup_by_end(p, 350, 380, only_fy=True) for p in (rev, ni, eps, gp, oi)
    )
    fye = _fye_month(list(rev_a) + list(ni_a))

    # --- quarterly rows (reported Q1-Q3), one per net-income quarter end ---
    quarterly = []
    for end in sorted(set(ni_q) | set(rev_q)):
        fy, label = _fiscal_label(end, fye)
        r = rev_q.get(end)
        quarterly.append({
            "quarter": label,
            "fiscal_year": fy,
            "period_end": end,
            "revenue": _b(r),
            "net_income": _b(ni_q.get(end)),
            "eps_diluted": eps_q.get(end),
            "gross_margin_pct": _margin(gp_q.get(end), r),
            "operating_income": _b(oi_q.get(end)),
        })

    # --- annual rows, one per FYE-aligned annual end ---
    ann_fy = {}  # fiscal_year -> {end, rev, ni, eps, gp, oi}
    annual = []
    for end in sorted(set(rev_a) | set(ni_a)):
        fy, _ = _fiscal_label(end, fye)
        r = rev_a.get(end)
        ann_fy[fy] = {"end": end, "rev": r, "ni": ni_a.get(end),
                      "eps": eps_a.get(end), "gp": gp_a.get(end), "oi": oi_a.get(end)}
        annual.append({
            "fiscal_year": fy,
            "period_end": end,
            "revenue": _b(r),
            "net_income": _b(ni_a.get(end)),
            "eps_diluted": eps_a.get(end),
            "gross_margin_pct": _margin(gp_a.get(end), r),
            "operating_income": _b(oi_a.get(end)),
        })

    # --- derived Q4 (annual minus sum of that FY's reported Q1-Q3) ---
    def _q4(annual_b, rows, k):
        vals = [row[k] for row in rows if row[k] is not None]
        s = sum(vals) if len(vals) == len(rows) else None
        return None if annual_b is None or s is None else round(annual_b - s, 3)

    for fy, a in ann_fy.items():
        qs = [row for row in quarterly if row["fiscal_year"] == fy]
        if len(qs) != 3:  # need exactly Q1-Q3 to back out Q4
            continue
        quarterly.append({
            "quarter": f"Q4 FY{fy}",
            "fiscal_year": fy,
            "period_end": a["end"],
            "revenue": _q4(_b(a["rev"]), qs, "revenue"),
            "net_income": _q4(_b(a["ni"]), qs, "net_income"),
            "eps_diluted": _q4(a["eps"], qs, "eps_diluted"),
            "gross_margin_pct": _margin(a["gp"], a["rev"]),
            "operating_income": _q4(_b(a["oi"]), qs, "operating_income"),
            "derived": True,
        })

    quarterly.sort(key=lambda x: x["period_end"])

    result = {
        "company": meta["name"],
        "slug": slug,
        "cik": cik,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "units": "USD billions (EPS in USD/share, margins in %)",
        "source": "SEC XBRL companyfacts (data.sec.gov)",
        "quarterly": quarterly,
        "annual": annual,
    }
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    (METRICS_DIR / f"{slug}.json").write_text(json.dumps(result, indent=2))
    logger.info(
        "Saved %s metrics: %d quarters, %d years", slug, len(quarterly), len(annual)
    )
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
    parser = argparse.ArgumentParser(description="Generate structured financials from SEC XBRL")
    parser.add_argument("--company", type=str, help="Single company slug (e.g. apple)")
    args = parser.parse_args()

    slugs = [args.company] if args.company else list(COMPANIES)
    for slug in slugs:
        generate_metrics(slug)


if __name__ == "__main__":
    main()
