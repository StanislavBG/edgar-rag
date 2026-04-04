"""Request audit log — SQLite-backed request tracking for the admin dashboard.

Logs every inbound HTTP request (minus noisy paths) with timing, status, and
coarse client metadata. Privacy rules from CLAUDE.md apply:
- Query content truncated at 50 chars for pattern detection
- No payment headers ever logged
- IPs shown as first two octets only in UI (stored raw for join-by-ip aggregation)
- User agents truncated at 100 chars
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.types import ASGIApp

logger = logging.getLogger("edgar-rag")

DB_PATH = Path("data/audit.db")

SKIP_PATHS = {"/health", "/favicon.ico", "/robots.txt"}
SKIP_PREFIXES = ("/static/",)

# Endpoints we bucket traffic into on the dashboard
TRACKED_BUCKETS = [
    "/v1/query",
    "/mcp",
    "/company/",
    "/",
    "/api",
    "/data",
    "/companies",
    "/health",
    "/admin",
    "/upload-vectors",
    "other",
]


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA journal_size_limit=10000000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            ip TEXT NOT NULL,
            user_agent TEXT NOT NULL,
            referer TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL,
            x402_paid INTEGER NOT NULL,
            tx_hash TEXT NOT NULL,
            x_admin_key_used INTEGER NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_path ON requests(path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_requests_ip ON requests(ip)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS query_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            query_preview TEXT NOT NULL,
            company TEXT NOT NULL,
            filing_type TEXT NOT NULL,
            ip TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_querylog_ts ON query_log(timestamp)")
    conn.commit()
    return conn


def _should_skip(path: str) -> bool:
    if path in SKIP_PATHS:
        return True
    for pfx in SKIP_PREFIXES:
        if path.startswith(pfx):
            return True
    return False


def _bucket_for(path: str) -> str:
    if path == "/" or path == "":
        return "/"
    for prefix in ("/v1/query", "/mcp", "/api", "/data", "/companies", "/health",
                   "/admin", "/upload-vectors"):
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?"):
            return prefix
    if path.startswith("/company/"):
        return "/company/"
    return "other"


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "-"


def _hash_ip(ip: str) -> str:
    """Return first two octets of IPv4, or first segment of IPv6, for display."""
    if not ip or ip == "-":
        return "-"
    if ":" in ip:
        return ip.split(":")[0] + ":*"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return ip[:8] + "*"


def _record(row: dict[str, Any]) -> None:
    try:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO requests
            (timestamp, method, path, ip, user_agent, referer, status_code,
             duration_ms, x402_paid, tx_hash, x_admin_key_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["timestamp"],
                row["method"],
                row["path"],
                row["ip"],
                row["user_agent"],
                row["referer"],
                row["status_code"],
                row["duration_ms"],
                1 if row["x402_paid"] else 0,
                row["tx_hash"],
                1 if row["x_admin_key_used"] else 0,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("audit: failed to record request")


def _record_query(
    endpoint: str,
    query_text: str,
    company: str,
    filing_type: str,
    ip: str,
) -> None:
    try:
        preview = (query_text or "")[:50]
        conn = _conn()
        conn.execute(
            """
            INSERT INTO query_log (timestamp, endpoint, query_preview, company, filing_type, ip)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                endpoint,
                preview,
                company or "",
                filing_type or "",
                ip,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        logger.exception("audit: failed to record query")


class AuditMiddleware:
    """ASGI middleware that records every non-skipped request to audit.db."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if _should_skip(path):
            await self.app(scope, receive, send)
            return

        # Buffer request body so we can both snoop it for query pattern tracking
        # AND replay it downstream. Cap at 64KB for safety.
        body_chunks: list[bytes] = []
        more = True
        while more:
            msg = await receive()
            if msg["type"] == "http.request":
                body_chunks.append(msg.get("body", b""))
                more = msg.get("more_body", False)
                if sum(len(c) for c in body_chunks) > 64 * 1024:
                    more = False
                    # drain remaining so downstream sees full body
                    while msg.get("more_body", False):
                        msg = await receive()
                        body_chunks.append(msg.get("body", b""))
                        more = msg.get("more_body", False)
            else:
                # disconnect — no body
                break
        full_body = b"".join(body_chunks)

        request = Request(scope)
        ip = _client_ip(request)
        ua = (request.headers.get("user-agent", "") or "-")[:100]
        referer = (request.headers.get("referer", "") or "-")[:200]
        admin_key = request.headers.get("x-admin-key", "")
        expected = os.environ.get("UPLOAD_SECRET", "")
        admin_used = bool(admin_key and expected and admin_key == expected)
        has_x402 = bool(request.headers.get("x-payment") or request.headers.get("x-payment-response"))

        # Query pattern capture (no full content)
        qinfo: tuple[str, str, str] | None = None
        if path in ("/v1/query", "/mcp") and full_body:
            try:
                data = json.loads(full_body.decode("utf-8", errors="replace"))
                if path == "/v1/query" and isinstance(data, dict):
                    qinfo = (
                        str(data.get("query", ""))[:50],
                        str(data.get("company", "") or "")[:200],
                        str(data.get("filing_type", "") or "")[:20],
                    )
                elif path == "/mcp" and isinstance(data, dict) and data.get("method") == "tools/call":
                    args = (data.get("params") or {}).get("arguments") or {}
                    if isinstance(args, dict):
                        qinfo = (
                            str(args.get("query", ""))[:50],
                            str(args.get("company", "") or "")[:200],
                            str(args.get("filing_type", "") or "")[:20],
                        )
            except (ValueError, UnicodeDecodeError):
                qinfo = None

        # Replay buffered body to downstream app
        sent = {"done": False}

        async def replay_receive():
            if sent["done"]:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent["done"] = True
            return {"type": "http.request", "body": full_body, "more_body": False}

        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message):  # type: ignore[no-untyped-def]
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, replay_receive, send_wrapper)
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            # If a 200 was returned to /v1/query and x-payment header was present,
            # treat as paid. tx_hash we don't have in-band; leave "-".
            x402_paid = has_x402 and status_code < 400 and path in ("/v1/query", "/mcp")
            _record(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "method": scope.get("method", "-"),
                    "path": path[:500],
                    "ip": ip,
                    "user_agent": ua,
                    "referer": referer,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "x402_paid": x402_paid,
                    "tx_hash": "-",
                    "x_admin_key_used": admin_used,
                }
            )
            if qinfo is not None and status_code < 500:
                _record_query(path, qinfo[0], qinfo[1], qinfo[2], ip)


# --- Admin router ---

router = APIRouter()


def _require_admin(x_admin_key: str | None) -> None:
    expected = os.environ.get("UPLOAD_SECRET", "")
    if not expected or not x_admin_key or x_admin_key != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _since(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    k = max(0, min(len(values) - 1, round((pct / 100.0) * (len(values) - 1))))
    return values[k]


def _gather_stats() -> dict[str, Any]:
    conn = _conn()
    now = datetime.now(timezone.utc)

    def count_since(hours: int) -> int:
        cutoff = (now - timedelta(hours=hours)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE timestamp >= ?", (cutoff,)
        ).fetchone()
        return int(row[0]) if row else 0

    totals = {
        "24h": count_since(24),
        "7d": count_since(24 * 7),
        "30d": count_since(24 * 30),
        "all": int(conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]),
    }

    # By endpoint bucket (last 7d)
    cutoff_7d = _since(24 * 7)
    rows = conn.execute(
        "SELECT path FROM requests WHERE timestamp >= ?", (cutoff_7d,)
    ).fetchall()
    bucket_counts: dict[str, int] = {b: 0 for b in TRACKED_BUCKETS}
    for (path,) in rows:
        b = _bucket_for(path)
        bucket_counts[b] = bucket_counts.get(b, 0) + 1

    # By status code class (7d)
    status_rows = conn.execute(
        "SELECT status_code, COUNT(*) FROM requests WHERE timestamp >= ? GROUP BY status_code",
        (cutoff_7d,),
    ).fetchall()
    status_class = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0}
    for code, n in status_rows:
        if 200 <= code < 300:
            status_class["2xx"] += n
        elif 300 <= code < 400:
            status_class["3xx"] += n
        elif 400 <= code < 500:
            status_class["4xx"] += n
        elif 500 <= code < 600:
            status_class["5xx"] += n
        else:
            status_class["other"] += n

    # Top referrers (7d)
    top_referers = conn.execute(
        """
        SELECT referer, COUNT(*) as n FROM requests
        WHERE timestamp >= ? AND referer != '-'
        GROUP BY referer ORDER BY n DESC LIMIT 10
        """,
        (cutoff_7d,),
    ).fetchall()

    # Top user agents (7d)
    top_uas = conn.execute(
        """
        SELECT user_agent, COUNT(*) as n FROM requests
        WHERE timestamp >= ? AND user_agent != '-'
        GROUP BY user_agent ORDER BY n DESC LIMIT 10
        """,
        (cutoff_7d,),
    ).fetchall()

    # Top IPs (7d) — hashed for display
    top_ips_raw = conn.execute(
        """
        SELECT ip, COUNT(*) as n FROM requests
        WHERE timestamp >= ? AND ip != '-'
        GROUP BY ip ORDER BY n DESC LIMIT 10
        """,
        (cutoff_7d,),
    ).fetchall()
    top_ips = [(_hash_ip(ip), n) for ip, n in top_ips_raw]

    # x402 stats (all time and 7d)
    paid_24h = int(
        conn.execute(
            "SELECT COUNT(*) FROM requests WHERE x402_paid = 1 AND timestamp >= ?",
            (_since(24),),
        ).fetchone()[0]
    )
    paid_7d = int(
        conn.execute(
            "SELECT COUNT(*) FROM requests WHERE x402_paid = 1 AND timestamp >= ?",
            (cutoff_7d,),
        ).fetchone()[0]
    )
    paid_all = int(
        conn.execute("SELECT COUNT(*) FROM requests WHERE x402_paid = 1").fetchone()[0]
    )

    # Response time percentiles (7d)
    durations = [
        int(r[0])
        for r in conn.execute(
            "SELECT duration_ms FROM requests WHERE timestamp >= ?", (cutoff_7d,)
        ).fetchall()
    ]
    percentiles = {
        "p50": _percentile(durations, 50),
        "p95": _percentile(durations, 95),
        "p99": _percentile(durations, 99),
    }

    # Traffic timeline — last 24h bucketed per hour
    timeline_rows = conn.execute(
        """
        SELECT substr(timestamp, 1, 13) as hr, COUNT(*) as n
        FROM requests WHERE timestamp >= ?
        GROUP BY hr ORDER BY hr
        """,
        (_since(24),),
    ).fetchall()
    timeline = [(hr, int(n)) for hr, n in timeline_rows]

    # Query patterns (7d)
    top_queries = conn.execute(
        """
        SELECT query_preview, COUNT(*) as n FROM query_log
        WHERE timestamp >= ? AND query_preview != ''
        GROUP BY query_preview ORDER BY n DESC LIMIT 15
        """,
        (cutoff_7d,),
    ).fetchall()
    top_companies = conn.execute(
        """
        SELECT company, COUNT(*) as n FROM query_log
        WHERE timestamp >= ? AND company != ''
        GROUP BY company ORDER BY n DESC LIMIT 10
        """,
        (cutoff_7d,),
    ).fetchall()
    top_filing_types = conn.execute(
        """
        SELECT filing_type, COUNT(*) as n FROM query_log
        WHERE timestamp >= ? AND filing_type != ''
        GROUP BY filing_type ORDER BY n DESC LIMIT 10
        """,
        (cutoff_7d,),
    ).fetchall()

    conn.close()

    return {
        "totals": totals,
        "by_endpoint_7d": bucket_counts,
        "status_classes_7d": status_class,
        "top_referrers_7d": [[r, int(n)] for r, n in top_referers],
        "top_user_agents_7d": [[u, int(n)] for u, n in top_uas],
        "top_ips_7d": [[i, int(n)] for i, n in top_ips],
        "x402": {"paid_24h": paid_24h, "paid_7d": paid_7d, "paid_all": paid_all,
                 "volume_usd_7d": round(paid_7d * 0.01, 2),
                 "volume_usd_all": round(paid_all * 0.01, 2)},
        "percentiles_ms_7d": percentiles,
        "timeline_24h": timeline,
        "top_queries_7d": [[q, int(n)] for q, n in top_queries],
        "top_companies_7d": [[c, int(n)] for c, n in top_companies],
        "top_filing_types_7d": [[f, int(n)] for f, n in top_filing_types],
    }


def _recent_requests(limit: int = 100) -> list[dict[str, Any]]:
    conn = _conn()
    rows = conn.execute(
        """
        SELECT timestamp, method, path, ip, user_agent, referer, status_code,
               duration_ms, x402_paid, x_admin_key_used
        FROM requests ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [
        {
            "timestamp": r[0],
            "method": r[1],
            "path": r[2],
            "ip": _hash_ip(r[3]),
            "user_agent": r[4],
            "referer": r[5],
            "status_code": r[6],
            "duration_ms": r[7],
            "x402_paid": bool(r[8]),
            "admin": bool(r[9]),
        }
        for r in rows
    ]


def _recent_queries(limit: int = 100) -> list[dict[str, Any]]:
    conn = _conn()
    rows = conn.execute(
        """
        SELECT timestamp, endpoint, query_preview, company, filing_type, ip
        FROM query_log ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [
        {
            "timestamp": r[0],
            "endpoint": r[1],
            "query_preview": r[2],
            "company": r[3],
            "filing_type": r[4],
            "ip": _hash_ip(r[5]),
        }
        for r in rows
    ]


# --- HTML rendering ---

_CSS = """
:root {
  --bg: #0a0a0a; --surface: #141414; --border: #262626; --text: #e5e5e5;
  --muted: #a3a3a3; --accent: #3b82f6; --green: #22c55e; --red: #ef4444;
  --code-bg: #1a1a2e;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.6; }
.container { max-width: 1200px; margin: 0 auto; padding: 2rem 1.5rem; }
h1 { font-size: 1.8rem; font-weight: 700; margin-bottom: 0.5rem; }
h2 { font-size: 1.15rem; font-weight: 600; margin: 1.5rem 0 0.75rem; color: var(--accent); }
h3 { font-size: 0.95rem; font-weight: 600; margin: 0 0 0.5rem; color: var(--text); }
p { color: var(--muted); margin-bottom: 0.5rem; font-size: 0.9rem; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.nav { margin-bottom: 1rem; font-size: 0.85rem; color: var(--muted); }
.nav a { margin-right: 0.75rem; }
.grid { display: grid; gap: 1rem; }
.grid-4 { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
.grid-2 { grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); }
.card { background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 1rem 1.25rem; }
.stat-value { font-size: 1.6rem; font-weight: 700; color: var(--text); }
.stat-label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.05em; }
table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
th, td { text-align: left; padding: 0.45rem 0.5rem; border-bottom: 1px solid var(--border);
  vertical-align: top; }
th { color: var(--muted); font-weight: 500; font-size: 0.72rem; text-transform: uppercase;
  letter-spacing: 0.05em; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.mono { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; font-size: 0.78rem; }
.badge { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 4px;
  font-size: 0.7rem; font-weight: 500; }
.b-2xx { background: #14532d; color: #bbf7d0; }
.b-3xx { background: #334155; color: #cbd5e1; }
.b-4xx { background: #78350f; color: #fde68a; }
.b-5xx { background: #7f1d1d; color: #fecaca; }
.b-paid { background: #1e3a8a; color: #bfdbfe; }
.b-admin { background: #4a044e; color: #f5d0fe; }
.bar { height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }
.bar > span { display: block; height: 100%; background: var(--accent); }
.sparkline { display: flex; align-items: flex-end; gap: 2px; height: 60px; }
.sparkline > div { flex: 1; background: var(--accent); opacity: 0.8; min-height: 1px;
  border-radius: 2px 2px 0 0; }
.truncate { max-width: 400px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
"""


def _escape(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _status_badge(code: int) -> str:
    if 200 <= code < 300:
        cls = "b-2xx"
    elif 300 <= code < 400:
        cls = "b-3xx"
    elif 400 <= code < 500:
        cls = "b-4xx"
    else:
        cls = "b-5xx"
    return f'<span class="badge {cls}">{code}</span>'


def _bar_row(label: str, count: int, total: int) -> str:
    pct = (count / total * 100) if total else 0
    return (
        '<tr><td class="mono truncate">' + _escape(label) + "</td>"
        + f'<td class="num">{count:,}</td>'
        + f'<td style="width: 40%;"><div class="bar"><span style="width: {pct:.1f}%"></span></div></td></tr>'
    )


def _render_overview(stats: dict[str, Any]) -> str:
    t = stats["totals"]
    ep = stats["by_endpoint_7d"]
    sc = stats["status_classes_7d"]
    x = stats["x402"]
    pct = stats["percentiles_ms_7d"]
    timeline = stats["timeline_24h"]

    max_tl = max((n for _, n in timeline), default=1)
    spark_bars = "".join(
        f'<div style="height: {(n / max_tl * 100):.1f}%" title="{_escape(hr)}: {n}"></div>'
        for hr, n in timeline
    ) or '<div style="opacity:0.3">no data</div>'

    total_ep = sum(ep.values()) or 1
    ep_rows = "".join(
        _bar_row(k, v, total_ep)
        for k, v in sorted(ep.items(), key=lambda kv: -kv[1])
        if v > 0
    ) or '<tr><td colspan="3" class="mono" style="color:var(--muted)">no requests yet</td></tr>'

    total_sc = sum(sc.values()) or 1
    sc_rows = "".join(_bar_row(k, v, total_sc) for k, v in sc.items() if v > 0)

    ref_rows = "".join(
        f'<tr><td class="mono truncate">{_escape(r)}</td><td class="num">{n:,}</td></tr>'
        for r, n in stats["top_referrers_7d"]
    ) or '<tr><td colspan="2" class="mono" style="color:var(--muted)">none</td></tr>'

    ua_rows = "".join(
        f'<tr><td class="mono truncate">{_escape(u)}</td><td class="num">{n:,}</td></tr>'
        for u, n in stats["top_user_agents_7d"]
    ) or '<tr><td colspan="2" class="mono" style="color:var(--muted)">none</td></tr>'

    ip_rows = "".join(
        f'<tr><td class="mono">{_escape(i)}</td><td class="num">{n:,}</td></tr>'
        for i, n in stats["top_ips_7d"]
    ) or '<tr><td colspan="2" class="mono" style="color:var(--muted)">none</td></tr>'

    q_rows = "".join(
        f'<tr><td class="mono truncate">{_escape(q)}</td><td class="num">{n:,}</td></tr>'
        for q, n in stats["top_queries_7d"]
    ) or '<tr><td colspan="2" class="mono" style="color:var(--muted)">no queries logged</td></tr>'

    co_rows = "".join(
        f'<tr><td class="mono truncate">{_escape(c)}</td><td class="num">{n:,}</td></tr>'
        for c, n in stats["top_companies_7d"]
    ) or '<tr><td colspan="2" class="mono" style="color:var(--muted)">none</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin — EDGAR RAG</title>
<style>{_CSS}</style></head><body>
<div class="container">
<div class="nav">
  <a href="/">Home</a>
  <a href="/admin">Overview</a>
  <a href="/admin/traffic">Traffic</a>
  <a href="/admin/queries">Queries</a>
  <a href="/admin/api/stats">JSON</a>
</div>
<h1>Admin Dashboard</h1>
<p>Live audit data from <span class="mono">data/audit.db</span>. All times UTC.</p>

<h2>Request Volume</h2>
<div class="grid grid-4">
  <div class="card"><div class="stat-value">{t['24h']:,}</div><div class="stat-label">Last 24h</div></div>
  <div class="card"><div class="stat-value">{t['7d']:,}</div><div class="stat-label">Last 7 days</div></div>
  <div class="card"><div class="stat-value">{t['30d']:,}</div><div class="stat-label">Last 30 days</div></div>
  <div class="card"><div class="stat-value">{t['all']:,}</div><div class="stat-label">All time</div></div>
</div>

<h2>Traffic — Last 24h (per hour)</h2>
<div class="card">
  <div class="sparkline">{spark_bars}</div>
</div>

<h2>Response Time (7d) & x402 Payments</h2>
<div class="grid grid-4">
  <div class="card"><div class="stat-value">{pct['p50']} ms</div><div class="stat-label">p50</div></div>
  <div class="card"><div class="stat-value">{pct['p95']} ms</div><div class="stat-label">p95</div></div>
  <div class="card"><div class="stat-value">{pct['p99']} ms</div><div class="stat-label">p99</div></div>
  <div class="card"><div class="stat-value">{x['paid_all']:,}</div><div class="stat-label">Paid req (all time)</div>
    <p style="margin-top:0.3rem;font-size:0.75rem;">7d: {x['paid_7d']} · vol ~${x['volume_usd_all']}</p></div>
</div>

<div class="grid grid-2" style="margin-top:1rem;">
  <div class="card">
    <h3>By Endpoint (7d)</h3>
    <table>{ep_rows}</table>
  </div>
  <div class="card">
    <h3>By Status Code Class (7d)</h3>
    <table>{sc_rows}</table>
  </div>
</div>

<div class="grid grid-2" style="margin-top:1rem;">
  <div class="card">
    <h3>Top Referrers (7d)</h3>
    <table>{ref_rows}</table>
  </div>
  <div class="card">
    <h3>Top User Agents (7d)</h3>
    <table>{ua_rows}</table>
  </div>
</div>

<div class="grid grid-2" style="margin-top:1rem;">
  <div class="card">
    <h3>Top IPs (7d) — masked</h3>
    <table>{ip_rows}</table>
  </div>
  <div class="card">
    <h3>Top Query Patterns (7d, 50 char preview)</h3>
    <table>{q_rows}</table>
  </div>
</div>

<div class="grid grid-2" style="margin-top:1rem;">
  <div class="card">
    <h3>Top Companies Queried (7d)</h3>
    <table>{co_rows}</table>
  </div>
  <div class="card">
    <h3>Privacy</h3>
    <p>Query preview: first 50 chars only. IPs: first 2 octets only. UAs: truncated at 100 chars. Payment headers: never stored.</p>
  </div>
</div>

</div></body></html>"""


def _render_traffic(stats: dict[str, Any], recent: list[dict[str, Any]]) -> str:
    paid_badge = '<span class="badge b-paid">paid</span>'
    admin_badge = '<span class="badge b-admin">admin</span>'

    def _flags(r: dict[str, Any]) -> str:
        out = ""
        if r["x402_paid"]:
            out += paid_badge
        if r["admin"]:
            out += admin_badge
        return out

    rows = "".join(
        "<tr>"
        + f'<td class="mono">{_escape(r["timestamp"][:19])}</td>'
        + f'<td class="mono">{_escape(r["method"])}</td>'
        + f'<td class="mono truncate">{_escape(r["path"])}</td>'
        + f"<td>{_status_badge(r['status_code'])}</td>"
        + f'<td class="num mono">{r["duration_ms"]}</td>'
        + f'<td class="mono">{_escape(r["ip"])}</td>'
        + f'<td class="mono truncate" style="max-width:200px;">{_escape(r["user_agent"])}</td>'
        + f"<td>{_flags(r)}</td>"
        + "</tr>"
        for r in recent
    ) or '<tr><td colspan="8" class="mono" style="color:var(--muted)">no requests</td></tr>'

    t = stats["totals"]
    pct = stats["percentiles_ms_7d"]
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Traffic — Admin EDGAR RAG</title>
<style>{_CSS}</style></head><body>
<div class="container">
<div class="nav">
  <a href="/">Home</a>
  <a href="/admin">Overview</a>
  <a href="/admin/traffic">Traffic</a>
  <a href="/admin/queries">Queries</a>
  <a href="/admin/api/stats">JSON</a>
</div>
<h1>Traffic Detail</h1>
<p>Most recent 100 requests. All times UTC. IPs masked.</p>

<div class="grid grid-4">
  <div class="card"><div class="stat-value">{t['24h']:,}</div><div class="stat-label">24h requests</div></div>
  <div class="card"><div class="stat-value">{t['7d']:,}</div><div class="stat-label">7d requests</div></div>
  <div class="card"><div class="stat-value">{pct['p95']} ms</div><div class="stat-label">p95 latency (7d)</div></div>
  <div class="card"><div class="stat-value">{pct['p99']} ms</div><div class="stat-label">p99 latency (7d)</div></div>
</div>

<h2>Recent Requests</h2>
<div class="card" style="overflow-x:auto;">
<table>
<thead><tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th><th>ms</th>
<th>IP</th><th>User Agent</th><th>Flags</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</div>
</div></body></html>"""


def _render_queries(stats: dict[str, Any], recent: list[dict[str, Any]]) -> str:
    rows = "".join(
        f"<tr>"
        f'<td class="mono">{_escape(r["timestamp"][:19])}</td>'
        f'<td class="mono">{_escape(r["endpoint"])}</td>'
        f'<td class="mono truncate">{_escape(r["query_preview"])}</td>'
        f'<td class="mono">{_escape(r["company"])}</td>'
        f'<td class="mono">{_escape(r["filing_type"])}</td>'
        f'<td class="mono">{_escape(r["ip"])}</td>'
        f"</tr>"
        for r in recent
    ) or '<tr><td colspan="6" class="mono" style="color:var(--muted)">no queries logged</td></tr>'

    top_q = "".join(
        f'<tr><td class="mono truncate">{_escape(q)}</td><td class="num">{n:,}</td></tr>'
        for q, n in stats["top_queries_7d"]
    ) or '<tr><td colspan="2" class="mono" style="color:var(--muted)">none</td></tr>'
    top_co = "".join(
        f'<tr><td class="mono truncate">{_escape(c)}</td><td class="num">{n:,}</td></tr>'
        for c, n in stats["top_companies_7d"]
    ) or '<tr><td colspan="2" class="mono" style="color:var(--muted)">none</td></tr>'
    top_ft = "".join(
        f'<tr><td class="mono">{_escape(f)}</td><td class="num">{n:,}</td></tr>'
        for f, n in stats["top_filing_types_7d"]
    ) or '<tr><td colspan="2" class="mono" style="color:var(--muted)">none</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Queries — Admin EDGAR RAG</title>
<style>{_CSS}</style></head><body>
<div class="container">
<div class="nav">
  <a href="/">Home</a>
  <a href="/admin">Overview</a>
  <a href="/admin/traffic">Traffic</a>
  <a href="/admin/queries">Queries</a>
  <a href="/admin/api/stats">JSON</a>
</div>
<h1>Query Log</h1>
<p>What agents are asking. Query text truncated at 50 chars per privacy rules.</p>

<div class="grid grid-2">
  <div class="card"><h3>Top Query Patterns (7d)</h3><table>{top_q}</table></div>
  <div class="card"><h3>Top Companies Queried (7d)</h3><table>{top_co}</table></div>
</div>
<div class="grid grid-2" style="margin-top:1rem;">
  <div class="card"><h3>Top Filing Types (7d)</h3><table>{top_ft}</table></div>
  <div class="card"><h3>Privacy</h3>
    <p>Query content is stored as 50-char preview only. Full query text never retained.
    IPs masked to first two octets for display.</p></div>
</div>

<h2>Recent Queries</h2>
<div class="card" style="overflow-x:auto;">
<table>
<thead><tr><th>Time</th><th>Endpoint</th><th>Preview (50ch)</th><th>Company</th>
<th>Type</th><th>IP</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</div>
</div></body></html>"""


@router.get("/admin")
async def admin_overview(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> HTMLResponse:
    _require_admin(x_admin_key)
    stats = _gather_stats()
    return HTMLResponse(content=_render_overview(stats))


@router.get("/admin/traffic")
async def admin_traffic(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> HTMLResponse:
    _require_admin(x_admin_key)
    stats = _gather_stats()
    recent = _recent_requests(100)
    return HTMLResponse(content=_render_traffic(stats, recent))


@router.get("/admin/queries")
async def admin_queries(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> HTMLResponse:
    _require_admin(x_admin_key)
    stats = _gather_stats()
    recent = _recent_queries(100)
    return HTMLResponse(content=_render_queries(stats, recent))


@router.get("/admin/api/stats")
async def admin_api_stats(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> JSONResponse:
    _require_admin(x_admin_key)
    return JSONResponse(content=_gather_stats())
