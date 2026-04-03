#!/bin/sh
rm -rf .pythonlibs
python3 -m pip install --target=.pythonlibs --no-cache-dir -r requirements.txt 2>&1 | grep -v TypeError
exit 0
