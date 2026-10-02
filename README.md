# MediCheck MX — servidor v49.5

API FastAPI para comparar OCR/campos leídos en Android con el conjunto COFEPRIS incluido. Gemini no forma parte del flujo.

## Despliegue en Render

1. Sustituye los archivos del servicio por el contenido de este ZIP (o súbelo a un repositorio Git y conecta ese repositorio a Render).
2. Conserva el entorno Docker y la ruta de salud `/health`.
3. Despliega y espera a que termine el build.
4. Comprueba `https://TU-SERVICIO.onrender.com/health`. Debe devolver `ok: true`, `service: medicheck-v49.5` y un número de `records` mayor que cero.
5. Prueba `POST /analyze-and-verify` desde `/docs` con `name: Laritol`, `concentration: 10 mg` y OCR que incluya `LARITOL LORATADINA 10 mg`. La respuesta debe contener `verification.status: VERIFIED` y `matched_record.registry: 202M2001 SSA` con la base incluida.

## Variables de entorno

- `AUTO_SYNC=1`: activa sincronización automática de documentos publicados en el portal oficial.
- `AUTO_SYNC_HOURS=24`: intervalo mínimo entre sincronizaciones exitosas.
- `COFEPRIS_YEARS=2026`: año de documentos de registros nuevos que se buscará. La base inicial incluida contiene el conjunto de 14,920 registros.
- `COFEPRIS_STATUS_YEARS=2022,2023,2024,2025,2026`: años de listas de cancelación/revocación.
- `SYNC_TOKEN`: opcional para habilitar `POST /sync`; si no se configura, la sincronización manual queda bloqueada. Si se configura, envía el valor mediante la cabecera `X-Sync-Token`.

## Comportamiento y límites

- La base inicial se restaura desde `cofepris.sqlite3.gz` cuando no existe `cofepris.sqlite3`.
- La sincronización conserva campos completos ya presentes: una extracción PDF incompleta no sustituye nombre, activo, concentración o presentación por valores vacíos.
- Las listas de revocación/cancelación se aplican como evidencia de estado al consultar un registro.
- `/health` comprueba que la base tenga registros; no se declara saludable una base vacía.
- En los planes de Render con sistema de archivos efímero, los cambios descargados durante la sincronización pueden perderse al reiniciar o redeplegar. Para conservarlos entre reinicios se requiere almacenamiento persistente compatible con el plan, o una base de datos externa.
- La coincidencia con un registro sanitario no demuestra por sí sola que el envase físico sea auténtico. La base inicial es el conjunto suministrado y no garantiza que refleje en tiempo real todos los cambios oficiales.


## V49.7 — dos fotografías
Nuevo endpoint `POST /analyze-and-verify-multi`: recibe OCR fusionado de frente + posterior/lateral y ambas imágenes como evidencia. El motor COFEPRIS sigue siendo determinista y no declara autenticidad física únicamente por coincidencia de registro.
