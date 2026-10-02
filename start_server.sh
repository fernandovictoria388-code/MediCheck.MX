#!/bin/sh
set -e
cd "$(dirname "$0")"
if [ ! -f cofepris.sqlite3 ]; then
  if [ -f cofepris.sqlite3.gz ]; then
    echo "Descomprimiendo base COFEPRIS..."
    gzip -dc cofepris.sqlite3.gz > cofepris.sqlite3
  else
    echo "ERROR: falta cofepris.sqlite3.gz"
    exit 1
  fi
fi
exec python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
