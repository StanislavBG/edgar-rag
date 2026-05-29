#!/bin/sh
# Install deps + download ONNX model. Always exit 0.
set +e

rm -f uv.lock

echo "Installing Python dependencies..."
pip install --no-cache-dir -r deps.txt >/dev/null 2>&1 || true

if ! python3 -c "import fastapi, uvicorn, lancedb, onnxruntime, tokenizers" 2>/dev/null; then
    echo "Retrying via python3 -m pip..."
    python3 -m pip install --no-cache-dir -r deps.txt >/dev/null 2>&1 || true
fi

if python3 -c "import fastapi, uvicorn, lancedb, onnxruntime, tokenizers" 2>/dev/null; then
    echo "Dependencies installed successfully"
else
    echo "WARNING: imports failed"
fi

# Download ONNX embedding model bundle
# Try GitHub Release first (private — needs auth), fall back to HuggingFace direct CDN
MODEL_DIR="models/bge-small-en-v1.5"
MODEL_ONNX="$MODEL_DIR/onnx/model.onnx"
MODEL_TOKENIZER="$MODEL_DIR/tokenizer.json"

if [ -f "$MODEL_ONNX" ] && [ -f "$MODEL_TOKENIZER" ]; then
    echo "Model bundle already present — skipping"
else
    echo "Downloading ONNX model from HuggingFace CDN..."
    mkdir -p "$MODEL_DIR/onnx"
    # Direct HuggingFace CDN URLs (no auth needed, public model)
    HF_BASE="https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main"
    curl -fsSL -o "$MODEL_ONNX" "$HF_BASE/onnx/model.onnx" 2>/dev/null || echo "model.onnx download failed"
    curl -fsSL -o "$MODEL_TOKENIZER" "$HF_BASE/tokenizer.json" 2>/dev/null || echo "tokenizer.json download failed"

    if [ -f "$MODEL_ONNX" ] && [ -f "$MODEL_TOKENIZER" ]; then
        echo "Model downloaded successfully"
    else
        echo "WARNING: Model download failed. Server will fail to load embeddings."
    fi
fi

# Download vector bundle from the private GitHub Release (baked into the build
# image so it survives Cloud Run cold starts/redeploys, unlike /upload-vectors
# to the ephemeral runtime disk). Requires GITHUB_TOKEN in Replit Secrets.
REPO="StanislavBG/edgar-rag"
DATA_TAG="v0.1.0-data"
ASSET_NAME="edgar-vectors.tar.gz"

if [ -d "data/vectors" ] && [ -n "$(ls -A data/vectors 2>/dev/null)" ]; then
    echo "Vectors already present — skipping download"
elif [ -z "$GITHUB_TOKEN" ]; then
    echo "WARNING: GITHUB_TOKEN not set — skipping vector download. Server will start with 0 filings."
else
    echo "Downloading vector bundle from GitHub Release $DATA_TAG..."
    ASSET_URL=$(curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" \
        "https://api.github.com/repos/$REPO/releases/tags/$DATA_TAG" 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((a['url'] for a in d.get('assets',[]) if a['name']=='$ASSET_NAME'),''))" 2>/dev/null)
    if [ -n "$ASSET_URL" ]; then
        mkdir -p data
        curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/octet-stream" \
            -o data/vectors.tar.gz "$ASSET_URL" 2>/dev/null || echo "vector download failed"
        if [ -f "data/vectors.tar.gz" ]; then
            tar xzf data/vectors.tar.gz -C data && rm -f data/vectors.tar.gz
            echo "Vectors extracted to data/vectors"
        fi
    else
        echo "WARNING: could not resolve $ASSET_NAME in release $DATA_TAG"
    fi
fi

exit 0
