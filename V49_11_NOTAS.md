# MediCheck MX v49.11 — normalización farmacéutica y OCR de dos fotos

Cambios:
- Mantiene el endpoint multi como objeto JSON (corrección del HTTP 422).
- Un nombre comercial exacto presente en el OCR puede rescatar un campo estructurado mal leído por OCR (ej. MSND -> ATCAR), sin inventar nombres fuera del catálogo COFEPRIS.
- La concentración nominal del envase se compara contra la variante de presentación correspondiente. Esto permite casos donde COFEPRIS almacena la cantidad de la sal (ej. atorvastatina cálcica 20.682 mg) y el envase muestra 20 mg.
- La presentación se compara contra la variante compatible con la concentración observada, no contra toda la cadena de variantes.
- Fabricantes se comparan por tokens corporativos distintivos, evitando exigir la dirección completa.
- La contradicción por nombre OCR incorrecto deja de dispararse cuando existe un nombre exacto del catálogo en las fotografías.
- La respuesta expone `matched_presentation_variant` y `observed_concentration`.
- No se declara autenticidad física por una coincidencia de registro; el resultado sigue siendo una comparación regulatoria.

Prueba local ATCAR con registro 044M2023 SSA, 20 mg, 30 tabletas y MSN Laboratories: nombre, activo, concentración, presentación, fabricante y registro coinciden con el registro cargado.
