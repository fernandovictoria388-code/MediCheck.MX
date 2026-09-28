#!/bin/sh
set -e
cd "$(dirname "$0")"
printf 'Pega tu NUEVA Gemini API key y pulsa Enter (no se mostrará):\n'
stty -echo
IFS= read -r KEY
stty echo
printf '\n'
printf 'GEMINI_API_KEY=%s\nGEMINI_MODEL=gemini-3.8-flash\nCOFEPRIS_YEARS=2026\nAUTO_SYNC=1\n' "$KEY" > .env
chmod 600 .env
echo 'Clave guardada en backend/.env (solo servidor).'
