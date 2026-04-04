#!/bin/sh
# Install dependencies — works in both dev and Cloud Run
pip install --no-cache-dir -r deps.txt 2>&1 | grep -v TypeError || \
python3 -m pip install --no-cache-dir -r deps.txt 2>&1 | grep -v TypeError

# Download pre-built ONNX embedding model bundle from GitHub Releases
# (eliminates HuggingFace Hub dependency at runtime)
MODEL_URL="https://github.com/StanislavBG/edgar-rag/releases/download/v0.1.0-model/bge-small-en-v1.5.tar.gz"
MODEL_ONNX="models/bge-small-en-v1.5/onnx/model.onnx"

if [ -f "$MODEL_ONNX" ]; then
    echo "Model bundle already present at $MODEL_ONNX — skipping download"
else
    echo "Downloading embedding model bundle from GitHub Releases..."
    mkdir -p models
    curl -fsSL -o models.tar.gz "$MODEL_URL" && \
        tar xzf models.tar.gz -C models/ && \
        rm -f models.tar.gz && \
        echo "Model bundle extracted to models/bge-small-en-v1.5/" || \
        echo "Model bundle download failed — will fall back to HuggingFace Hub at runtime"
fi

exit 0
