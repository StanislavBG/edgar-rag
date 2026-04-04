#!/bin/sh
# Install dependencies — works in both dev and Cloud Run
pip install --no-cache-dir -r deps.txt 2>&1 | grep -v TypeError || \
python3 -m pip install --no-cache-dir -r deps.txt 2>&1 | grep -v TypeError

# Pre-download the ONNX embedding model so startup is fast
echo "Pre-downloading embedding model..."
python3 -c "
from huggingface_hub import hf_hub_download
for f in ['onnx/model.onnx', 'tokenizer.json', 'tokenizer_config.json', 'config.json']:
    hf_hub_download('BAAI/bge-small-en-v1.5', f, revision='main')
print('Model cached')
" 2>&1 || echo "Model pre-download skipped (will download on first request)"

exit 0
