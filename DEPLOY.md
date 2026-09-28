# Deploy v33

1. Sube el contenido de esta carpeta al repositorio de GitHub que usa Render.
2. En Render conserva `GEMINI_API_KEY` con tu clave nueva. No la pongas en GitHub ni APK.
3. Define:
   - `GEMINI_MODEL=gemini-3.8-flash`
   - `COFEPRIS_LIVE_SEARCH=1`
   - `AUTO_SYNC=0`
4. Haz un nuevo deploy.
5. Comprueba `/health`. Debe mostrar `cofepris_live_search: true`.
6. La aplicación puede verificar medicamentos aunque la sincronización masiva de PDFs de gob.mx esté temporalmente bloqueada.

La sincronización masiva sigue disponible con `POST /sync`; no se usa automáticamente para no retrasar el arranque cuando el índice documental oficial está inaccesible.
