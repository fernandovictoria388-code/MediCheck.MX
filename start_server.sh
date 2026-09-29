#!/bin/sh
set -e
cd "$(dirname "$0")"
# No ejecutar sincronizacion automatica: la base SQLite ya viene precargada.
exec python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
