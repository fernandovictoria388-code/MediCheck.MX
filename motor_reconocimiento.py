"""Motor V49 de reconocimiento determinista para MediCheck MX.

No usa modelos de IA ni servicios externos para identificar el medicamento.
Recibe campos estructurados y/o OCR producido por el teléfono y los compara
contra la base COFEPRIS local. Un score ordena candidatos; los estados se
emiten únicamente cuando existen reglas de evidencia suficientes.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Tuple

UNKNOWN = {"", "NO IDENTIFICADO", "N/A", "NA", "NULL", "NONE", "UNKNOWN"}
REGISTRY_RE = re.compile(r"\b\d{1,6}[A-Z]?\d{0,4}\s*(?:SSA|SS[A-Z]?|MEX)\b", re.I)
CONCENTRATION_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:MG|G|MCG|UG|µG|ML|L|UI|U|%|MG/ML|MG\s*/\s*ML)\b",
    re.I,
)
LABELS = {
    "NOMBRE", "NOMBRE COMERCIAL", "DENOMINACION DISTINTIVA", "DENOMINACION GENERICA",
    "PRINCIPIO ACTIVO", "PRINCIPIOS ACTIVOS", "CONCENTRACION", "PRESENTACION",
    "FABRICANTE", "LABORATORIO", "TITULAR", "REGISTRO", "REGISTRO SANITARIO",
    "LOTE", "CADUCIDAD", "FECHA DE CADUCIDAD", "EXPIRACION", "VIGENCIA",
}


def norm(value: Any) -> str:
    s = str(value or "").upper().strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("µ", "U")
    return re.sub(r"\s+", " ", s)


def compact(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", norm(value))


def known(value: Any) -> bool:
    return norm(value) not in UNKNOWN


def tokens(value: Any) -> set[str]:
    return {x for x in re.findall(r"[A-Z0-9]+", norm(value)) if len(x) > 1}


def token_similarity(a: Any, b: Any) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    # F1-like token score; more robust to OCR inserting/removing words.
    return (2.0 * inter) / (len(ta) + len(tb))


def char_similarity(a: Any, b: Any) -> float:
    a, b = compact(a), compact(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    return difflib.SequenceMatcher(None, a, b).ratio()


def best_similarity(a: Any, b: Any) -> float:
    return max(token_similarity(a, b), char_similarity(a, b))


def contains(value: Any, text: Any) -> bool:
    a, b = norm(value), norm(text)
    return bool(a and b and (a in b or compact(a) in compact(b)))

def token_presence_score(value: Any, text: Any, min_len: int = 4) -> float:
    """Evidence de que términos distintivos del campo aparecen en OCR.
    Se usa para campos donde el OCR suele mezclar varias líneas (p.ej.
    marca, principio activo o titular). No exige que todo el texto coincida.
    """
    vt = {t for t in tokens(value) if len(t) >= min_len}
    tt = {t for t in tokens(text) if len(t) >= min_len}
    if not vt or not tt:
        return 0.0
    return len(vt & tt) / len(vt)



MANUFACTURER_STOPWORDS = {
    "SA", "S", "DE", "CV", "C", "A", "SAS", "LLC", "INC", "LTD",
    "MEXICO", "MEXICO", "ESPANA", "ESPAÑA", "ALEMANIA", "ARGENTINA",
    "BRASIL", "CHILE", "COLOMBIA", "USA", "CANADA", "CANADA",
}

def manufacturer_presence_score(value: Any, text: Any) -> float:
    """Busca una marca/titular distintivo visible en el OCR.
    Evita usar palabras jurídicas o geográficas como evidencia por sí solas.
    """
    vt = {t for t in tokens(value) if len(t) >= 5 and t not in MANUFACTURER_STOPWORDS}
    tt = {t for t in tokens(text) if len(t) >= 5}
    if not vt or not tt:
        return 0.0
    return 1.0 if (vt & tt) else 0.0

def extract_registries(text: str) -> List[str]:
    seen = []
    for m in REGISTRY_RE.findall(text or ""):
        v = re.sub(r"\s+", " ", m).strip().upper()
        if v not in seen:
            seen.append(v)
    return seen


def extract_concentrations(text: str) -> List[str]:
    seen = []
    for m in CONCENTRATION_RE.findall(text or ""):
        v = re.sub(r"\s+", "", m.upper().replace(",", ".")).replace("UG", "MCG")
        if v not in seen:
            seen.append(v)
    return seen


def clean_ocr_lines(ocr_text: str) -> List[str]:
    out = []
    for raw in str(ocr_text or "").splitlines():
        line = re.sub(r"[^\w%/.,:+()\- ]+", " ", raw, flags=re.UNICODE)
        line = re.sub(r"\s+", " ", line).strip(" -:|")
        if len(line) < 3:
            continue
        if norm(line) in LABELS:
            continue
        out.append(line)
    return out


def infer_fields(ocr_text: str) -> Dict[str, Any]:
    """Extract only high-signal facts; it deliberately does not invent a name."""
    text = str(ocr_text or "")
    lines = clean_ocr_lines(text)
    registries = extract_registries(text)
    concentrations = extract_concentrations(text)

    # Conserva líneas completas y también fragmentos de alta señal.
    # En una sola línea de OCR es frecuente que aparezcan marca + dosis +
    # activo + fabricante (p.ej. "ACTRON 400 IBUPROFENO ... BAYER").
    name_candidates = []
    for line in lines:
        n = norm(line)
        if len(n) >= 4 and n not in name_candidates:
            name_candidates.append(line)
        # Extraer secuencias alfabéticas de 1-2 palabras ayuda a recuperar
        # la marca cuando la misma línea también contiene concentración.
        words = re.findall(r"[A-ZÁÉÍÓÚÑÜ]{4,}(?:\s+[A-ZÁÉÍÓÚÑÜ]{2,})?", n)
        for w in words:
            if w not in LABELS and w not in name_candidates:
                name_candidates.append(w)
        if len(name_candidates) >= 24:
            break

    return {
        "registry": registries[0] if registries else "",
        "registries": registries,
        "concentrations": concentrations,
        "name_candidates": name_candidates,
        "ocr_lines": lines,
        "ocr_text": text[:20000],
    }



def presentation_equivalent(a: Any, b: Any) -> bool:
    """Detecta coincidencias prácticas de forma farmacéutica, cantidad y dosis.
    Ej.: 'caja con 30 cápsulas 400 mg' frente a una presentación COFEPRIS
    que contiene '30 cápsulas de 400 o 600 mg'.
    """
    aa, bb = norm(a), norm(b)
    if not aa or not bb:
        return False
    units = ("CAPSULA", "CAPSULAS", "TABLETA", "TABLETAS", "COMPRIMIDO", "COMPRIMIDOS", "FRASCO", "AMPOLLETA", "ML")
    a_units = {u for u in units if u in aa}
    b_units = {u for u in units if u in bb}
    if a_units and not (a_units & b_units):
        return False
    nums_a = re.findall(r"\b(\d+)\s*(?:CAPSULAS?|TABLETAS?|COMPRIMIDOS?|ML|G)\b", aa)
    nums_b = re.findall(r"\b(\d+)\s*(?:CAPSULAS?|TABLETAS?|COMPRIMIDOS?|ML|G)\b", bb)
    count_ok = bool(set(nums_a) & set(nums_b)) if nums_a and nums_b else True
    return count_ok and (a_units <= b_units or not a_units)

def concentration_equivalent(a: Any, b: Any) -> bool:
    pa = [(float(n.replace(',', '.')), u.upper().replace('UG','MCG').replace('µG','MCG')) for n,u in re.findall(r"(\d+(?:[.,]\d+)?)\s*(MG|G|MCG|UG|µG|ML|L|UI|U|%)", str(a or ''), re.I)]
    pb = [(float(n.replace(',', '.')), u.upper().replace('UG','MCG').replace('µG','MCG')) for n,u in re.findall(r"(\d+(?:[.,]\d+)?)\s*(MG|G|MCG|UG|µG|ML|L|UI|U|%)", str(b or ''), re.I)]
    for va,ua in pa:
        for vb,ub in pb:
            if ua == ub and abs(va-vb) <= max(0.001, abs(vb)*0.0005):
                return True
            if ua == 'G' and ub == 'MG' and abs(va*1000-vb) <= max(0.001, abs(vb)*0.0005):
                return True
            if ua == 'MG' and ub == 'G' and abs(va-vb*1000) <= max(0.001, abs(va)*0.0005):
                return True
    return False

def _field(row: Dict[str, Any], name: str) -> str:
    if name == "manufacturer":
        # COFEPRIS distingue titular, fabricante, acondicionador y distribuidor.
        # Para identificar el envase, cualquiera de ellos puede aportar evidencia
        # de la marca/fabricante visible en la caja.
        vals = [row.get("manufacturer"), row.get("titular"),
                row.get("fabricantes_medicamentos"), row.get("acondicionado_por"),
                row.get("distribuidores")]
        return " | ".join(str(v) for v in vals if v)
    if name == "active_ingredient":
        return str(row.get("active_ingredient") or "")
    return str(row.get(name) or "")


def compare_record(row: Dict[str, Any], query: Dict[str, Any], ocr: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Dict[str, float]]:
    checks: Dict[str, Any] = {}
    sims: Dict[str, float] = {}
    weights = {"registry": 45, "name": 25, "active_ingredient": 15, "concentration": 7, "presentation": 4, "manufacturer": 4}
    points = 0.0
    possible = 0.0

    registry_query = norm(query.get("registry"))
    ocr_regs = [norm(x) for x in ocr.get("registries", [])]
    if registry_query:
        possible += weights["registry"]
        ok = norm(row.get("registry")) == registry_query
        checks["registry"] = ok
        sims["registry"] = 1.0 if ok else 0.0
        if ok:
            points += weights["registry"]
    elif ocr_regs:
        possible += weights["registry"]
        ok = norm(row.get("registry")) in ocr_regs
        checks["registry"] = ok
        sims["registry"] = 1.0 if ok else 0.0
        if ok:
            points += weights["registry"]

    for field in ("name", "active_ingredient", "concentration", "presentation", "manufacturer"):
        q = query.get(field, "")
        row_value = _field(row, field)
        if field == "concentration" and known(q):
            qcs = extract_concentrations(q)
            rcs = extract_concentrations(str(row.get("concentration") or ""))
            sim = 1.0 if concentration_equivalent(q, row_value) else max([best_similarity(a, b) for a in qcs for b in rcs] or [best_similarity(q, row_value)])
        elif field == "manufacturer" and known(q):
            qt, rt = tokens(q), tokens(row_value)
            sim = (len(qt & rt) / len(qt)) if qt else 0.0
            sim = max(sim, char_similarity(q, row_value) if compact(q) == compact(row_value) else 0.0)
        else:
            sim = best_similarity(q, row_value) if known(q) else 0.0
            if field == "name" and known(q) and known(row_value):
                qt, rt = set(tokens(q)), set(tokens(row_value))
                if rt and rt.issubset(qt):
                    sim = max(sim, 1.0)
            if field == "presentation" and known(q) and presentation_equivalent(q, row_value):
                sim = max(sim, 1.0)
        # OCR is treated as evidence, not as a fabricated structured field.
        if not known(q) and ocr.get("ocr_text"):
            if field == "name":
                # La marca puede estar incrustada en una línea con dosis y activo.
                sim = max(
                    [1.0 if contains(row_value, ocr.get("ocr_text", "")) else 0.0] +
                    [best_similarity(row_value, x) for x in ocr.get("name_candidates", [])]
                )
            elif field == "active_ingredient":
                sim = max(
                    [max(token_presence_score(row_value, ocr.get("ocr_text", "")), manufacturer_presence_score(row_value, ocr.get("ocr_text", "")))] +
                    [best_similarity(row_value, x) for x in ocr.get("ocr_lines", [])]
                )
            elif field == "concentration":
                row_cs = extract_concentrations(str(row.get("concentration") or ""))
                sim = 1.0 if any(concentration_equivalent(a, b) for a in row_cs for b in ocr.get("concentrations", [])) else max([best_similarity(a, b) for a in row_cs for b in ocr.get("concentrations", [])] or [0.0])
            elif field == "manufacturer":
                # La caja puede mostrar sólo la marca del titular (p.ej. BAYER),
                # mientras COFEPRIS almacena titular/fabricante/domicilio.
                sim = max(
                    [max(token_presence_score(row_value, ocr.get("ocr_text", "")), manufacturer_presence_score(row_value, ocr.get("ocr_text", "")))] +
                    [best_similarity(row_value, x) for x in ocr.get("ocr_lines", [])]
                )
            else:
                sim = best_similarity(row_value, ocr.get("ocr_text", ""))
                if field == "presentation":
                    sim = max(sim, 1.0 if presentation_equivalent(ocr.get("ocr_text", ""), row_value) else 0.0)
        if known(q) or (field in {"name", "active_ingredient", "concentration", "manufacturer"} and ocr.get("ocr_text")):
            possible += weights[field]
            ok = sim >= (0.82 if field in {"name", "manufacturer"} else 0.72)
            if field == "concentration" and ocr.get("concentrations"):
                ok = any(concentration_equivalent(row_value, x) or best_similarity(row_value, x) >= 0.72 for x in ocr["concentrations"])
            checks[field] = ok
            sims[field] = round(sim, 4)
            if ok:
                points += weights[field]

    confidence = round(100 * points / possible) if possible else 0
    return confidence, checks, sims


def _status_for(confidence: int, checks: Dict[str, Any], row_status: str, explicit_registry: bool) -> str:
    state = norm(row_status)
    strong_identity = bool(checks.get("name") and checks.get("active_ingredient") and (checks.get("manufacturer") or checks.get("concentration")))
    registry_ok = bool(checks.get("registry"))
    if state in {"REVOCADO", "CANCELADO"} and (registry_ok or strong_identity):
        return state
    if confidence >= 90 and (registry_ok or strong_identity):
        return "VERIFIED"
    if confidence >= 75 and (registry_ok or strong_identity or checks.get("name")):
        return "STRONG_MATCH"
    if confidence >= 45:
        return "REVIEW"
    return "NO_MATCH"




def _candidate_rows(c, query: Dict[str, Any], ocr: Dict[str, Any]):
    """Reduce el universo antes del fuzzy matching.

    V49.2 comparaba cada solicitud contra los 14,920 registros y ejecutaba
    múltiples SequenceMatcher por registro. Eso puede convertir una consulta
    de OCR en decenas de miles de comparaciones costosas. V49.3 usa primero
    coincidencias léxicas en SQLite y sólo aplica el motor de similitud al
    subconjunto recuperado.
    """
    registry = norm(query.get("registry")) or (ocr.get("registries") or [""])[0]
    if registry:
        return c.execute("SELECT * FROM records WHERE UPPER(registry)=?", (registry,)).fetchall()

    # Tokens de alta señal. Evitamos términos demasiado genéricos del envase.
    raw = []
    for value in [query.get("name"), query.get("active_ingredient"),
                  query.get("manufacturer"), ocr.get("ocr_text", "")]:
        raw.extend(tokens(value))
    stop = {
        "CAJA", "CON", "CAPSULA", "CAPSULAS", "TABLETA", "TABLETAS",
        "COMPRIMIDO", "COMPRIMIDOS", "PARA", "POR", "EN", "DE", "DEL",
        "MG", "MCG", "ML", "G", "MEXICO", "SSA", "BAYER",
    }
    strong = [t for t in raw if len(t) >= 4 and t not in stop and not t.isdigit()]
    # Mantener orden y eliminar duplicados.
    strong = list(dict.fromkeys(strong))[:8]

    if not strong:
        # Fallback conservador para OCR de muy baja señal.
        return c.execute("SELECT * FROM records").fetchall()

    clauses = []
    params = []
    columns = [
        "name", "active_ingredient", "manufacturer", "titular",
        "fabricantes_medicamentos", "fabricantes_farmacos",
        "acondicionado_por", "distribuidores", "denominacion_generica"
    ]
    for tok in strong:
        like = f"%{tok}%"
        clauses.append("(" + " OR ".join(f"UPPER(COALESCE({col},'')) LIKE ?" for col in columns) + ")")
        params.extend([like] * len(columns))
    sql = "SELECT * FROM records WHERE " + " OR ".join(clauses)
    rows = c.execute(sql, params).fetchall()

    # Si el filtro léxico no encuentra nada, hacemos un único fallback completo.
    if not rows:
        return c.execute("SELECT * FROM records").fetchall()
    return rows

def recognize(c, query: Dict[str, Any] | None = None, ocr_text: str = "", limit: int = 5) -> Dict[str, Any]:
    query = dict(query or {})
    for k in ("registry", "name", "active_ingredient", "concentration", "presentation", "manufacturer"):
        query[k] = str(query.get(k) or "").strip()

    ocr = infer_fields(ocr_text)
    if not any(known(query.get(k)) for k in query) and not ocr.get("ocr_text", "").strip():
        return {
            "status": "INSUFFICIENT_DATA", "found": False, "confidence": 0,
            "message": "No hay código, texto OCR ni datos del medicamento para realizar una comparación.",
            "candidates": [], "evidence": {"ocr_supplied": False, "registry_supplied": False},
        }

    # Primero filtramos candidatos en SQLite; el fuzzy matching sólo se aplica
    # al subconjunto recuperado. Esto evita escanear y comparar los 14,920
    # registros en cada fotografía.
    registry = norm(query.get("registry")) or (ocr.get("registries") or [""])[0]
    rows = _candidate_rows(c, query, ocr)

    candidates = []
    for raw in rows:
        row = dict(raw)
        confidence, checks, sims = compare_record(row, query, ocr)
        candidates.append((confidence, row, checks, sims))
    candidates.sort(key=lambda x: (x[0], x[3].get("registry", 0), x[3].get("name", 0), x[3].get("active_ingredient", 0)), reverse=True)

    if not candidates:
        return {
            "status": "NO_MATCH", "found": False, "confidence": 0,
            "message": "No se encontró un registro compatible en la base COFEPRIS cargada. Esto no demuestra que el medicamento carezca de registro.",
            "candidates": [], "evidence": {"ocr_supplied": bool(ocr.get("ocr_text")), "registry_supplied": bool(registry)},
        }

    best_conf, best_row, best_checks, best_sims = candidates[0]
    explicit_registry = bool(query.get("registry") or ocr.get("registries"))
    status = _status_for(best_conf, best_checks, best_row.get("status", ""), explicit_registry)

    top = []
    for conf, row, checks, sims in candidates[:max(1, limit)]:
        top.append({
            "confidence": conf,
            "registry": row.get("registry", ""),
            "name": row.get("name", ""),
            "active_ingredient": row.get("active_ingredient", ""),
            "concentration": row.get("concentration", ""),
            "presentation": row.get("presentation", ""),
            "manufacturer": row.get("manufacturer", "") or row.get("titular", ""),
            "status": row.get("status", ""),
            "field_checks": checks,
            "similarities": sims,
        })

    # Contradiction: an explicit registry plus a strong name mismatch means review,
    # never a verified result.
    contradiction = False
    if explicit_registry and best_checks.get("registry") and known(query.get("name")):
        contradiction = not best_checks.get("name", False)
    if contradiction and status == "VERIFIED":
        status = "REVIEW"

    message = {
        "VERIFIED": "Identificación consistente con un registro de la base COFEPRIS cargada.",
        "STRONG_MATCH": "Hay una coincidencia fuerte, pero falta evidencia para considerarla concluyente.",
        "REVIEW": "La información coincide parcialmente; se requiere revisar el envase y/o más datos.",
        "NO_MATCH": "No hay una coincidencia suficiente en la base COFEPRIS cargada.",
        "REVOCADO": "La coincidencia corresponde a un registro cuyo estado en la base cargada es REVOCADO.",
        "CANCELADO": "La coincidencia corresponde a un registro cuyo estado en la base cargada es CANCELADO.",
    }.get(status, "Resultado de comparación.")

    return {
        "status": status,
        "found": status not in {"NO_MATCH", "INSUFFICIENT_DATA"},
        "confidence": best_conf,
        "message": message,
        "matched_record": best_row,
        "field_checks": best_checks,
        "similarities": best_sims,
        "candidates": top,
        "evidence": {
            "ocr_supplied": bool(ocr.get("ocr_text", "").strip()),
            "registry_supplied": explicit_registry,
            "registry_detected": ocr.get("registries", []),
            "concentrations_detected": ocr.get("concentrations", []),
            "contradiction_detected": contradiction,
            "database_records": c.execute("SELECT COUNT(*) FROM records").fetchone()[0],
        },
        "source": best_row.get("source_url"),
        "source_date": best_row.get("source_date"),
    }
