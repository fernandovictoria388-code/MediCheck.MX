# MediCheck MX V49 — Motor de reconocimiento sin Gemini

## Flujo
`foto -> OCR/barcode en Android -> motor V49 -> COFEPRIS SQLite -> resultado`

Gemini no participa en el flujo V49. El servidor no requiere `GEMINI_API_KEY`.

## Archivos principales
- `app.py`: API FastAPI V49.
- `motor_reconocimiento.py`: normalización, extracción OCR, similitud, reglas de evidencia y estados.
- `sync_cofepris.py`: sincronización opcional con documentos oficiales de COFEPRIS.
- `cofepris.sqlite3.gz`: base COFEPRIS suministrada/cargada en el servidor.

## Estados
- `VERIFIED`: coincidencia consistente con evidencia fuerte.
- `STRONG_MATCH`: coincidencia fuerte pero no concluyente.
- `REVIEW`: evidencia parcial o ambigua.
- `NO_MATCH`: no hay coincidencia suficiente en la base cargada.
- `REVOCADO` / `CANCELADO`: solo cuando la evidencia identifica suficientemente el registro con ese estado.
- `INSUFFICIENT_DATA`: no hay datos suficientes para comparar.

El score sirve para ordenar candidatos; no equivale a autenticidad física del envase.

## Endpoints
- `GET /health`
- `GET /sources`
- `POST /verify`
- `POST /cofepris/live`
- `POST /barcode-lookup`
- `POST /analyze-and-verify`
- `POST /sync`
- `GET /gemini/status` (informa que está deshabilitado)

`POST /gemini/analyze` devuelve 410 para evitar que el flujo dependa de Gemini.

## V49.1 ajustes de validación
- Normalización de acentos, espacios y formato.
- Comparación específica de concentraciones con equivalencia numérica/unidad.
- Comparación de fabricante por tokens para tolerar direcciones y puntuación.
- Detección de contradicción entre registro explícito y nombre.
- El código de barras no se interpreta como registro sanitario.
