from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path
import os, json, base64, sqlite3, requests, re, datetime

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except Exception:
    pass

app = FastAPI(title='MediCheck MX Verification API', version='0.43')
BASE = Path(__file__).parent
DATA = BASE
DB = BASE / 'cofepris.sqlite3'
COFEPRIS_PAGE='https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos'
COFEPRIS_VIEWER='https://registros.cofepris.gob.mx/BRSDM/default.aspx'
# Gemini 3.8 is the primary production model. If Render still has an old
# GEMINI_MODEL such as gemini-1.5-flash, it is intentionally ignored so a stale
# environment variable cannot keep the service on the failing legacy model.
_requested_model=os.getenv('GEMINI_MODEL','').strip()
_env_models=[x.strip() for x in os.getenv('GEMINI_MODELS','').split(',') if x.strip()]
if _env_models:
    GEMINI_MODELS=list(dict.fromkeys(_env_models))
elif _requested_model and not _requested_model.startswith('gemini-1.5-'):
    GEMINI_MODELS=list(dict.fromkeys([_requested_model,'gemini-3.8-flash','gemini-3.5-flash-lite','gemini-2.5-flash']))
else:
    GEMINI_MODELS=['gemini-3.8-flash','gemini-3.5-flash-lite','gemini-2.5-flash']
GEMINI_MODEL=GEMINI_MODELS[0]
GEMINI_URL='https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
SEED_FILE = BASE / 'seed_cofepris.json'

class VerifyRequest(BaseModel):
    barcode:str=''; name:str=''; active_ingredient:str=''; concentration:str=''; presentation:str=''; manufacturer:str=''; registry:str=''; lot:str=''; expiry:str=''
class AnalyzeVerifyRequest(BaseModel):
    image_base64:str; mime_type:str='image/jpeg'; barcode:str=''; ocr_text:str=''
class GeminiRequest(AnalyzeVerifyRequest): pass

import unicodedata

def norm(v):
    s=str(v or '').upper().strip()
    s=''.join(ch for ch in unicodedata.normalize('NFKD',s) if not unicodedata.combining(ch))
    return re.sub(r'\s+',' ',s)

def known(v): return bool(v and norm(v) not in {'NO IDENTIFICADO','N/A','NULL','NONE',''})

def compact(v): return re.sub(r'[^A-Z0-9]+','',norm(v))

def contains_match(a,b):
    a=norm(a); b=norm(b)
    if not a or not b: return False
    return a==b or a in b or b in a or compact(a)==compact(b)

def token_score(a,b):
    a=set(re.findall(r'[A-Z0-9]+',norm(a))); b=set(re.findall(r'[A-Z0-9]+',norm(b)))
    if not a or not b: return 0.0
    return len(a & b)/max(1,len(a | b))

def first_nonempty(*vals):
    for v in vals:
        if known(v): return v
    return ''

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
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
    # Compatibility with older SQLite databases that only had the original 10 fields.
    extra={
        'fecha_expedicion_vigencia':'TEXT','fecha_expedicion_vigencia_prorroga':'TEXT','forma_farmaceutica':'TEXT',
        'indicaciones_terapeuticas':'TEXT','contra_indicaciones':'TEXT','vida_util':'TEXT','fraccion':'TEXT',
        'denominacion_generica':'TEXT','vista_administracion':'TEXT','tipo_medicamento':'TEXT','cantidad':'TEXT',
        'sistema_organico':'TEXT','grupo_farmacologico':'TEXT','subgrupo_farmacologico':'TEXT','subgrupo_quimico':'TEXT',
        'sustancia_quimica':'TEXT','titular':'TEXT','domicilio':'TEXT','fabricantes_medicamentos':'TEXT',
        'fabricantes_farmacos':'TEXT','acondicionado_por':'TEXT','acondicionado_extranjero':'TEXT','distribuidores':'TEXT',
        'unidad_farmaco_vigilancia':'TEXT','fecha_emision':'TEXT'
    }
    existing={r[1] for r in c.execute('PRAGMA table_info(records)').fetchall()}
    for col,typ in extra.items():
        if col not in existing:
            c.execute(f'ALTER TABLE records ADD COLUMN {col} {typ}')
    c.commit()
    seed_from_supplied_dataset(c)
    return c

def seed_from_supplied_dataset(c):
    """Carga el conjunto JSON suministrado para MediCheck como base inicial.
    No se presenta como una descarga completa ni como verificación independiente de COFEPRIS.
    """
    if not SEED_FILE.exists():
        return 0
    if c.execute('SELECT COUNT(*) FROM records').fetchone()[0] > 0:
        return 0
    data=json.loads(SEED_FILE.read_text(encoding='utf-8'))
    today=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    n=0
    for x in data:
        reg=norm(x.get('numero_registro'))
        status=norm(x.get('estado'))
        c.execute("""INSERT OR REPLACE INTO records
            (registry,name,active_ingredient,concentration,presentation,manufacturer,status,source_url,source_date,source_fragment)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (reg, x.get('denominacion_distintiva',''), x.get('principio_activo',''), '',
             x.get('denominacion_generica',''), x.get('titular_registro',''),
             'ACTIVE' if status=='VIGENTE' else status, COFEPRIS_PAGE, today, json.dumps(x,ensure_ascii=False)))
        if status in {'REVOCADO','CANCELADO'}:
            c.execute('INSERT OR REPLACE INTO statuses VALUES (?,?,?,?,?)',
                      (reg,status,f'El conjunto de datos suministrado indica estado {status}.',COFEPRIS_PAGE,today))
        n+=1
    c.execute('INSERT OR REPLACE INTO sync_sources VALUES (?,?,?,?,?,?,?,?)',
              ('supplied_seed','Conjunto COFEPRIS suministrado por el usuario',COFEPRIS_PAGE,'seed','2026',today,SEED_FILE.stat().st_size,n))
    c.commit()
    return n

def source_info():
    c=db(); rows=[dict(r) for r in c.execute('SELECT * FROM sync_sources ORDER BY key')];
    return {'official_page':COFEPRIS_PAGE,'viewer':COFEPRIS_VIEWER,'last_sync':max([r['last_sync'] for r in rows if r['last_sync']] or [None]),'sources':rows,'database':str(DB.name),'records':c.execute('SELECT COUNT(*) FROM records').fetchone()[0],'statuses':c.execute('SELECT COUNT(*) FROM statuses').fetchone()[0],'status_breakdown':[dict(r) for r in c.execute('SELECT status, COUNT(*) AS count FROM statuses GROUP BY status ORDER BY status')], 'record_status_breakdown':[dict(r) for r in c.execute('SELECT status, COUNT(*) AS count FROM records GROUP BY status ORDER BY status')]}

@app.get('/health')
def health():
    info=source_info(); return {'ok':True,'service':'medicheck-v43','gemini_configured':bool(os.getenv('GEMINI_API_KEY','').strip()),'gemini_model':GEMINI_MODEL,'gemini_models':GEMINI_MODELS,**info}
@app.get('/sources')
def sources(): return source_info()
@app.get('/gemini/status')
def gemini_status(): return {'configured':bool(os.getenv('GEMINI_API_KEY','').strip()),'model':GEMINI_MODEL,'models':GEMINI_MODELS,'api_key_location':'server_environment_only'}

@app.post('/cofepris/live')
def cofepris_live(req:VerifyRequest):
    """Busca en los 14,920 registros cargados y compara campos regulatorios."""
    c=db()
    fields={
        'registry': req.registry, 'name': req.name,
        'active_ingredient': req.active_ingredient, 'concentration': req.concentration,
        'presentation': req.presentation, 'manufacturer': req.manufacturer,
    }
    fields={k:(norm(v) if known(v) else '') for k,v in fields.items()}
    if not any(fields.values()):
        return {'status':'REVIEW','found':False,'confidence':0,'message':'Faltan datos identificables para consultar COFEPRIS.','source':COFEPRIS_PAGE,'viewer':COFEPRIS_VIEWER}
    total=c.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    if not total:
        return {'status':'SOURCE_UNAVAILABLE','found':None,'confidence':0,'message':'No hay registros COFEPRIS cargados en la base local.','source':COFEPRIS_PAGE,'viewer':COFEPRIS_VIEWER}
    # Registry is the strongest key. If supplied, only that registry is considered.
    if fields['registry']:
        rows=c.execute('SELECT * FROM records WHERE UPPER(registry)=?',(fields['registry'],)).fetchall()
    else:
        rows=c.execute('SELECT * FROM records').fetchall()
    candidates=[]
    weights={'registry':45,'name':25,'active_ingredient':15,'concentration':7,'presentation':4,'manufacturer':4}
    for row in rows:
        checks={}; points=0; possible=0
        rv={
            'registry':row['registry'], 'name':row['name'],
            'active_ingredient': ' '.join(x for x in [row['active_ingredient'], row['concentration']] if known(x)),
            'concentration': row['concentration'],
            'presentation': first_nonempty(row['presentation']),
            'manufacturer': first_nonempty(row['manufacturer'], row['titular'], row['fabricantes_medicamentos'])
        }
        for f,w in weights.items():
            q=fields[f]
            if not q: continue
            possible+=w
            ok=contains_match(q,rv[f])
            if not ok and f in {'name','active_ingredient','manufacturer'}:
                ok=token_score(q,rv[f])>=0.50
            checks[f]=ok
            if ok: points+=w
        conf=round(100*points/possible) if possible else 0
        # Prefer exact registry/name/active matches and complete field agreement.
        if fields['registry'] and norm(row['registry'])==fields['registry']: conf=max(conf,45)
        if fields['name'] and contains_match(fields['name'],row['name']): conf=max(conf,70 if not fields['registry'] else conf)
        if fields['active_ingredient'] and contains_match(fields['active_ingredient'],rv['active_ingredient']): conf=max(conf,75 if checks.get('name') else conf)
        candidates.append((conf,points,dict(row),checks))
    candidates.sort(key=lambda x:(x[0],x[1]),reverse=True)
    if not candidates:
        return {'status':'REVIEW','found':False,'confidence':0,'message':'No se encontró el registro solicitado en la base disponible. Esto no demuestra que el medicamento carezca de registro.','source':COFEPRIS_PAGE,'viewer':COFEPRIS_VIEWER}
    conf,points,row,checks=candidates[0]
    row_status=norm(row.get('status'))
    if row_status in {'REVOCADO','CANCELADO'}:
        status=row_status
    elif conf>=90:
        status='MATCH'
    else:
        status='REVIEW'
    return {
        'status':status,'found':True,'confidence':conf,
        'message':'Coincidencia contra la base COFEPRIS cargada. La coincidencia regulatoria no demuestra por sí sola la autenticidad física del envase.',
        'matched_record':row,'field_checks':checks,
        'source':row.get('source_url') or COFEPRIS_PAGE,
        'source_date':row.get('source_date',''),'viewer':COFEPRIS_VIEWER,
        'database_records':total
    }

@app.post('/verify')
def verify(req:VerifyRequest):
    """Verificación COFEPRIS con evidencia fuerte antes de declarar estados regulatorios."""
    c=db()
    reg=norm(req.registry) if known(req.registry) else ''
    # Registro exacto: es la vía más fuerte para localizar el producto.
    if reg:
        rows=c.execute('SELECT * FROM records WHERE UPPER(registry)=?',(reg,)).fetchall()
        if not rows:
            st=c.execute('SELECT * FROM statuses WHERE UPPER(registry)=?',(reg,)).fetchone()
            if st:
                return {'status':st['status'],'found':True,'confidence':100,'message':st['reason'] or 'El registro figura con ese estado en la fuente cargada.','field_checks':{'registry':True},'source':st['source_url'],'source_date':st['source_date']}
            return {'status':'NO_MATCH','found':False,'confidence':0,'message':'El número de registro no fue localizado en la base COFEPRIS cargada. Esto no demuestra por sí solo que el producto sea irregular. Se requiere corroboración adicional.','source':COFEPRIS_PAGE,'needs_external_confirmation':True}
    else:
        rows=c.execute('SELECT * FROM records').fetchall()
    if not rows:
        return {'status':'REVIEW','found':False,'confidence':0,'message':'La base COFEPRIS está vacía.','source':COFEPRIS_PAGE}

    weights={'registry':45,'name':25,'active_ingredient':15,'manufacturer':6,'concentration':5,'presentation':4}
    best=None
    for row in rows:
        vals={
            'registry':row['registry'], 'name':row['name'],
            'active_ingredient':' '.join(x for x in [row['active_ingredient'],row['concentration']] if known(x)),
            'manufacturer':first_nonempty(row['manufacturer'],row['titular'],row['fabricantes_medicamentos']),
            'concentration':row['concentration'],
            'presentation':row['presentation']
        }
        total=0; possible=0; checks={}
        for f,w in weights.items():
            q=getattr(req,f)
            if not known(q): continue
            possible+=w
            ok=contains_match(q,vals[f])
            if not ok and f in {'name','active_ingredient','manufacturer'}:
                ok=token_score(q,vals[f])>=0.50
            checks[f]=ok
            if ok: total+=w
        conf=round(100*total/possible) if possible else 0
        candidate=(conf,total,dict(row),checks)
        if best is None or candidate[:2]>best[:2]: best=candidate

    conf,total,row,checks=best
    state=norm(row.get('status'))
    has_registry=known(req.registry)
    name_ok=checks.get('name',False)
    active_ok=checks.get('active_ingredient',False)
    manufacturer_ok=checks.get('manufacturer',False)
    concentration_ok=checks.get('concentration',False)

    # Nunca declarar REVOCADO/CANCELADO por una coincidencia parcial cuando el
    # registro sanitario no fue identificado. Requiere evidencia fuerte del producto.
    strong_identity = name_ok and active_ok and (manufacturer_ok or concentration_ok)
    if state in {'REVOCADO','CANCELADO'} and (has_registry or strong_identity):
        status=state
    elif conf>=90 and strong_identity:
        status='MATCH'
    elif conf>=50:
        status='REVIEW'
    else:
        status='NO_MATCH'

    msg='Coincidencia calculada contra la base COFEPRIS cargada. Esto no demuestra autenticidad física del envase.'
    if state in {'REVOCADO','CANCELADO'} and not (has_registry or strong_identity):
        msg='Se encontró una coincidencia parcial con un registro cuyo estado es '+state+', pero la evidencia del producto es insuficiente para declarar ese estado. Se requiere identificación adicional.'
    return {'status':status,'found':True,'confidence':conf,'message':msg,'matched_record':row,'field_checks':checks,'source':row.get('source_url',COFEPRIS_PAGE),'source_date':row.get('source_date',''),'database_records':c.execute('SELECT COUNT(*) FROM records').fetchone()[0], 'evidence':{'registry_identified':has_registry,'strong_identity':strong_identity}, 'needs_external_confirmation': status=='NO_MATCH' and not has_registry}

def gemini_analyze(req):
    key=os.getenv('GEMINI_API_KEY','').strip()
    if not key: raise HTTPException(status_code=503,detail='Gemini no está configurado: agrega GEMINI_API_KEY como variable de entorno en el servidor. Nunca la pongas en el APK.')
    prompt='''Analiza esta fotografía de un envase de medicamento en México. Extrae SOLO información visible o razonablemente legible. No inventes. Usa "No identificado" si no es legible. Devuelve JSON con exactamente: commercial_name, active_ingredient, concentration, presentation, manufacturer, health_registration, lot, expiry, image_quality, visual_inconsistencies. No determines autenticidad física ni estatus regulatorio.'''
    if req.barcode: prompt+='\nCódigo escaneado: '+req.barcode
    if req.ocr_text: prompt+='\nOCR preliminar:\n'+req.ocr_text[:12000]
    schema={'type':'object','properties':{k:{'type':'string'} for k in ['commercial_name','active_ingredient','concentration','presentation','manufacturer','health_registration','lot','expiry','image_quality','visual_inconsistencies']},'required':['commercial_name','active_ingredient','concentration','presentation','manufacturer','health_registration','lot','expiry','image_quality','visual_inconsistencies']}
    # Gemini 3.x no usa temperature/top_p/top_k in this migration path.
    # Keep the request deterministic through the JSON schema instead.
    payload={'contents':[{'parts':[{'text':prompt},{'inline_data':{'mime_type':req.mime_type,'data':req.image_base64}}]}],
             'generationConfig':{'responseMimeType':'application/json','responseSchema':schema}}

    transient={404,429,500,502,503,504}
    failures=[]
    for model in GEMINI_MODELS:
        for attempt in range(1):
            try:
                r=requests.post(GEMINI_URL.format(model=model),params={'key':key},json=payload,timeout=18)
            except requests.RequestException as e:
                failures.append(f'{model}: network error: {e}')
                if attempt==0: continue
                break
            if r.status_code < 400:
                try:
                    data=r.json()
                    text=''.join(p.get('text','') for c in data.get('candidates',[]) for p in c.get('content',{}).get('parts',[]))
                except Exception as e:
                    failures.append(f'{model}: invalid API response: {e}')
                    break
                if not text:
                    failures.append(f'{model}: Gemini no devolvió JSON.')
                    break
                try:
                    analysis=json.loads(text.replace('```json','').replace('```','').strip())
                    analysis['_gemini_model']=model
                    return analysis
                except Exception as e:
                    failures.append(f'{model}: JSON inválido: {e}')
                    break
            detail=r.text[:700].replace('\n',' ')
            failures.append(f'{model}: HTTP {r.status_code}: {detail}')
            if r.status_code in transient and r.status_code in {429,500,502,503,504}:
                # El siguiente modelo actúa como fallback; evitamos cadenas de reintentos largos.
                break
            break

    # Do not convert an AI outage into a fake COFEPRIS NO_MATCH/0 confidence.
    raise HTTPException(status_code=503,detail='El servicio de análisis Gemini está temporalmente no disponible. No se realizó una verificación COFEPRIS fiable. Intenta nuevamente en unos minutos. Modelos probados: '+', '.join(GEMINI_MODELS)+'. Detalles: '+' | '.join(failures[-6:]))

@app.post('/gemini/analyze')
def gemini_endpoint(req:GeminiRequest): return {'ok':True,'model':GEMINI_MODEL,'models':GEMINI_MODELS,'analysis':gemini_analyze(req)}

@app.post('/analyze-and-verify')
def analyze_and_verify(req:AnalyzeVerifyRequest):
    if not req.image_base64: raise HTTPException(status_code=400,detail='Falta la fotografía.')
    c=db(); n=c.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    if n==0:
        try:
            from sync_cofepris import sync_official_sources
            sync_official_sources()
        except Exception as e:
            raise HTTPException(status_code=503,detail=f'No fue posible sincronizar COFEPRIS: {e}')
    a=gemini_analyze(req)
    vr=VerifyRequest(barcode=req.barcode,name=a.get('commercial_name',''),active_ingredient=a.get('active_ingredient',''),concentration=a.get('concentration',''),presentation=a.get('presentation',''),manufacturer=a.get('manufacturer',''),registry=a.get('health_registration',''),lot=a.get('lot',''),expiry=a.get('expiry',''))
    return {'ok':True,'workflow':'photo->gemini->cofepris->match','gemini':a,'verification':verify(vr)}

@app.post('/sync')
def sync():
    try:
        from sync_cofepris import sync_official_sources
        return sync_official_sources()
    except Exception as e: raise HTTPException(status_code=500,detail=str(e))
