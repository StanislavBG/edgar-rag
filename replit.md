# EDGAR RAG — Replit Deployment Guide

## Quick Start
1. Git-Sync from `StanislavBG/edgar-rag` (main branch)
2. Set Replit Secrets:
   - `WALLET_ADDRESS` = `0x038326AF0793eC0dE8b1f18d0f4f0184FA965e8C`
   - `X402_FACILITATOR_URL` = `https://x402.org/facilitator`
   - `UPLOAD_SECRET` = your generated secret
   - `ALLOWED_HOSTS` = `*` (or your deploy URL)
3. Deploy (Cloud Run)

## How It Works
- **Language:** Python 3.11 (set in `.replit` modules)
- **Build step:** `sh install.sh` (installs deps + downloads ONNX model)
- **Run step:** `python -m uvicorn src.server:app --host 0.0.0.0 --port 8080 --timeout-keep-alive 120`
- **Port:** 8080 (Cloud Run requirement)
- **Dependencies:** defined in `deps.txt` (runtime) and installed by `install.sh`

## The Dependency Dance
Replit's Cloud Run deploy runs `uv sync` on `pyproject.toml` before our build step.
To survive uv's resolver bugs:
- `pyproject.toml` has `dependencies = []` — uv has nothing to resolve
- Real deps are in `deps.txt` — installed via `pip install -r deps.txt` in `install.sh`
- `install.sh` swallows pip errors and always exits 0
- No `uv.lock` (different uv versions between local and Replit)
- No `requirements.txt` (Replit auto-runs pip on that name, fails)

## Model Download
The ONNX embedding model (127MB) is downloaded by `install.sh` from
HuggingFace's public CDN directly via curl — no `huggingface-hub` Python
package needed. Files land in `models/bge-small-en-v1.5/`.
`src/query.py` loads the model from this local path on startup.

## Environment Variables
Set in Replit Secrets (not in shell, not in code):

| Variable | Purpose |
|----------|---------|
| `WALLET_ADDRESS` | x402 payment recipient (Coinbase Base L2 wallet) |
| `X402_FACILITATOR_URL` | Coinbase facilitator endpoint |
| `UPLOAD_SECRET` | Bearer token for `/upload-vectors` and `/admin/*` |
| `ALLOWED_HOSTS` | TrustedHost middleware whitelist (use `*` for any) |
| `GITHUB_TOKEN` | Read token for the private repo — lets `install.sh` pull the vector bundle from the `v0.1.0-data` Release at build time. Without it, the server starts with 0 filings. |

## Data Flow
1. Vectors are **not in Git** (too big, 48MB compressed)
2. Local machine runs `python src/ingest.py` → populates `data/vectors/`
3. Local machine tars and uploads the bundle to the `v0.1.0-data` GitHub Release:
   `tar czf edgar-vectors.tar.gz -C data vectors && gh release upload v0.1.0-data edgar-vectors.tar.gz --clobber`
4. On Cloud Run **build**, `install.sh` downloads the bundle (auth via `GITHUB_TOKEN`)
   and extracts it to `data/vectors/` — baked into the image, so it survives cold
   starts and redeploys. (Cloud Run's runtime disk is ephemeral; `/upload-vectors`
   pushes there and is lost on restart — fine for hot-patching, not for durability.)
5. Server reads vectors at runtime for query search

> If `accession_number` ever looks like a CIK again, re-run `python src/migrate_accession.py`
> (it re-derives accession from `source_url`) before re-tarring and uploading the Release.

## Troubleshooting

### "uv sync failed — lancedb not available for Linux"
- Check `pyproject.toml` has `dependencies = []` (NOT a populated list)
- Run: `git reset --hard origin/main && rm -rf .pythonlibs uv.lock` then redeploy

### "pip conflict checker crashed during install.sh"
- Known pip bug with corrupted package metadata in `.pythonlibs`
- Fix: `rm -rf .pythonlibs` in Replit shell, then redeploy
- `install.sh` has `|| true` after pip calls so this shouldn't fail the build

### "libstdc++.so.6 not found"
- Ensure `replit.nix` includes `pkgs.python311`
- This provides the C++ runtime numpy needs

### "Server starts but returns 404"
- Ensure port 8080 is set in `.replit` and in uvicorn command
- Check Replit's port mapping: localPort 8080 → externalPort 80

### "Module not found: onnxruntime"
- `install.sh` might have silently failed — check build logs
- Run manually in shell: `pip install -r deps.txt`

## Endpoints
- `GET /` — landing page (HTML for browsers, JSON for `Accept: application/json`)
- `GET /health` — status check
- `GET /companies` — company list
- `GET /company/{slug}` — company profile (AAPL, MSFT, etc.)
- `GET /data` — data catalog
- `GET /api` — JSON API docs
- `POST /v1/query` — semantic search (x402-gated, $0.01)
- `POST /v1/query/stream` — SSE streaming version
- `GET /v1/query/cache-stats` — cache hit/miss stats (free)
- `POST /mcp` — MCP JSON-RPC endpoint
- `POST /upload-vectors` — receive vector uploads (bearer-gated)
- `POST /upload-vectors-chunk` — chunked upload for >32MB (bearer-gated)
- `GET /admin`, `/admin/traffic`, `/admin/queries` — audit dashboards (X-Admin-Key gated)

## Source Code
https://github.com/StanislavBG/edgar-rag (private)
