#!/bin/sh
set -e
cd "$(dirname "$0")"
# La verificación individual usa COFEPRIS en tiempo real mediante Gemini + Google Search.
# La sincronización masiva se deja manual porque el índice de documentos de gob.mx
# puede devolver una página mínima/403 aunque el visor siga disponible.
if [ "${AUTO_SYNC:-0}" = "1" ]; then
    python3 sync_cofepris.py || echo 'AVISO: COFEPRIS no pudo sincronizarse; el servidor seguirá iniciado.'
fi
exec python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
