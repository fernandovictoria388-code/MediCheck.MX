from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path
import os, json, base64, sqlite3, requests, re, datetime

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except Exception:
    pass

app = FastAPI(title='MediCheck MX Verification API', version='0.33')
BASE = Path(__file__).parent
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)
DB = DATA / 'cofepris.sqlite3'
COFEPRIS_PAGE='https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos'
COFEPRIS_VIEWER='https://registros.cofepris.gob.mx/BRSDM/default.aspx'
GEMINI_MODEL=os.getenv('GEMINI_MODEL','gemini-3.8-flash')
GEMINI_URL='https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
LIVE_SEARCH_ENABLED=os.getenv('COFEPRIS_LIVE_SEARCH','1').strip().lower() not in {'0','false','no','off'}

class VerifyRequest(BaseModel):
    barcode:str=''; name:str=''; active_ingredient:str=''; concentration:str=''; presentation:str=''; manufacturer:str=''; registry:str=''; lot:str=''; expiry:str=''
class AnalyzeVerifyRequest(BaseModel):
    image_base64:str; mime_type:str='image/jpeg'; barcode:str=''; ocr_text:str=''
class GeminiRequest(AnalyzeVerifyRequest): pass


def norm(v): return re.sub(r'\s+',' ',str(v or '').upper().strip())
def known(v): return bool(v and norm(v) not in {'NO IDENTIFICADO','N/A','NULL','NONE',''})


def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS records (registry TEXT PRIMARY KEY,name TEXT,active_ingredient TEXT,concentration TEXT,presentation TEXT,manufacturer TEXT,status TEXT DEFAULT 'ACTIVE',source_url TEXT,source_date TEXT,source_fragment TEXT)''')
    try: c.execute('ALTER TABLE records ADD COLUMN source_fragment TEXT')
    except sqlite3.OperationalError: pass
    c.execute('''CREATE TABLE IF NOT EXISTS statuses (registry TEXT PRIMARY KEY,status TEXT,reason TEXT,source_url TEXT,source_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sync_sources (key TEXT PRIMARY KEY,label TEXT,url TEXT,kind TEXT,year TEXT,last_sync TEXT,bytes INTEGER,record_count INTEGER)''')
    c.commit(); return c


def source_info():
    c=db(); rows=[dict(r) for r in c.execute('SELECT * FROM sync_sources ORDER BY key')]
    return {'official_page':COFEPRIS_PAGE,'viewer':COFEPRIS_VIEWER,'last_sync':max([r['last_sync'] for r in rows if r['last_sync']] or [None]),'sources':rows,'database':str(DB.name),'records':c.execute('SELECT COUNT(*) FROM records').fetchone()[0],'statuses':c.execute('SELECT COUNT(*) FROM statuses').fetchone()[0],'status_breakdown':[dict(r) for r in c.execute('SELECT status, COUNT(*) AS count FROM statuses GROUP BY status ORDER BY status')]}


@app.get('/')
def root():
    return {'ok':True,'service':'MediCheck MX','version':'0.33','message':'API activa','health':'/health'}

@app.get('/health')
def health():
    info=source_info(); return {'ok':True,'service':'medicheck-v33','gemini_configured':bool(os.getenv('GEMINI_API_KEY','').strip()),'gemini_model':GEMINI_MODEL,'cofepris_live_search':LIVE_SEARCH_ENABLED,**info}
@app.get('/sources')
def sources(): return source_info()
@app.get('/gemini/status')
def gemini_status(): return {'configured':bool(os.getenv('GEMINI_API_KEY','').strip()),'model':GEMINI_MODEL,'api_key_location':'server_environment_only','cofepris_live_search':LIVE_SEARCH_ENABLED}


def gemini_post(payload, timeout=90):
    key=os.getenv('GEMINI_API_KEY','').strip()
    if not key:
        raise HTTPException(status_code=503,detail='Gemini no está configurado: agrega GEMINI_API_KEY como variable de entorno en el servidor. Nunca la pongas en el APK.')
    r=requests.post(GEMINI_URL.format(model=GEMINI_MODEL),headers={'x-goog-api-key':key,'Content-Type':'application/json'},json=payload,timeout=timeout)
    if r.status_code>=400: raise HTTPException(status_code=502,detail=f'Gemini HTTP {r.status_code}: {r.text[:1200]}')
    return r.json()


def response_text(data):
    return ''.join(p.get('text','') for c in data.get('candidates',[]) for p in c.get('content',{}).get('parts',[]))


def grounding_sources(data):
    out=[]; seen=set()
    gm=(data.get('candidates') or [{}])[0].get('groundingMetadata') or {}
    chunks=gm.get('groundingChunks') or []
    for ch in chunks:
        web=ch.get('web') or {}
        url=web.get('uri') or web.get('url')
        title=web.get('title') or ''
        if url and url not in seen:
            seen.add(url); out.append({'url':url,'title':title})
    return out


def cofepris_live_lookup(req:VerifyRequest):
    """Consulta COFEPRIS en tiempo real mediante Gemini + Google Search grounding.
    La búsqueda se restringe en la instrucción a dominios oficiales de COFEPRIS.
    Si no existe evidencia explícita en una fuente oficial, no se inventa el dato.
    """
    if not LIVE_SEARCH_ENABLED:
        return None
    key=os.getenv('GEMINI_API_KEY','').strip()
    if not key: return None
    terms=[]
    if known(req.registry): terms.append('Número de registro sanitario: '+req.registry)
    if known(req.name): terms.append('Denominación distintiva: '+req.name)
    if known(req.active_ingredient): terms.append('Principio activo: '+req.active_ingredient)
    if known(req.manufacturer): terms.append('Fabricante: '+req.manufacturer)
    if not terms: return None
    query='\n'.join(terms)
    prompt='''Actúa como verificador documental de COFEPRIS México. Debes consultar información pública ACTUAL usando Google Search, pero SOLO puedes aceptar evidencia de dominios oficiales de COFEPRIS: registros.cofepris.gob.mx y www.gob.mx/cofepris (incluidos subdominios oficiales de cofepris.gob.mx).\n\nBusca coincidencias exactas o inequívocas para el medicamento indicado. Prioriza el Visor de Registros de Medicamentos de COFEPRIS y sus páginas de detalle. NO uses farmacias, blogs, Wikipedia, redes sociales ni otros sitios como evidencia.\n\nReglas estrictas:\n1) No inventes ningún campo.\n2) Si un campo no aparece explícitamente en una fuente oficial, devuelve "No identificado".\n3) Un registro encontrado NO demuestra que el envase físico sea auténtico.\n4) Distingue registro vigente de cancelado/revocado si la fuente lo indica.\n5) Si no encuentras una coincidencia oficial suficiente, found=false.\n6) Devuelve SOLO JSON válido con los campos solicitados.\n\nDatos proporcionados por la app:\n'''+query
    schema={'type':'object','properties':{
        'found':{'type':'boolean'},
        'status':{'type':'string'},
        'registry':{'type':'string'},
        'commercial_name':{'type':'string'},
        'active_ingredient':{'type':'string'},
        'concentration':{'type':'string'},
        'presentation':{'type':'string'},
        'manufacturer':{'type':'string'},
        'holder':{'type':'string'},
        'vigency':{'type':'string'},
        'evidence':{'type':'string'},
        'source_url':{'type':'string'}
    },'required':['found','status','registry','commercial_name','active_ingredient','concentration','presentation','manufacturer','holder','vigency','evidence','source_url']}
    payload={'contents':[{'parts':[{'text':prompt}]}],
             'tools':[{'google_search':{}}],
             'generationConfig':{'responseMimeType':'application/json','responseSchema':schema,'temperature':0.0}}
    try:
        data=gemini_post(payload,timeout=90)
        text=response_text(data)
        if not text: return None
        clean=text.replace('```json','').replace('```','').strip()
        result=json.loads(clean)
        result['_grounding_sources']=grounding_sources(data)
        # Keep only official sources as accepted evidence.
        official=[]
        for s in result.get('_grounding_sources',[]):
            u=s.get('url','')
            if re.search(r'(^https?://)?([^/]*\.)?cofepris\.gob\.mx(?:/|$)',u,re.I) or 'gob.mx/cofepris' in u.lower():
                official.append(s)
        result['_grounding_sources']=official
        if result.get('source_url') and not (re.search(r'cofepris\.gob\.mx',result['source_url'],re.I) or 'gob.mx/cofepris' in result['source_url'].lower()):
            result['source_url']=''
        return result
    except (ValueError, json.JSONDecodeError):
        return None
    except HTTPException:
        return None
    except Exception:
        return None


@app.post('/cofepris/live')
def cofepris_live(req:VerifyRequest):
    result=cofepris_live_lookup(req)
    if result is None:
        return {'ok':False,'found':False,'message':'No se obtuvo evidencia oficial suficiente mediante la consulta en tiempo real.','viewer':COFEPRIS_VIEWER}
    return {'ok':True,'cofepris':result,'viewer':COFEPRIS_VIEWER}


def local_verify(req:VerifyRequest):
    c=db(); reg=norm(req.registry)
    if known(req.registry):
        st=c.execute('SELECT * FROM statuses WHERE registry=?',(reg,)).fetchone()
        if st:
            return {'status':st['status'],'confidence':100,'message':st['reason'] or 'El registro figura como no vigente en la fuente pública sincronizada.','field_checks':{'registry':True},'source':st['source_url'],'source_date':st['source_date']}
    rows=c.execute('SELECT * FROM records').fetchall()
    if not rows: return None
    weights={'registry':45,'name':15,'active_ingredient':15,'manufacturer':10,'concentration':8,'presentation':7}
    best=None
    for row in rows:
        if known(req.registry) and norm(row['registry'])!=reg: continue
        total=possible=0; checks={}
        for field in weights:
            rv=getattr(req,field); dv=row[field] or ''
            if not dv and field in {'name','active_ingredient','manufacturer','concentration','presentation'}: dv=row['source_fragment'] or ''
            if known(rv) and dv:
                possible+=weights[field]; ok=norm(rv)==norm(dv)
                if not ok and field in {'name','active_ingredient','manufacturer','presentation'}: ok=norm(rv) in norm(dv) or norm(dv) in norm(rv)
                checks[field]=ok
                if ok: total+=weights[field]
        conf=round(100*total/possible) if possible else 0
        if best is None or conf>best[0]: best=(conf,dict(row),checks)
    if best is None: return None
    conf,row,checks=best
    status='MATCH' if conf>=90 else ('REVIEW' if conf>=50 else 'NO_MATCH')
    return {'status':status,'confidence':conf,'message':'Coincidencia calculada contra registros públicos sincronizados desde COFEPRIS. Esto no demuestra autenticidad física del envase.','matched_record':row,'field_checks':checks,'source':row.get('source_url',COFEPRIS_PAGE),'source_date':row.get('source_date',''),'mode':'local_database'}


def merge_live_verification(req, live):
    if not live: return None
    found=bool(live.get('found'))
    if not found:
        return {'status':'NO_MATCH','confidence':0,'message':'No se encontró evidencia oficial suficiente para una coincidencia en la consulta en tiempo real de COFEPRIS. Esto no demuestra por sí solo que el producto físico sea falso.','mode':'cofepris_live_search','cofepris':live,'source':COFEPRIS_VIEWER}
    checks={}
    pairs=[('registry','registry'),('name','commercial_name'),('active_ingredient','active_ingredient'),('concentration','concentration'),('presentation','presentation'),('manufacturer','manufacturer')]
    weights={'registry':45,'name':15,'active_ingredient':15,'manufacturer':10,'concentration':8,'presentation':7}
    total=possible=0
    for reqf,livef in pairs:
        rv=getattr(req,reqf); dv=live.get(livef,'')
        if known(rv) and known(dv):
            possible+=weights[reqf]
            ok=norm(rv)==norm(dv)
            if not ok and reqf in {'name','active_ingredient','manufacturer','presentation'}: ok=norm(rv) in norm(dv) or norm(dv) in norm(rv)
            checks[reqf]=ok
            if ok: total+=weights[reqf]
    conf=round(100*total/possible) if possible else 0
    raw_status=norm(live.get('status',''))
    if raw_status in {'CANCELADO','CANCELLED'}: status='CANCELLED'
    elif raw_status in {'REVOCADO','REVOKED'}: status='REVOKED'
    elif conf>=90: status='MATCH'
    elif conf>=50: status='REVIEW'
    else: status='NO_MATCH'
    return {'status':status,'confidence':conf,'message':'Coincidencia contrastada con evidencia obtenida en tiempo real desde fuentes oficiales de COFEPRIS. Un registro coincidente no demuestra autenticidad física del envase.','mode':'cofepris_live_search','field_checks':checks,'cofepris':live,'source':live.get('source_url') or COFEPRIS_VIEWER}


@app.post('/verify')
def verify(req:VerifyRequest):
    local=local_verify(req)
    # Prefer a direct live official check when a registry/name is available. This avoids
    # depending on the broken/blocked gob.mx document index for individual verifications.
    live=cofepris_live_lookup(req)
    merged=merge_live_verification(req,live)
    if merged and (merged.get('status') in {'MATCH','CANCELLED','REVOKED'} or local is None or merged.get('confidence',0)>=50):
        return merged
    if local: return local
    return {'status':'REVIEW','confidence':0,'message':'No hay una coincidencia local y la consulta oficial en tiempo real no aportó evidencia suficiente.','source':COFEPRIS_VIEWER,'mode':'review'}


def gemini_analyze(req):
    prompt='''Analiza esta fotografía de un envase de medicamento en México. Extrae SOLO información visible o razonablemente legible. No inventes. Usa "No identificado" si no es legible. Devuelve JSON con exactamente: commercial_name, active_ingredient, concentration, presentation, manufacturer, health_registration, lot, expiry, image_quality, visual_inconsistencies. No determines autenticidad física ni estatus regulatorio.'''
    if req.barcode: prompt+='\nCódigo escaneado: '+req.barcode
    if req.ocr_text: prompt+='\nOCR preliminar:\n'+req.ocr_text[:12000]
    schema={'type':'object','properties':{k:{'type':'string'} for k in ['commercial_name','active_ingredient','concentration','presentation','manufacturer','health_registration','lot','expiry','image_quality','visual_inconsistencies']},'required':['commercial_name','active_ingredient','concentration','presentation','manufacturer','health_registration','lot','expiry','image_quality','visual_inconsistencies']}
    payload={'contents':[{'parts':[{'text':prompt},{'inline_data':{'mime_type':req.mime_type,'data':req.image_base64}}]}],'generationConfig':{'responseMimeType':'application/json','responseSchema':schema,'temperature':0.1}}
    data=gemini_post(payload,timeout=90)
    text=response_text(data)
    if not text: raise HTTPException(status_code=502,detail='Gemini no devolvió JSON.')
    try: return json.loads(text.replace('```json','').replace('```','').strip())
    except Exception as e: raise HTTPException(status_code=502,detail=f'JSON de Gemini inválido: {e}')


@app.post('/gemini/analyze')
def gemini_endpoint(req:GeminiRequest): return {'ok':True,'model':GEMINI_MODEL,'analysis':gemini_analyze(req)}

@app.post('/analyze-and-verify')
def analyze_and_verify(req:AnalyzeVerifyRequest):
    if not req.image_base64: raise HTTPException(status_code=400,detail='Falta la fotografía.')
    # No force a full database synchronization here. COFEPRIS's public document index
    # can be unavailable while the official live viewer/search remains available.
    a=gemini_analyze(req)
    vr=VerifyRequest(barcode=req.barcode,name=a.get('commercial_name',''),active_ingredient=a.get('active_ingredient',''),concentration=a.get('concentration',''),presentation=a.get('presentation',''),manufacturer=a.get('manufacturer',''),registry=a.get('health_registration',''),lot=a.get('lot',''),expiry=a.get('expiry',''))
    verification=verify(vr)
    return {'ok':True,'workflow':'photo->gemini->cofepris-live-or-local->match','gemini':a,'verification':verification}

@app.post('/sync')
def sync():
    try:
        from sync_cofepris import sync_official_sources
        return sync_official_sources()
    except Exception as e: raise HTTPException(status_code=500,detail=str(e))
