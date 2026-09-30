# MediCheck MX v47

- Base: v46 FOTO_BUSQUEDA.
- Sincronización COFEPRIS oficial configurable con AUTO_SYNC=1.
- Refresco automático cada 24 h por defecto (AUTO_SYNC_HOURS).
- Conserva la base local anterior mientras una sincronización nueva está en curso.
- Archiva documentos descargados y SHA-256 en `data/cofepris_archive/`.
- Descubrimiento de documentos oficiales para 2022-2026; años configurables con `COFEPRIS_YEARS` y `COFEPRIS_STATUS_YEARS`.
- La fuente oficial dinámica sigue siendo el Visor COFEPRIS; los documentos publicados sirven como respaldo/actualización descargable.
