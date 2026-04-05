# EDGAR RAG Microservice

## Stack
- **Local dev:** Python 3.11-3.13, FastAPI, LanceDB, sentence-transformers (bge-small-en-v1.5)
- **Replit prod:** Python 3.11, FastAPI, LanceDB, onnxruntime (no torch)
- **Payments:** x402 on Base L2 (USDC)

## Commands (local)
- `pip install -r deps.txt -r deps-dev.txt` — install runtime + dev deps
- `python -m uvicorn src.server:app --reload` — dev server
- `python src/ingest.py` — run SEC EDGAR ingestion
- `python src/upload.py` — upload vectors to Replit
- `python src/tracker.py status` — ingestion coverage matrix
- `ruff check src/` — lint
- `bandit -r src/` — security scan

## Replit Deployment Rules (LEARNED THE HARD WAY — DO NOT BREAK)

### Files that control deployment:
- `.replit` — run/deploy config, ports, workflows (Replit agent configured, don't touch)
- `replit.nix` — just `pkgs.python311`
- `install.sh` — dependency installer, always exits 0
- `deps.txt` — runtime dep pins (exact versions)
- `deps-dev.txt` — local dev deps (torch, sentence-transformers, etc.)
- `pyproject.toml` — **empty dependencies list** + tool configs only
- `replit.md` — deployment docs

### DO NOT (will break deployment):
- **Add dependencies to `pyproject.toml` `[project.dependencies]`** — Replit's uv resolver rejects valid lancedb wheels. Keep `dependencies = []`. `install.sh` does the real install via pip + deps.txt.
- **Create `requirements.txt`** — Replit auto-runs pip on it and crashes on the conflict-checker bug
- **Create `uv.lock` (commit it)** — Replit's uv is older and doesn't understand revision 3 format
- **Add `[tool.uv]` section** — deprecated `tool.uv.dev-dependencies` fails Replit validation
- **Add `[project.optional-dependencies]` (e.g. `[dev]`)** — uv installs them anyway, pulling torch (2GB)
- **Remove the `requires-python = ">=3.11,<3.14"` cap** — HF packages have no 3.14 wheels
- **Add `huggingface-hub` to deps.txt** — no Python 3.14 wheels, blocks uv. Use curl to HuggingFace CDN instead.
- **Add torch/sentence-transformers to Replit deps** — 2GB+, exceeds disk quota
- **Change port from 8080** — Cloud Run requires it
- **Modify `.replit` workflows section** — Replit agent set this up, works
- **Let pip errors fail install.sh** — use `>/dev/null 2>&1 || true` pattern, always exit 0
- **Commit `models/` directory** — 127MB of binaries, download at build time instead

### How deps work:
- **Runtime** (what server needs): `deps.txt` — exact pinned versions, installed by `install.sh`
- **Local dev** (editing, testing): `deps-dev.txt` — includes torch, sentence-transformers, edgartools, ruff
- **pyproject.toml dependencies = []** intentionally — keeps uv out of the way
- **pyproject.toml `[tool.*]`** sections OK — ruff, bandit, pytest configs

### Resolving Replit deploy issues:
1. Check `pyproject.toml dependencies == []`
2. Run in Replit shell: `git fetch origin && git reset --hard origin/main && rm -rf .pythonlibs uv.lock`
3. Verify: `grep -c "^  \"" pyproject.toml` should be 0 (no dep entries)
4. Redeploy

## Security Rules (MANDATORY)
- Never hardcode secrets — use os.environ only
- Never use eval(), exec(), pickle, or subprocess with shell=True
- Never log query content, wallet addresses, or payment headers
- Never serve results without verified x402 payment (fail closed)
- All user inputs go through Pydantic models with extra="forbid"
- All string fields must have max_length constraints
- No string interpolation in LanceDB where clauses
- /upload-vectors requires Bearer token

## Conventions
- One file per concern: server.py, query.py, mcp.py, ingest.py, upload.py, db.py, tracker.py, audit.py, company.py, landing.py, errors.py
- Shared constants in db.py: DATA_DIR, VECTOR_DIM, TABLE_NAME, MODEL_NAME
- Type hints on all functions
- No docstrings unless logic is non-obvious
- No abstractions until needed twice
- data/ is in .gitignore — never commit vector data
- models/ is in .gitignore — downloaded at build time
- query.py: local dev uses sentence-transformers (auto-detect), Replit uses ONNX runtime from local model bundle

## Admin Dashboard
Request audit log + admin pages (all require `X-Admin-Key: $UPLOAD_SECRET` header):
- `GET /admin` — overview dashboard (traffic, endpoints, x402 payments, latency)
- `GET /admin/traffic` — last 100 requests with masked IPs
- `GET /admin/queries` — query log (50-char preview, company, filing type)
- `GET /admin/api/stats` — JSON stats for external tooling

Audit log in `data/audit.db` (SQLite, gitignored). Middleware in `src/audit.py`
logs every request except /health, /favicon.ico, /robots.txt, /static/*.
Privacy: query previews 50 chars max, payment headers never stored,
IPs masked to a.b.*.* on display.

## MCP Tools
- `search_filings` (paid $0.01): semantic search over indexed filings
- `get_filing` (free): retrieve all passages by accession number
- `list_companies` (free): list indexed companies with stats
- `get_data_catalog` (free): full data inventory
