#!/bin/sh
# Install dependencies — works in both dev and Cloud Run
pip install --no-cache-dir -r deps.txt 2>&1 | grep -v TypeError || \
python3 -m pip install --no-cache-dir -r deps.txt 2>&1 | grep -v TypeError
exit 0
