# MediCheck MX v49 — Motor de reconocimiento robusto

- OCR local sin Gemini.
- Normalización conservadora y tolerancia a errores OCR.
- Levenshtein + similitud por tokens para candidatos.
- Comparación por campos: nombre, principio activo, concentración, fabricante, presentación y registro.
- La concentración tiene una regla estricta.
- Un registro exacto no permite ignorar contradicciones de nombre/activo/concentración.
- Margen entre primer y segundo candidato para evitar falsos positivos.
- Estados: VERIFIED, STRONG_MATCH, REVIEW, NO_MATCH, REVOCADO/CANCELADO solo con evidencia suficiente.
- La coincidencia COFEPRIS no prueba por sí sola autenticidad física.
