# MediCheck MX v29 — configuración real

## 1. Gemini
No pongas la API key dentro de Android. En el servidor configura:

GEMINI_API_KEY=TU_NUEVA_CLAVE
GEMINI_MODEL=gemini-3.8-flash

Después reinicia el backend y abre `/gemini/status`. Debe indicar `configured: true`.

## 2. COFEPRIS
El backend descubre los enlaces desde la página oficial y descarga los listados al iniciar `/sync`.

Por defecto:
- Registros otorgados: 2026
- Revocados/cancelados: 2025 (son los años que actualmente aparecen publicados para esas categorías)

La página oficial también ofrece el Visor de Registros de Medicamentos para consultas por número, denominación, principio activo, fabricante, etc.

## 3. Flujo
`POST /analyze-and-verify` hace:
1. fotografía → Gemini
2. extracción estructurada
3. sincronización COFEPRIS si la base está vacía
4. comparación
5. resultado MATCH / REVIEW / NO_MATCH

Un registro encontrado no demuestra por sí solo que el envase físico sea auténtico.
