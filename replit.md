# EDGAR RAG — Replit Environment

## Architecture
- **Runtime**: Python 3.11 (Replit modules = ["python-3.11"])
- **Framework**: FastAPI + Uvicorn
- **Port**: 8080 (required by Cloud Run)
- **Embeddings**: ONNX runtime (no torch/CUDA)
- **Vector store**: LanceDB on disk at `data/vectors/`

## How Dependencies Work
Packages are pre-installed into `.pythonlibs/` via `pip install --target=.pythonlibs`.
Replit's site customization automatically adds `.pythonlibs` to `sys.path` — no PYTHONPATH override needed.
Install/refresh with:
```
sh install.sh
```
Or manually:
```
pip install --target=.pythonlibs --no-cache-dir -r deps.txt
```

## Run (Development)
Workflow: `python3 -m uvicorn src.server:app --host 0.0.0.0 --port 8080`

## Deployment (Cloud Run)
The `.replit` `[deployment]` section runs:
```
python3 -m uvicorn src.server:app --host 0.0.0.0 --port 8080
```
Build step (`sh install.sh`) installs deps into `.pythonlibs/` at deploy time.

## Required Secrets
Set these in the Replit Secrets pane (not in shell):
- `WALLET_ADDRESS`
- `X402_FACILITATOR_URL`
- `UPLOAD_SECRET`
- `ALLOWED_HOSTS` (set to `*` to allow all, or your domain)

## Source Files
All application code lives in `src/`:
- `server.py` — FastAPI app, routes, middleware
- `db.py` — LanceDB access, company/filing helpers
- `query.py` — RAG query logic
- `ingest.py` — Filing ingestion pipeline
- `upload.py` — Upload endpoint helpers
- `tracker.py` — Request/usage tracking
- `mcp.py` — MCP integration

## Troubleshooting
- **Import errors at startup**: run `sh install.sh` to reinstall deps into `.pythonlibs/`
- **lancedb errors in deployment**: avoid committing `uv.lock` or having a `.python-version` file — both trigger `uv sync` which fails on lancedb
- **libstdc++ errors**: ensure `replit.nix` includes `stdenv.cc.cc.lib`
- **Port mismatch**: app must listen on 8080 for Cloud Run
