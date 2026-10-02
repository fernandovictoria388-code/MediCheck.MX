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

    # The first few substantive OCR lines are retained as name candidates.
    # The matching engine decides whether any of them actually matches COFEPRIS.
    name_candidates = []
    for line in lines:
        n = norm(line)
        if REGISTRY_RE.search(line) or CONCENTRATION_RE.search(line):
            continue
        if len(n) >= 4 and n not in name_candidates:
            name_candidates.append(line)
        if len(name_candidates) >= 8:
            break

    return {
        "registry": registries[0] if registries else "",
        "registries": registries,
        "concentrations": concentrations,
        "name_candidates": name_candidates,
        "ocr_lines": lines,
        "ocr_text": text[:20000],
    }



def _amount_units(value: Any) -> List[Tuple[float, str]]:
    out = []
    for n, u in re.findall(r"(\d+(?:[.,]\d+)?)\s*(MG|G|MCG|UG|µG|ML|L|UI|U|%)", str(value or ''), re.I):
        out.append((float(n.replace(',', '.')), u.upper().replace('UG', 'MCG').replace('µG', 'MCG')))
    return out


def concentration_equivalent(a: Any, b: Any) -> bool:
    """Literal unit equivalence; used when both sides express the same quantity."""
    pa, pb = _amount_units(a), _amount_units(b)
    for va, ua in pa:
        for vb, ub in pb:
            if ua == ub and abs(va-vb) <= max(0.001, abs(vb)*0.0005):
                return True
            if ua == 'G' and ub == 'MG' and abs(va*1000-vb) <= max(0.001, abs(vb)*0.0005):
                return True
            if ua == 'MG' and ub == 'G' and abs(va-vb*1000) <= max(0.001, abs(va)*0.0005):
                return True
    return False


def presentation_variants(value: Any) -> List[str]:
    return [x.strip() for x in re.split(r'\s*\|\s*', str(value or '')) if x.strip()]


def _nominal_strengths(text: Any) -> List[float]:
    """Extract nominal strengths written in presentations, e.g. 10/20/40/80 MG."""
    vals = []
    for seg in presentation_variants(text):
        for n, u in re.findall(r"(\d+(?:[.,]\d+)?)\s*(MG|G)\b", seg, re.I):
            v = float(n.replace(',', '.')) * (1000 if u.upper() == 'G' else 1)
            if v not in vals:
                vals.append(v)
    return vals


def concentration_matches_record(query_value: Any, row: Dict[str, Any]) -> bool:
    """Match label strength to COFEPRIS strength, including salt-equivalent records.

    COFEPRIS can store the amount of a salt (e.g. atorvastatina cálcica 20.682 mg)
    while the carton is labelled with the nominal active amount (20 mg). The
    presentation is the authoritative bridge between those representations.
    """
    q_amounts = _amount_units(query_value)
    if not q_amounts:
        return False
    # First: literal equivalence against the stored concentration field.
    if concentration_equivalent(query_value, row.get('concentration', '')):
        return True
    # Second: nominal strength explicitly present in the COFEPRIS presentation.
    q_mg = [v if u == 'MG' else (v * 1000 if u == 'G' else None) for v, u in q_amounts]
    q_mg = [v for v in q_mg if v is not None]
    strengths = _nominal_strengths(row.get('presentation', ''))
    return any(abs(q-strength) <= max(0.01, strength*0.002) for q in q_mg for strength in strengths)


def _name_ocr_similarity(row_value: Any, ocr: Dict[str, Any]) -> float:
    rn = norm(row_value)
    if not rn:
        return 0.0
    best = 0.0
    for x in ocr.get('name_candidates', []):
        if norm(x) == rn:
            return 1.0
        best = max(best, best_similarity(row_value, x))
    # Exact catalog name anywhere in OCR is strong evidence, even if another
    # OCR token was incorrectly promoted to the structured `name` field.
    text = norm(ocr.get('ocr_text', ''))
    if text and re.search(r'(?<![A-Z0-9])' + re.escape(rn) + r'(?![A-Z0-9])', text):
        best = max(best, 1.0)
    return best


def _manufacturer_similarity(row_value: Any, query_value: Any, ocr: Dict[str, Any]) -> float:
    """Compare manufacturer names by distinctive corporate tokens, not full address."""
    generic = {'PRIVATE', 'LIMITED', 'LIMITED', 'SA', 'CV', 'DE', 'LA', 'EL', 'AND', 'UNIT', 'DIVISION'}
    row_tokens = {x for x in tokens(row_value) if x not in generic}
    best = 0.0
    for source in [query_value] + list(ocr.get('ocr_lines', [])):
        st = {x for x in tokens(source) if x not in generic}
        if not st or not row_tokens:
            continue
        overlap = len(st & row_tokens)
        score = overlap / min(len(st), len(row_tokens))
        best = max(best, score)
        if {'MSN', 'LABORATORIES'}.issubset(st & row_tokens):
            best = max(best, 1.0)
    return best


def _field(row: Dict[str, Any], name: str) -> str:
    if name == "manufacturer":
        return str(row.get("manufacturer") or row.get("titular") or row.get("fabricantes_medicamentos") or "")
    if name == "active_ingredient":
        return str(row.get("active_ingredient") or "")
    return str(row.get(name) or "")


def compare_record(row: Dict[str, Any], query: Dict[str, Any], ocr: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Dict[str, float]]:
    checks: Dict[str, Any] = {}
    sims: Dict[str, float] = {}
    weights = {"registry": 45, "name": 25, "active_ingredient": 15, "concentration": 7, "presentation": 4, "manufacturer": 4}
    points = 0.0
    possible = 0.0

    registry_query = norm(query.get("registry")) if known(query.get("registry")) else ""
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
            sim = 1.0 if concentration_matches_record(q, row) else max([best_similarity(a, b) for a in qcs for b in rcs] or [best_similarity(q, row_value)])
        elif field == "presentation" and known(q):
            variants = presentation_variants(row_value)
            # If a concentration is supplied, compare only against variants carrying that strength.
            qcs = extract_concentrations(q)
            preferred = []
            if qcs:
                for seg in variants:
                    if any(concentration_equivalent(x, seg) or best_similarity(x, seg) >= 0.72 for x in qcs):
                        preferred.append(seg)
                variants = preferred or variants
            sim = max([best_similarity(q, seg) for seg in variants] or [0.0])
        elif field == "manufacturer":
            sim = _manufacturer_similarity(row_value, q, ocr)
        else:
            sim = best_similarity(q, row_value) if known(q) else 0.0
        # OCR remains independent evidence. If a structured field was itself
        # produced by imperfect OCR (e.g. MSND), a catalog-exact OCR hit such as
        # ATCAR must be allowed to rescue the field instead of being discarded.
        if ocr.get("ocr_text"):
            if field == "name":
                sim = max(sim, _name_ocr_similarity(row_value, ocr))
            elif field == "active_ingredient":
                active_norm = norm(str(row_value).split("/")[0])
                ocr_norm = norm(ocr.get("ocr_text", ""))
                # A catalog active ingredient appearing verbatim in either photo
                # is stronger evidence than similarity against a long OCR line.
                if active_norm and re.search(r"(?<![A-Z0-9])" + re.escape(active_norm) + r"(?![A-Z0-9])", ocr_norm):
                    sim = max(sim, 1.0)
                else:
                    sim = max(sim, max([best_similarity(row_value, x) for x in ocr.get("ocr_lines", [])] or [0.0]))
            elif field == "concentration":
                sim = max(sim, 1.0 if concentration_matches_record(" ".join(ocr.get("concentrations", [])), row) else 0.0)
            elif field == "manufacturer":
                sim = max(sim, _manufacturer_similarity(row_value, q, ocr))
            elif field == "presentation" and not known(q):
                sim = max(sim, max([best_similarity(row_value, x) for x in ocr.get("ocr_lines", [])] or [0.0]))
        if known(q) or (field in {"name", "active_ingredient", "concentration", "manufacturer"} and ocr.get("ocr_text")):
            possible += weights[field]
            ok = sim >= (0.82 if field in {"name", "manufacturer"} else 0.72)
            if field == "concentration":
                ok = concentration_matches_record(q, row) if known(q) else any(concentration_matches_record(x, row) for x in ocr.get("concentrations", []))
            if field == "name":
                ok = sim >= 0.82
            checks[field] = ok
            sims[field] = round(sim, 4)
            if ok:
                points += weights[field]

    confidence = round(100 * points / possible) if possible else 0
    return confidence, checks, sims


def _status_for(confidence: int, checks: Dict[str, Any], row_status: str, explicit_registry: bool) -> str:
    state = norm(row_status)
    strong_identity = bool(
        checks.get("name") and (
            (checks.get("active_ingredient") and (checks.get("manufacturer") or checks.get("concentration")))
            or checks.get("concentration")
        )
    )
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

    # Candidate selection: registry is strongest; exact commercial name is also
    # high-signal. This avoids a false NO_MATCH when OCR misses the active ingredient.
    registry = (norm(query.get("registry")) if known(query.get("registry")) else "") or (ocr.get("registries") or [""])[0]
    query_name = norm(query.get("name"))
    # If Android only supplied OCR, recover an exact commercial name from the
    # COFEPRIS catalog itself. This is deliberately catalog-constrained: the
    # engine never invents a name, it only accepts a name that exists in DB and
    # appears as a token/phrase in the OCR. Exact-name matches are evaluated
    # before fuzzy candidates, preventing LARITOL G from beating LARITOL.
    if not registry and not query_name and ocr.get("ocr_text"):
        ocr_norm = norm(ocr.get("ocr_text"))
        exact_rows = c.execute(
            "SELECT * FROM records WHERE name IS NOT NULL AND TRIM(name) <> ''"
        ).fetchall()
        hits = []
        for raw in exact_rows:
            rn = norm(raw["name"])
            if rn and re.search(r"(?<![A-Z0-9])" + re.escape(rn) + r"(?![A-Z0-9])", ocr_norm):
                hits.append((len(compact(rn)), dict(raw)))
        if hits:
            # Prefer the first prominent OCR token when it is an exact catalog
            # name. On medicine boxes the commercial name normally precedes
            # the active ingredient; this prevents LORATADINA from winning
            # over LARITOL in "LARITOL LORATADINA 10 mg".
            first_token = re.match(r"[A-Z0-9][A-Z0-9*_-]{2,}", ocr_norm)
            first = first_token.group(0) if first_token else ""
            exact_first = [h for h in hits if norm(h[1]["name"]) == first]
            pool = exact_first or hits
            pool.sort(key=lambda x: x[0], reverse=True)
            query_name = norm(pool[0][1]["name"])
    if registry:
        rows = c.execute("SELECT * FROM records WHERE UPPER(registry)=?", (registry,)).fetchall()
    elif query_name:
        rows = c.execute("SELECT * FROM records WHERE UPPER(name)=?", (query_name,)).fetchall()
        if not rows:
            rows = c.execute("SELECT * FROM records WHERE UPPER(name) LIKE ?", ("%" + query_name + "%",)).fetchall()
    else:
        rows = c.execute("SELECT * FROM records").fetchall()

    candidates = []
    for raw in rows:
        row = dict(raw)
        confidence, checks, sims = compare_record(row, query, ocr)
        candidates.append((confidence, row, checks, sims))
    candidates.sort(key=lambda x: (
        1 if query_name and norm(x[1].get("name")) == query_name else 0,
        x[0],
        x[3].get("registry", 0),
        x[3].get("name", 0),
        x[3].get("active_ingredient", 0)
    ), reverse=True)

    if not candidates:
        return {
            "status": "NO_MATCH", "found": False, "confidence": 0,
            "message": "No se encontró un registro compatible en la base COFEPRIS cargada. Esto no demuestra que el medicamento carezca de registro.",
            "candidates": [], "evidence": {"ocr_supplied": bool(ocr.get("ocr_text")), "registry_supplied": bool(registry)},
        }

    best_conf, best_row, best_checks, best_sims = candidates[0]
    # Clean empty separators found in some source spreadsheets (e.g. "A / / /")
    # for display/API consumers while retaining the original source_fragment.
    if best_row.get("active_ingredient"):
        ingredients = [x.strip() for x in str(best_row["active_ingredient"]).split("/") if x.strip()]
        if ingredients:
            best_row["active_ingredient"] = " / ".join(ingredients)
    explicit_registry = bool(known(query.get("registry")) or ocr.get("registries"))

    # Apply the separately synchronized COFEPRIS revocation/cancellation lists.
    # These lists are authoritative status evidence and must not be ignored just
    # because the original product row came from an older dataset snapshot.
    try:
        status_row = c.execute(
            "SELECT status, reason, source_url, source_date FROM statuses WHERE UPPER(registry)=?",
            (norm(best_row.get("registry")),),
        ).fetchone()
        if status_row and norm(status_row["status"]) in {"REVOCADO", "CANCELADO"}:
            best_row["status"] = norm(status_row["status"])
            best_row["status_reason"] = status_row["reason"] or ""
            best_row["status_source_url"] = status_row["source_url"] or ""
            best_row["status_source_date"] = status_row["source_date"] or ""
    except Exception:
        # Older/partial DBs may not yet contain the statuses table.
        pass

    # High-confidence deterministic path: exact commercial name + equivalent
    # concentration identifies the COFEPRIS record even when active ingredient
    # OCR is missing. The matched record then supplies the authoritative fields.
    exact_name = bool(query_name and norm(best_row.get("name")) == query_name)
    concentration_ok = bool(best_checks.get("concentration"))
    if exact_name and concentration_ok and norm(best_row.get("status", "")) not in {"REVOCADO", "CANCELADO"}:
        status = "VERIFIED"
        best_conf = 100
    else:
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

    # Expose the specific presentation variant that matched the observed strength.
    # This prevents the Android UI from having to display the entire pipe-separated
    # list of all strengths in the COFEPRIS source row.
    matched_variant = ""
    observed_concentration = ""
    for source in [query.get("concentration", "")] + ocr.get("concentrations", []):
        if source:
            observed_concentration = source
            break
    if observed_concentration:
        for seg in presentation_variants(best_row.get("presentation", "")):
            if concentration_equivalent(observed_concentration, seg):
                matched_variant = seg
                break
            if any(best_similarity(x, seg) >= 0.72 for x in extract_concentrations(observed_concentration)):
                matched_variant = seg
                break

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
        "matched_presentation_variant": matched_variant,
        "observed_concentration": observed_concentration,
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
