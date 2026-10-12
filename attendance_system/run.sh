#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

exec "$PYTHON" app.py
