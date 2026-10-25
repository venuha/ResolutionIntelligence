#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Create the environment first: python3.12 -m venv .venv"
  echo "Then install: .venv/bin/pip install -r requirements.lock"
  exit 1
fi
exec .venv/bin/python -m uvicorn resolution.main:app --host 127.0.0.1 --port 8103
