# EDGAR RAG Microservice

## Stack
Python 3.11, FastAPI, LanceDB, sentence-transformers (bge-small-en-v1.5), openlibx402

## Commands
- `pip install -e ".[dev]"` — install with dev deps
- `python -m uvicorn src.server:app --reload` — dev server
- `python src/ingest.py` — run SEC EDGAR ingestion locally
- `python src/upload.py` — upload vectors to Replit
- `pytest` — run tests
- `ruff check src/` — lint
- `bandit -r src/` — security scan
- `pip-audit` — dependency vuln scan

## Security Rules (MANDATORY)
- Never hardcode secrets — use os.environ only
- Never use eval(), exec(), pickle, or subprocess with shell=True
- Never log query content, wallet addresses, or payment headers
- Never serve results without verified x402 payment (fail closed)
- All user inputs go through Pydantic models with extra="forbid"
- Pin openlibx402 >= 2.3.0 (critical CVE in older versions)
- Pin lancedb to exact version (format compatibility between local and Replit)
- All string fields must have max_length constraints
- No string interpolation in LanceDB where clauses — use parameterized filters
- /upload-vectors requires Bearer token

## Conventions
- One file per concern: server.py, query.py, mcp.py, ingest.py, upload.py, db.py
- Type hints on all functions
- No docstrings unless logic is non-obvious
- No abstractions until needed twice
- data/ is in .gitignore — never commit vector data
