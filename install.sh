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

exit 0
