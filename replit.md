# EDGAR RAG — Replit Setup
Runtime: Python 3.11+
Install: pip install -e .
Run: python -m uvicorn src.server:app --host 0.0.0.0 --port 3000
Secrets needed: WALLET_ADDRESS, X402_FACILITATOR_URL, UPLOAD_SECRET
Vector data on persistent disk at data/vectors/ (not in Git).
Set ALLOWED_HOSTS secret to your Replit deployment URL if you want host validation.
