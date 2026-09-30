# MediCheck MX v0.41 — COFEPRIS 14,920 registros

Esta versión usa la base `cofepris.sqlite3` cargada con el archivo proporcionado por el usuario.

## Base incluida
- 14,920 registros
- 10,081 VIGENTE
- 2,666 CANCELADO
- 2,173 REVOCADO
- 4,839 registros en `statuses` (cancelados + revocados)

## Verificación avanzada
`POST /cofepris/live` y `POST /verify` comparan:
- número de registro
- nombre comercial
- principio activo + concentración
- fabricante/titular
- presentación
- concentración

La búsqueda normaliza mayúsculas, acentos y espacios, y usa coincidencia por tokens para pequeñas diferencias de formato.

## Flujo automático
`POST /analyze-and-verify` mantiene el flujo:

foto → Gemini → extracción estructurada → COFEPRIS → comparación de campos → resultado

La coincidencia regulatoria no demuestra por sí sola la autenticidad física del envase.

## Render
La base SQLite viene precargada y `AUTO_SYNC=0` para evitar que un arranque reemplace la base con una sincronización incompleta.
