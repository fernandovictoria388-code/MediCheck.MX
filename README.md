# MediCheck MX v33 — COFEPRIS en tiempo real

Esta versión conserva el backend FastAPI y Gemini del proyecto y añade una ruta de verificación que no depende de que el índice documental de `gob.mx` pueda descargarse.

## Flujo principal

Foto → Gemini → extracción estructurada → **consulta en tiempo real con Gemini + Google Search** → evidencia de COFEPRIS → comparación de campos → resultado.

La búsqueda en tiempo real se limita en la instrucción a dominios oficiales de COFEPRIS (`registros.cofepris.gob.mx` y `gob.mx/cofepris`). Si no hay evidencia oficial suficiente, la app devuelve revisión/no coincidencia y no inventa datos.

> Un registro sanitario coincidente no demuestra por sí solo que el envase físico sea auténtico.

## Variables de Render

- `GEMINI_API_KEY`: tu clave nueva, solo en Render.
- `GEMINI_MODEL=gemini-3.8-flash`
- `COFEPRIS_LIVE_SEARCH=1`
- `AUTO_SYNC=0` recomendado: la sincronización masiva queda manual porque el índice de documentos de gob.mx puede responder con una página mínima/403 aunque el visor oficial siga disponible.

## Endpoints

- `GET /health`
- `GET /gemini/status`
- `GET /sources`
- `POST /cofepris/live` — consulta oficial individual
- `POST /verify` — verifica con consulta oficial en tiempo real y usa SQLite como respaldo
- `POST /gemini/analyze`
- `POST /analyze-and-verify` — flujo automático completo
- `POST /sync` — sincronización masiva de listados, si el índice oficial vuelve a ser accesible

## Fuente oficial

Visor de Registros de Medicamentos de COFEPRIS:
https://registros.cofepris.gob.mx/BRSDM/default.aspx


## v34 — corrección de estados COFEPRIS
- Corrige el error del sincronizador que usaba `HEAD` en lugar de los encabezados definidos.
- `/cofepris/live` diferencia `FOUND`, `NOT_FOUND`, `REVOKED`, `CANCELLED` y `SOURCE_UNAVAILABLE`.
- `SOURCE_UNAVAILABLE` nunca se presenta como medicamento sin registro.
- Se conserva el enlace oficial al Visor de Registros de Medicamentos.
