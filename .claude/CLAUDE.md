# EDGAR RAG Microservice

## Stack
- **Local dev:** Python 3.11, FastAPI, LanceDB, sentence-transformers (bge-small-en-v1.5)
- **Replit prod:** Python 3.11, FastAPI, LanceDB, onnxruntime (no torch)
- **Payments:** x402 on Base L2 (USDC)

## Commands (local)
- `pip install -e ".[dev]"` — install with dev deps
- `python -m uvicorn src.server:app --reload` — dev server
- `python src/ingest.py` — run SEC EDGAR ingestion
- `python src/upload.py` — upload vectors to Replit
- `python src/tracker.py status` — ingestion coverage matrix
- `ruff check src/` — lint
- `bandit -r src/` — security scan

## Replit Deployment (DO NOT BREAK)
The following files control Replit deployment. Do not modify unless you know what you're doing:
- `.replit` — deployment config (build, run, workflows, ports)
- `replit.nix` — system deps (just pkgs.python311)
- `install.sh` — dependency installer (reads deps.txt, always exits 0)
- `deps.txt` — runtime dependencies for Replit (NOT requirements.txt — Replit auto-runs pip on that name)
- `replit.md` — Replit-specific documentation

### Rules that prevent deployment breakage:
- **NEVER create `requirements.txt`** — Replit auto-detects it and runs pip, which crashes on a conflict checker bug
- **NEVER create `pyproject.toml`** — Replit auto-detects it and runs `uv sync`, which fails on lancedb
- **NEVER create `uv.lock`** — triggers Replit's uv resolver
- **NEVER change the port from 8080** — Cloud Run requires it
- **NEVER add torch or sentence-transformers to deps.txt** — exceeds Replit disk quota (2GB+). Use onnxruntime instead.
- **NEVER modify `.replit` workflows section** — Replit agent configured this, it works
- Runtime deps go in `deps.txt`, NOT requirements.txt
- `pyproject.toml` exists locally (gitignored) for local dev only

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
- One file per concern: server.py, query.py, mcp.py, ingest.py, upload.py, db.py, tracker.py
- Shared constants in db.py: DATA_DIR, VECTOR_DIM, TABLE_NAME, MODEL_NAME
- Type hints on all functions
- No docstrings unless logic is non-obvious
- No abstractions until needed twice
- data/ is in .gitignore — never commit vector data
- query.py auto-detects runtime: sentence-transformers (local) or onnxruntime (Replit)
