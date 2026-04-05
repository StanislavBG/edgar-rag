#!/bin/sh
# Install deps + download model. Always exit 0 to survive pip conflict-checker bugs.
set +e

# Remove stale uv lock
rm -f uv.lock

echo "Installing Python dependencies..."
# Swallow all pip output/errors — conflict checker crashes on pre-installed corrupted metadata
pip install --no-cache-dir -r deps.txt >/dev/null 2>&1 || true

# Verify imports; retry if pip bailed early
if ! python3 -c "import fastapi, uvicorn, lancedb, onnxruntime, tokenizers" 2>/dev/null; then
    echo "Retrying via python3 -m pip..."
    python3 -m pip install --no-cache-dir -r deps.txt >/dev/null 2>&1 || true
fi

# Report status
if python3 -c "import fastapi, uvicorn, lancedb, onnxruntime, tokenizers" 2>/dev/null; then
    echo "Dependencies installed successfully"
else
    echo "WARNING: imports failed — server may not start"
fi

# Download ONNX embedding model bundle from GitHub Releases
MODEL_URL="https://github.com/StanislavBG/edgar-rag/releases/download/v0.1.0-model/bge-small-en-v1.5.tar.gz"
MODEL_ONNX="models/bge-small-en-v1.5/onnx/model.onnx"

if [ -f "$MODEL_ONNX" ]; then
    echo "Model bundle already present — skipping"
else
    echo "Downloading embedding model..."
    mkdir -p models
    if curl -fsSL -o models.tar.gz "$MODEL_URL" 2>/dev/null && tar xzf models.tar.gz -C models/ 2>/dev/null; then
        echo "Model extracted"
    else
        echo "Model download failed — HuggingFace Hub fallback active"
    fi
    rm -f models.tar.gz
fi

exit 0
