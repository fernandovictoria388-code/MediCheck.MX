#!/bin/sh
set -e
cd "$(dirname "$0")"
if [ "${AUTO_SYNC:-1}" = "1" ]; then python3 sync_cofepris.py || echo 'AVISO: COFEPRIS no pudo sincronizarse; el servidor seguirá iniciado.'; fi
exec python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
