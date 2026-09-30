from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path
import os, json, sqlite3, datetime, re

from motor_reconocimiento import recognize, norm, known

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except Exception:
    pass

app = FastAPI(title='MediCheck MX Verification API', version='0.492')
BASE = Path(__file__).parent
DB = BASE / 'cofepris.sqlite3'
SEED_FILE = BASE / 'seed_cofepris.json'
COFEPRIS_PAGE = 'https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos'
COFEPRIS_VIEWER = 'https://registros.cofepris.gob.mx/BRSDM/default.aspx'

class VerifyRequest(BaseModel):
    barcode: str = ''
    name: str = ''
    active_ingredient: str = ''
    concentration: str = ''
    presentation: str = ''
    manufacturer: str = ''
    registry: str = ''
    lot: str = ''
    expiry: str = ''

class AnalyzeVerifyRequest(BaseModel):
    image_base64: str = ''
    mime_type: str = 'image/jpeg'
    barcode: str = ''
    ocr_text: str = ''
    name: str = ''
    active_ingredient: str = ''
    concentration: str = ''
    presentation: str = ''
    manufacturer: str = ''
    registry: str = ''

class BarcodeLookupRequest(BaseModel):
    barcode: str
    ocr_text: str = ''


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS records (
        registry TEXT PRIMARY KEY, name TEXT, active_ingredient TEXT, concentration TEXT,
        presentation TEXT, manufacturer TEXT, status TEXT DEFAULT 'VIGENTE',
        source_url TEXT, source_date TEXT, source_fragment TEXT,
        fecha_expedicion_vigencia TEXT, fecha_expedicion_vigencia_prorroga TEXT,
        forma_farmaceutica TEXT, indicaciones_terapeuticas TEXT, contra_indicaciones TEXT,
        vida_util TEXT, fraccion TEXT, denominacion_generica TEXT, vista_administracion TEXT,
        tipo_medicamento TEXT, cantidad TEXT, sistema_organico TEXT, grupo_farmacologico TEXT,
        subgrupo_farmacologico TEXT, subgrupo_quimico TEXT, sustancia_quimica TEXT,
        titular TEXT, domicilio TEXT, fabricantes_medicamentos TEXT, fabricantes_farmacos TEXT,
        acondicionado_por TEXT, acondicionado_extranjero TEXT, distribuidores TEXT,
        unidad_farmaco_vigilancia TEXT, fecha_emision TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS statuses (
        registry TEXT PRIMARY KEY,status TEXT,reason TEXT,source_url TEXT,source_date TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS sync_sources (
        key TEXT PRIMARY KEY,label TEXT,url TEXT,kind TEXT,year TEXT,last_sync TEXT,bytes INTEGER,record_count INTEGER
    )''')
    c.commit()
    seed_from_supplied_dataset(c)
    return c


def seed_from_supplied_dataset(c):
    if not SEED_FILE.exists() or c.execute('SELECT COUNT(*) FROM records').fetchone()[0] > 0:
        return 0
    data = json.loads(SEED_FILE.read_text(encoding='utf-8'))
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    n = 0
    for x in data:
        reg = norm(x.get('numero_registro'))
        status = norm(x.get('estado'))
        c.execute('''INSERT OR REPLACE INTO records
            (registry,name,active_ingredient,concentration,presentation,manufacturer,status,source_url,source_date,source_fragment)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (reg, x.get('denominacion_distintiva',''), x.get('principio_activo',''), '',
             x.get('denominacion_generica',''), x.get('titular_registro',''),
             status or 'VIGENTE', COFEPRIS_PAGE, today, json.dumps(x, ensure_ascii=False)))
        if status in {'REVOCADO','CANCELADO'}:
            c.execute('INSERT OR REPLACE INTO statuses VALUES (?,?,?,?,?)',
                      (reg,status,f'El conjunto de datos suministrado indica estado {status}.',COFEPRIS_PAGE,today))
        n += 1
    c.execute('INSERT OR REPLACE INTO sync_sources VALUES (?,?,?,?,?,?,?,?)',
              ('supplied_seed','Conjunto COFEPRIS suministrado por el usuario',COFEPRIS_PAGE,'seed','2006-2026',today,SEED_FILE.stat().st_size,n))
    c.commit()
    return n


def source_info():
    c = db()
    rows = [dict(r) for r in c.execute('SELECT * FROM sync_sources ORDER BY key')]
    return {
        'official_page': COFEPRIS_PAGE,
        'viewer': COFEPRIS_VIEWER,
        'last_sync': max([r['last_sync'] for r in rows if r['last_sync']] or [None]),
        'sources': rows,
        'database': DB.name,
        'records': c.execute('SELECT COUNT(*) FROM records').fetchone()[0],
        'statuses': c.execute('SELECT COUNT(*) FROM statuses').fetchone()[0],
        'record_status_breakdown': [dict(r) for r in c.execute('SELECT status, COUNT(*) AS count FROM records GROUP BY status ORDER BY status')],
    }


_SYNC_STARTED = False

def _background_cofepris_sync():
    global _SYNC_STARTED
    if _SYNC_STARTED or os.getenv('AUTO_SYNC','0').strip().lower() not in {'1','true','yes'}:
        return
    _SYNC_STARTED = True
    def worker():
        try:
            from sync_cofepris import sync_if_due
            result = sync_if_due()
            print('COFEPRIS_AUTO_SYNC:', json.dumps(result, ensure_ascii=False)[:4000], flush=True)
        except Exception as exc:
            print('COFEPRIS_AUTO_SYNC_ERROR:', repr(exc), flush=True)
    import threading
    threading.Thread(target=worker, name='cofepris-auto-sync', daemon=True).start()


_background_cofepris_sync()


@app.get('/health')
def health():
    info = source_info()
    return {'ok': True, 'service': 'medicheck-v49.2', 'gemini': False, 'recognition_engine': 'motor_reconocimiento_v49_2', **info}

@app.get('/sources')
def sources():
    return source_info()

@app.get('/gemini/status')
def gemini_status():
    return {'configured': False, 'enabled': False, 'reason': 'Gemini fue retirado del flujo V49.'}

@app.post('/gemini/analyze')
def gemini_removed():
    raise HTTPException(status_code=410, detail='Gemini está deshabilitado en MediCheck MX V49. Usa /analyze-and-verify con OCR local.')

@app.post('/barcode-lookup')
def barcode_lookup(req: BarcodeLookupRequest):
    # COFEPRIS no es un catálogo GTIN. El código se conserva como evidencia y
    # el reconocimiento regulatorio se realiza con el OCR/campos disponibles.
    c = db()
    result = recognize(c, {'name':'', 'active_ingredient':'', 'concentration':'', 'presentation':'', 'manufacturer':'', 'registry':''}, req.ocr_text)
    result['barcode'] = re.sub(r'\s+', '', req.barcode or '')
    result['evidence']['barcode_supplied'] = bool(req.barcode)
    result['message'] = 'El código de barras fue recibido. La base COFEPRIS se consulta con la identificación textual/OCR; el GTIN no se trata como registro sanitario.'
    return {'ok': True, 'workflow': 'barcode->ocr->cofepris', 'identification': result}

@app.post('/cofepris/live')
def cofepris_live(req: VerifyRequest):
    c = db()
    result = recognize(c, req.model_dump(), '')
    result['source'] = result.get('source') or COFEPRIS_PAGE
    result['viewer'] = COFEPRIS_VIEWER
    return result

@app.post('/verify')
def verify(req: VerifyRequest):
    c = db()
    result = recognize(c, req.model_dump(), '')
    result['viewer'] = COFEPRIS_VIEWER
    return result

@app.post('/analyze-and-verify')
def analyze_and_verify(req: AnalyzeVerifyRequest):
    if not req.ocr_text.strip() and not any([req.name, req.active_ingredient, req.concentration, req.presentation, req.manufacturer, req.registry]):
        raise HTTPException(status_code=400, detail='No hay OCR ni datos identificables. Realiza el OCR en el teléfono y vuelve a enviar la lectura.')
    c = db()
    query = {
        'barcode': req.barcode,
        'name': req.name,
        'active_ingredient': req.active_ingredient,
        'concentration': req.concentration,
        'presentation': req.presentation,
        'manufacturer': req.manufacturer,
        'registry': req.registry,
    }
    result = recognize(c, query, req.ocr_text)
    result['evidence']['barcode_supplied'] = bool(req.barcode)
    result['evidence']['photo_received'] = bool(req.image_base64)
    return {
        'ok': True,
        'workflow': 'foto->OCR/Barcode local->motor V49.2->COFEPRIS',
        'gemini': None,
        'verification': result,
    }

@app.post('/sync')
def sync():
    expected = os.getenv('SYNC_TOKEN','').strip()
    supplied = os.getenv('SYNC_TOKEN_HEADER_VALUE','').strip()
    if expected and supplied != expected:
        raise HTTPException(status_code=403, detail='Sin autorización para sincronizar COFEPRIS.')
    try:
        from sync_cofepris import sync_official_sources
        return sync_official_sources(force=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
