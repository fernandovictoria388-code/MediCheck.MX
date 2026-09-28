# MediCheck MX backend

## 1. Configurar Gemini
No pongas la API key en Android. En el proveedor del servidor define `GEMINI_API_KEY`.

Modelo por defecto: `gemini-3.8-flash`.

## 2. COFEPRIS
El servidor descubre los enlaces desde la página oficial:
https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos

`POST /sync` descarga los documentos seleccionados y crea `data/cofepris.sqlite3` y `data/sync_manifest.json`.

Variables:
- `COFEPRIS_YEARS=2026` (puedes usar `2025,2026`)
- `AUTO_SYNC=1`

## 3. Probar
- GET `/health`
- GET `/gemini/status`
- GET `/sources`
- POST `/sync`
- POST `/analyze-and-verify`

## 4. Android
Configura en la app la URL HTTPS pública del backend, por ejemplo `https://tu-servidor.example.com/`. No uses `localhost` en la APK instalada en el teléfono.
