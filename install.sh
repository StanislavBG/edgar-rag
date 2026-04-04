#!/bin/sh
# Install dependencies — bypass Replit auto-pyproject.toml issues
set +e

# Remove any Replit-generated pyproject.toml that would trigger uv sync
rm -f pyproject.toml uv.lock

echo "Installing Python dependencies from deps.txt..."
pip install --no-cache-dir -r deps.txt 2>&1 | grep -v "TypeError\|conflict" || true

# Verify critical imports; retry if pip quit early on conflict check bug
if ! python3 -c "import fastapi, uvicorn, lancedb, onnxruntime, tokenizers" 2>/dev/null; then
    echo "Retrying install via python3 -m pip..."
    python3 -m pip install --no-cache-dir -r deps.txt 2>&1 | grep -v "TypeError\|conflict" || true
fi

# Download pre-built ONNX embedding model bundle from GitHub Releases
MODEL_URL="https://github.com/StanislavBG/edgar-rag/releases/download/v0.1.0-model/bge-small-en-v1.5.tar.gz"
MODEL_ONNX="models/bge-small-en-v1.5/onnx/model.onnx"

if [ -f "$MODEL_ONNX" ]; then
    echo "Model bundle already present — skipping download"
else
    echo "Downloading embedding model bundle..."
    mkdir -p models
    if curl -fsSL -o models.tar.gz "$MODEL_URL" 2>/dev/null && tar xzf models.tar.gz -C models/ 2>/dev/null; then
        echo "Model bundle extracted"
    else
        echo "Model bundle download failed — will download from HuggingFace Hub on first query"
    fi
    rm -f models.tar.gz
fi

# Always succeed
exit 0
