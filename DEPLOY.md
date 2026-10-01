# Despliegue — MediCheck MX v49.5

## Render

- Runtime: Docker
- Health check: `/health`
- Puerto: 8000 (lo inicia `start_server.sh`)
- No requiere `GEMINI_API_KEY`.

Después del deploy, revisa `/health`. El campo `service` debe ser `medicheck-v49.5`, `ok` debe ser `true` y `records` debe ser mayor que cero.

Para sincronización automática, configura `AUTO_SYNC=1` y `AUTO_SYNC_HOURS=24`. La sincronización puede fallar temporalmente si el portal/documento oficial no responde; el servidor conserva la base ya cargada y registra el error en los logs.

Para permitir sincronización manual, configura un secreto fuerte en `SYNC_TOKEN` y envía ese mismo valor como cabecera `X-Sync-Token` al llamar a `POST /sync`. Sin ese secreto, el endpoint devuelve 503; con un valor incorrecto, devuelve 403.

**Persistencia:** en servicios con disco efímero, las actualizaciones locales no sobreviven a un reinicio. La base inicial se recupera del archivo comprimido incluido.
