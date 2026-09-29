from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path
import os, json, base64, sqlite3, requests, re, datetime

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except Exception:
    pass

app = FastAPI(title='MediCheck MX Verification API', version='0.38')
BASE = Path(__file__).parent
DATA = BASE / 'data'
DATA.mkdir(exist_ok=True)
DB = DATA / 'cofepris.sqlite3'
COFEPRIS_PAGE='https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos'
COFEPRIS_VIEWER='https://registros.cofepris.gob.mx/BRSDM/default.aspx'
GEMINI_MODEL=os.getenv('GEMINI_MODEL','gemini-3.8-flash')
GEMINI_URL='https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
SEED_FILE = BASE / 'seed_cofepris.json'

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
    return {'official_page':COFEPRIS_PAGE,'viewer':COFEPRIS_VIEWER,'last_sync':max([r['last_sync'] for r in rows if r['last_sync']] or [None]),'sources':rows,'database':str(DB.name),'records':c.execute('SELECT COUNT(*) FROM records').fetchone()[0],'statuses':c.execute('SELECT COUNT(*) FROM statuses').fetchone()[0],'status_breakdown':[dict(r) for r in c.execute('SELECT status, COUNT(*) AS count FROM statuses GROUP BY status ORDER BY status')]}

@app.get('/health')
def health():
    info=source_info(); return {'ok':True,'service':'medicheck-v38','gemini_configured':bool(os.getenv('GEMINI_API_KEY','').strip()),'gemini_model':GEMINI_MODEL,**info}
@app.get('/sources')
def sources(): return source_info()
@app.get('/gemini/status')
def gemini_status(): return {'configured':bool(os.getenv('GEMINI_API_KEY','').strip()),'model':GEMINI_MODEL,'api_key_location':'server_environment_only'}

@app.post('/cofepris/live')
def cofepris_live(req:VerifyRequest):
    """Consulta la copia local disponible; no intenta automatizar el CAPTCHA del Visor."""
    c=db()
    reg=norm(req.registry) if known(req.registry) else ''
    name=norm(req.name) if known(req.name) else ''
    active=norm(req.active_ingredient) if known(req.active_ingredient) else ''
    rows=c.execute('SELECT * FROM records').fetchall()
    if not rows:
        return {'status':'SOURCE_UNAVAILABLE','found':None,'confidence':0,'message':'No hay registros COFEPRIS cargados en la base local.','source':COFEPRIS_PAGE,'viewer':COFEPRIS_VIEWER}
    candidates=[]
    for row in rows:
        score=0; checks={}
        if reg:
            ok=norm(row['registry'])==reg; checks['registry']=ok; score += 60 if ok else 0
        if name:
            rn=norm(row['name']); ok=name==rn or name in rn or rn in name; checks['name']=ok; score += 25 if ok else 0
        if active:
            ra=norm(row['active_ingredient']); ok=active in ra or ra in active; checks['active_ingredient']=ok; score += 15 if ok else 0
        if (reg or name or active) and score>0:
            candidates.append((score,dict(row),checks))
    candidates.sort(key=lambda x:x[0],reverse=True)
    if not candidates:
        return {'status':'REVIEW','found':False,'confidence':0,'message':'No se encontró coincidencia suficiente en la copia local disponible. Esto no demuestra que el medicamento carezca de registro.','source':COFEPRIS_PAGE,'viewer':COFEPRIS_VIEWER}
    score,row,checks=candidates[0]
    row_status=norm(row.get('status'))
    if row_status in {'REVOCADO','CANCELADO'}:
        status=row_status
    elif score>=90:
        status='MATCH'
    else:
        status='REVIEW'
    return {'status':status,'found':True,'confidence':score,'message':'Coincidencia contra la copia local del conjunto COFEPRIS cargado. No demuestra autenticidad física del envase.','matched_record':row,'field_checks':checks,'source':row.get('source_url') or COFEPRIS_PAGE,'source_date':row.get('source_date',''),'viewer':COFEPRIS_VIEWER}

@app.post('/verify')
def verify(req:VerifyRequest):
    c=db(); reg=norm(req.registry)
    if known(req.registry):
        st=c.execute('SELECT * FROM statuses WHERE registry=?',(reg,)).fetchone()
        if st:
            return {'status':st['status'],'confidence':100,'message':st['reason'] or 'El registro figura como no vigente en la fuente pública sincronizada.','field_checks':{'registry':True},'source':st['source_url'],'source_date':st['source_date']}
    rows=c.execute('SELECT * FROM records').fetchall()
    if not rows: return {'status':'REVIEW','confidence':0,'message':'La base COFEPRIS todavía no está sincronizada. Ejecuta /sync.','source':COFEPRIS_PAGE}
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
    conf,row,checks=best
    status='MATCH' if conf>=90 else ('REVIEW' if conf>=50 else 'NO_MATCH')
    return {'status':status,'confidence':conf,'message':'Coincidencia calculada contra registros públicos sincronizados desde COFEPRIS. Esto no demuestra autenticidad física del envase.','matched_record':row,'field_checks':checks,'source':row.get('source_url',COFEPRIS_PAGE),'source_date':row.get('source_date','')}

def gemini_analyze(req):
    key=os.getenv('GEMINI_API_KEY','').strip()
    if not key: raise HTTPException(status_code=503,detail='Gemini no está configurado: agrega GEMINI_API_KEY como variable de entorno en el servidor. Nunca la pongas en el APK.')
    prompt='''Analiza esta fotografía de un envase de medicamento en México. Extrae SOLO información visible o razonablemente legible. No inventes. Usa "No identificado" si no es legible. Devuelve JSON con exactamente: commercial_name, active_ingredient, concentration, presentation, manufacturer, health_registration, lot, expiry, image_quality, visual_inconsistencies. No determines autenticidad física ni estatus regulatorio.'''
    if req.barcode: prompt+='\nCódigo escaneado: '+req.barcode
    if req.ocr_text: prompt+='\nOCR preliminar:\n'+req.ocr_text[:12000]
    schema={'type':'object','properties':{k:{'type':'string'} for k in ['commercial_name','active_ingredient','concentration','presentation','manufacturer','health_registration','lot','expiry','image_quality','visual_inconsistencies']},'required':['commercial_name','active_ingredient','concentration','presentation','manufacturer','health_registration','lot','expiry','image_quality','visual_inconsistencies']}
    payload={'contents':[{'parts':[{'text':prompt},{'inline_data':{'mime_type':req.mime_type,'data':req.image_base64}}]}],'generationConfig':{'responseMimeType':'application/json','responseSchema':schema,'temperature':0.1}}
    r=requests.post(GEMINI_URL.format(model=GEMINI_MODEL),params={'key':key},json=payload,timeout=90)
    if r.status_code>=400: raise HTTPException(status_code=502,detail=f'Gemini HTTP {r.status_code}: {r.text[:1000]}')
    data=r.json(); text=''.join(p.get('text','') for c in data.get('candidates',[]) for p in c.get('content',{}).get('parts',[]))
    if not text: raise HTTPException(status_code=502,detail='Gemini no devolvió JSON.')
    try: analysis=json.loads(text.replace('```json','').replace('```','').strip())
    except Exception as e: raise HTTPException(status_code=502,detail=f'JSON de Gemini inválido: {e}')
    return analysis

@app.post('/gemini/analyze')
def gemini_endpoint(req:GeminiRequest): return {'ok':True,'model':GEMINI_MODEL,'analysis':gemini_analyze(req)}

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
