# MediCheck MX V49.2 — Motor de Reconocimiento sin Gemini

Corrección del motor V49 para OCR en una sola línea.

## Mejoras
- Detecta la marca aunque aparezca junto con dosis y principio activo: `ACTRON 400 IBUPROFENO ...`.
- Usa presencia de términos distintivos del principio activo en OCR.
- Usa evidencia del titular/marca visible, por ejemplo `BAYER`, aunque COFEPRIS almacene fabricante, titular y acondicionador en campos distintos.
- Comprueba presentación/cantidad cuando el OCR contiene `30 CAPSULAS`.
- Mantiene concentración como evidencia independiente.
- No usa Gemini.

## Prueba de regresión
Entrada OCR: `ACTRON 400 IBUPROFENO 400 mg CAJA CON 30 CAPSULAS BAYER`

Resultado esperado con la base suministrada:
- Estado: `VERIFIED`
- Registro: `124M2004 SSA`
- Nombre: `ACTRON`
- Estado COFEPRIS: `VIGENTE`

## Despliegue
La API reporta `medicheck-v49.2` y `motor_reconocimiento_v49_2` para verificar que la corrección esté activa en Render.
