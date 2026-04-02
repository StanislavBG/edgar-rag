# EDGAR RAG — Replit Deployment

## Quick Start
1. Import from GitHub: `StanislavBG/edgar-rag`
2. Set secrets in the Secrets pane (not shell):
   - `WALLET_ADDRESS` = `0x038326AF0793eC0dE8b1f18d0f4f0184FA965e8C`
   - `X402_FACILITATOR_URL` = `https://x402.org/facilitator`
   - `UPLOAD_SECRET` = (your generated secret)
3. Click Deploy

## How It Works
- **Build step:** `pip install -r requirements.txt` (runs automatically)
- **Run step:** `python3 -m uvicorn src.server:app --host 0.0.0.0 --port 8080`
- **Port:** 8080 (required by Replit Cloud Run)
- **No torch/CUDA** — uses ONNX runtime for embeddings (~50MB)

## Important
- `requirements.txt` must exist in the repo root
- No `pyproject.toml` in the repo (causes uv conflicts)
- Vector data lives on disk at `data/vectors/` (uploaded from local machine)
- The deployment environment uses `modules = ["python-3.11"]` which bundles Python + pip

## Troubleshooting
- If deployment fails with "pip not found": make sure `modules = ["python-3.11"]` is in `.replit`
- If deployment fails with lancedb errors: delete `uv.lock` and `pyproject.toml` if they exist
- If `libstdc++` error: the `replit.nix` includes `stdenv.cc.cc.lib` for this
