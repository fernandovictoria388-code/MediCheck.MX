# Despliegue MediCheck MX V49

## 1. Render
El servicio V49 usa Docker y arranca con `start_server.sh`.

Variables opcionales:
- `AUTO_SYNC=1`
- `AUTO_SYNC_HOURS=24`
- `COFEPRIS_YEARS=2026`
- `COFEPRIS_STATUS_YEARS=2022,2023,2024,2025,2026`
- `SYNC_TOKEN` para proteger la sincronización manual.

**No se requiere `GEMINI_API_KEY`.**

## 2. Verificación
Después del despliegue abre `/health` y comprueba:
- `service`: `medicheck-v49`
- `gemini`: `false`
- `recognition_engine`: `motor_reconocimiento_v49`
- `records`: número de registros cargados.

## 3. Flujo
Android realiza el escaneo de código y OCR. El servidor compara esos datos con la base COFEPRIS mediante `motor_reconocimiento.py`.

La coincidencia no equivale por sí sola a autenticidad física del envase.
