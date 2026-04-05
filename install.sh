#!/bin/sh
# Install dependencies — survive Replit's auto-pyproject.toml + pip conflict bugs
set +e

# Replace any Replit-generated pyproject.toml with our known-good minimal one
# (Replit's auto-generated pyproject.toml has deprecated tool.uv.dev-dependencies
# which triggers uv sync and crashes on pip's conflict checker bug)
rm -f uv.lock
if [ -f pyproject.toml.replit ]; then
    cp pyproject.toml.replit pyproject.toml
fi

echo "Installing Python dependencies from deps.txt..."
# Swallow all pip output and exit codes — pip's conflict checker crashes on
# pre-installed corrupted metadata (not our fault). We verify imports after.
pip install --no-cache-dir -r deps.txt >/dev/null 2>&1 || true

# Verify critical imports; retry if pip quit early
if ! python3 -c "import fastapi, uvicorn, lancedb, onnxruntime, tokenizers" 2>/dev/null; then
    echo "Retrying install via python3 -m pip..."
    python3 -m pip install --no-cache-dir -r deps.txt >/dev/null 2>&1 || true
fi

# Verify again and report
if python3 -c "import fastapi, uvicorn, lancedb, onnxruntime, tokenizers" 2>/dev/null; then
    echo "Dependencies installed successfully"
else
    echo "WARNING: Some dependencies may be missing — server may fail to start"
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
        echo "Model bundle download failed — HuggingFace Hub fallback on first query"
    fi
    rm -f models.tar.gz
fi

# Always succeed
exit 0
