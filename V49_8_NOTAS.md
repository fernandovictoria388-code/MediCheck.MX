# MediCheck MX V49.8 - 2 fotos

Endpoint principal: POST /analyze-and-verify-multi

Flujo: frente + posterior/lateral -> OCR local fusionado -> motor V49.8 -> COFEPRIS.

Gemini no participa en este flujo.

La respuesta conserva el registro sanitario recibido desde el envase y devuelve evidencia separada de las dos fotografias.


## v49.11
El motor incorpora normalización de concentración/presentación, rescate de nombre por OCR exacto del catálogo y comparación de fabricante por tokens distintivos.
