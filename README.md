# MediCheck MX v27 — Backend real Gemini + COFEPRIS

## 1) Configurar Gemini

Ejecuta:

```bash
cd backend
./configure_gemini.sh
```

Introduce tu **nueva** API key de Gemini. La clave queda en `backend/.env` y nunca se incluye en la APK.

Modelo predeterminado: `gemini-3.8-flash`.

## 2) Sincronizar COFEPRIS

```bash
python3 sync_cofepris.py
```

El sincronizador visita la página oficial de COFEPRIS, descubre los enlaces vigentes y crea/actualiza `data/cofepris.sqlite3`.

Por defecto descarga registros 2026 y estados revocados/cancelados 2025. Para ampliar años:

```bash
COFEPRIS_YEARS=2024,2025,2026 python3 sync_cofepris.py
```

No se inventan campos que no puedan extraerse con seguridad de los documentos públicos.

## 3) Arrancar

```bash
./start_server.sh
```

El servidor intentará sincronizar COFEPRIS antes de arrancar cuando `AUTO_SYNC=1`.

Endpoints:
- `GET /health`
- `GET /gemini/status`
- `GET /sources`
- `POST /sync`
- `POST /gemini/analyze`
- `POST /verify`
- `POST /analyze-and-verify`

## Flujo automático

Foto → Gemini → extracción JSON → COFEPRIS SQLite → comparación de registro/nombre/principio activo/concentración/fabricante/presentación → resultado.

Un registro encontrado no demuestra autenticidad física del envase.
