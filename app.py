from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pathlib import Path
import os, json, base64, sqlite3, requests, re, datetime

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env')
except Exception:
    pass

app = FastAPI(title='MediCheck MX Verification API', version='0.49')
BASE = Path(__file__).parent
DATA = BASE
DB = BASE / 'cofepris.sqlite3'
COFEPRIS_PAGE='https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos'
COFEPRIS_VIEWER='https://registros.cofepris.gob.mx/BRSDM/default.aspx'
SEED_FILE = BASE / 'seed_cofepris.json'

class VerifyRequest(BaseModel):
    barcode:str=''; name:str=''; active_ingredient:str=''; concentration:str=''; presentation:str=''; manufacturer:str=''; registry:str=''; lot:str=''; expiry:str=''
class AnalyzeVerifyRequest(BaseModel):
    image_base64:str; mime_type:str='image/jpeg'; barcode:str=''; ocr_text:str=''
class GeminiRequest(AnalyzeVerifyRequest): pass
class BarcodeLookupRequest(BaseModel):
    barcode:str

import unicodedata

def norm(v):
    """Normalización conservadora: acentos/espacios/case sin alterar la evidencia original."""
    s=str(v or '').upper().strip()
    s=''.join(ch for ch in unicodedata.normalize('NFKD',s) if not unicodedata.combining(ch))
    s=s.replace('—','-').replace('–','-').replace('：',':')
    return re.sub(r'\s+',' ',s)

def ocr_norm(v):
    """Normaliza errores comunes de OCR solo para comparar; no modifica el texto mostrado."""
    s=norm(v)
    s=re.sub(r'\s*([/-])\s*',r'\1',s)
    # Correcciones seguras de contexto para unidades/números.
    s=re.sub(r'(?<=\d)O(?=\d|\s|MG|MCG|ML|G|UI|%)','0',s)
    s=re.sub(r'(?<=\d)I(?=\d|\s|MG|MCG|ML|G|UI|%)','1',s)
    s=re.sub(r'(?<=\d)L(?=\d|\s|MG|MCG|ML|G|UI|%)','1',s)
    return re.sub(r'\s+',' ',s).strip()

def known(v): return bool(v and norm(v) not in {'NO IDENTIFICADO','N/A','NULL','NONE','SIN DATOS',''})

def compact(v): return re.sub(r'[^A-Z0-9]+','',ocr_norm(v))

def levenshtein(a,b):
    a,b=compact(a),compact(b)
    if not a or not b: return 0 if a==b else 999
    if len(a)>len(b): a,b=b,a
    prev=list(range(len(a)+1))
    for j,cb in enumerate(b,1):
        cur=[j]
        for i,ca in enumerate(a,1):
            cur.append(min(cur[-1]+1,prev[i]+1,prev[i-1]+(ca!=cb)))
        prev=cur
    return prev[-1]

def edit_similarity(a,b):
    a,b=compact(a),compact(b)
    if not a or not b: return 0.0
    if a==b: return 1.0
    d=levenshtein(a,b)
    return max(0.0,1.0-d/max(len(a),len(b)))

def token_score(a,b):
    aa=set(re.findall(r'[A-Z0-9]+',ocr_norm(a))); bb=set(re.findall(r'[A-Z0-9]+',ocr_norm(b)))
    if not aa or not bb: return 0.0
    return len(aa & bb)/max(1,len(aa | bb))

def field_similarity(a,b):
    if not known(a) or not known(b): return 0.0
    aa,bb=ocr_norm(a),ocr_norm(b)
    if aa==bb or compact(aa)==compact(bb): return 1.0
    if aa in bb or bb in aa: return 0.96
    return max(edit_similarity(aa,bb), token_score(aa,bb))

def parse_concentration(v):
    if not known(v): return None
    s=ocr_norm(v).replace(',', '.')
    m=re.search(r'(\d+(?:\.\d+)?)\s*(MG|MCG|G|ML|UI|%)\s*(?:/\s*(ML|G|DOSIS))?',s)
    if not m: return None
    value=float(m.group(1)); unit=m.group(2); per=m.group(3) or ''
    # Convert mass units to mg; leave volume/UI/% in native units.
    if unit=='G': value*=1000; unit='MG'
    return (round(value,6),unit,per)

def concentration_match(a,b):
    pa,pb=parse_concentration(a),parse_concentration(b)
    if pa and pb: return pa==pb
    return field_similarity(a,b)>=0.94

def contains_match(a,b): return field_similarity(a,b)>=0.94

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


# COFEPRIS v47: refresh in background so the API can keep serving the last
# known-good database while an official refresh is running. The refresh is
# opt-in via AUTO_SYNC=1 and throttled by AUTO_SYNC_HOURS.
_SYNC_STARTED=False
def _background_cofepris_sync():
    global _SYNC_STARTED
    if _SYNC_STARTED or os.getenv('AUTO_SYNC','0').strip().lower() not in {'1','true','yes'}:
        return
    _SYNC_STARTED=True
    def worker():
        try:
            from sync_cofepris import sync_if_due
            sync_if_due()
        except Exception as exc:
            print('COFEPRIS_AUTO_SYNC_ERROR:', exc, flush=True)
    import threading
    threading.Thread(target=worker, name='cofepris-auto-sync', daemon=True).start()

_background_cofepris_sync()

@app.get('/health')
def health():
    info=source_info(); return {'ok':True,'service':'medicheck-v48-no-gemini','gemini_configured':False,'gemini_removed':True,**info}
@app.get('/sources')
def sources(): return source_info()
@app.get('/gemini/status')
def gemini_status():
    return {'configured':False,'disabled':True,'message':'Gemini fue eliminado del motor de verificación. MediCheck MX usa OCR, coincidencia local y COFEPRIS.'}


def local_barcode_lookup(barcode):
    code=re.sub(r'\s+','',str(barcode or ''))
    if len(code)<4:
        raise HTTPException(status_code=400,detail='Código de barras demasiado corto.')
    c=db()
    # El GTIN no es un registro COFEPRIS. Solo buscamos el código si alguna fuente
    # suministrada lo contiene explícitamente; nunca inferimos el producto.
    rows=c.execute('SELECT * FROM records WHERE source_fragment LIKE ?',('%'+code+'%',)).fetchall()
    if rows:
        row=dict(rows[0])
        return {'ok':True,'barcode':code,'found':True,'identification':{'commercial_name':row.get('name',''),'active_ingredient':row.get('active_ingredient',''),'concentration':row.get('concentration',''),'presentation':row.get('presentation',''),'manufacturer':row.get('manufacturer',''),'health_registration':row.get('registry',''),'match_confidence':100},'verification':verify(VerifyRequest(barcode=code,name=row.get('name',''),active_ingredient=row.get('active_ingredient',''),concentration=row.get('concentration',''),presentation=row.get('presentation',''),manufacturer=row.get('manufacturer',''),registry=row.get('registry','')))}
    return {'ok':True,'barcode':code,'found':False,'identification':None,'verification':{'status':'REVIEW','found':False,'confidence':0,'message':'El código de barras fue leído. Un GTIN no es un registro COFEPRIS y no se usa para inventar una identificación. Toma una foto del envase para que el OCR local extraiga nombre, principio activo, concentración y fabricante.','needs_photo':True,'source':COFEPRIS_PAGE,'viewer':COFEPRIS_VIEWER}}

@app.post('/barcode-lookup')
def barcode_lookup(req:BarcodeLookupRequest):
    return local_barcode_lookup(req.barcode)

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
    """Motor COFEPRIS robusto: fuzzy para candidatos, reglas estrictas para verificar."""
    c=db(); rows=[]
    reg=ocr_norm(req.registry) if known(req.registry) else ''
    if reg:
        rows=c.execute('SELECT * FROM records WHERE UPPER(registry)=?',(norm(req.registry),)).fetchall()
        if not rows:
            st=c.execute('SELECT * FROM statuses WHERE UPPER(registry)=?',(norm(req.registry),)).fetchone()
            if st:
                return {'status':'REVIEW','found':True,'confidence':55,'message':'El registro fue mencionado pero no se localizó un registro completo en la base cargada. Se requiere corroboración adicional.','field_checks':{'registry':False},'source':st['source_url'],'source_date':st['source_date'],'evidence':{'registry_identified':True,'registry_found':False}}
            return {'status':'REVIEW','found':False,'confidence':0,'message':'El número de registro no fue localizado en la base COFEPRIS cargada. Esto no demuestra por sí solo que el producto sea irregular.','source':COFEPRIS_PAGE,'needs_external_confirmation':True,'evidence':{'registry_identified':True,'registry_found':False}}
    else:
        rows=c.execute('SELECT * FROM records').fetchall()
    if not rows:
        return {'status':'REVIEW','found':False,'confidence':0,'message':'La base COFEPRIS está vacía.','source':COFEPRIS_PAGE}

    queries={
        'name':req.name,'active_ingredient':req.active_ingredient,'concentration':req.concentration,
        'manufacturer':req.manufacturer,'presentation':req.presentation,'registry':req.registry
    }
    weights={'name':28,'active_ingredient':28,'concentration':20,'manufacturer':12,'presentation':7,'registry':5}
    candidates=[]
    for row in rows:
        vals={
          'name':row['name'],
          'active_ingredient':first_nonempty(row['active_ingredient'],row['sustancia_quimica'],row['denominacion_generica']),
          'concentration':row['concentration'],
          'manufacturer':first_nonempty(row['manufacturer'],row['titular'],row['fabricantes_medicamentos']),
          'presentation':first_nonempty(row['presentation'],row['forma_farmaceutica']),
          'registry':row['registry']
        }
        scores={}; checks={}; provided=[]
        for f,q in queries.items():
            if not known(q): continue
            provided.append(f)
            if f=='concentration': score=1.0 if concentration_match(q,vals[f]) else field_similarity(q,vals[f])
            else: score=field_similarity(q,vals[f])
            scores[f]=score
            # Candidate tolerance: name/active can survive a small OCR error; critical concentration is stricter.
            checks[f]= score>=({'name':0.82,'active_ingredient':0.82,'manufacturer':0.80,'presentation':0.78,'registry':0.98,'concentration':0.94}[f])
        if not provided: continue
        denom=sum(weights[f] for f in provided)
        raw=sum(weights[f]*scores[f] for f in provided)/denom*100 if denom else 0
        critical_conc = (not known(req.concentration)) or checks.get('concentration',False)
        identity_ok = checks.get('name',False) and checks.get('active_ingredient',False)
        # Barcode is only corroboration here; a GTIN is not a COFEPRIS registry.
        candidate=(round(raw), scores, checks, dict(row), critical_conc, identity_ok)
        candidates.append(candidate)
    if not candidates:
        return {'status':'NO_MATCH','found':False,'confidence':0,'message':'No hay datos suficientes para construir una coincidencia fiable.','source':COFEPRIS_PAGE}
    candidates.sort(key=lambda x:(x[0],sum(x[2].values())),reverse=True)
    best=candidates[0]
    conf,scores,checks,row,critical_conc,identity_ok=best
    second_conf=candidates[1][0] if len(candidates)>1 else 0
    margin=conf-second_conf
    state=norm(row.get('status'))
    if state=='ACTIVE': state='VIGENTE'
    registry_exact=bool(reg and norm(row.get('registry'))==norm(req.registry))
    contradiction=[]
    for f,label in [('name','nombre'),('active_ingredient','principio activo'),('manufacturer','fabricante'),('presentation','presentación')]:
        if known(queries[f]) and known(row.get(f)) and not checks.get(f,False): contradiction.append(label)
    if known(req.concentration) and known(row.get('concentration')) and not checks.get('concentration',False): contradiction.append('concentración')

    # Hard gates: no VERIFICADO when critical evidence is absent/conflicting.
    verified = registry_exact or (identity_ok and critical_conc and conf>=90 and margin>=8)
    if contradiction: verified=False
    if state in {'REVOCADO','CANCELADO'}:
        status=state if verified else 'REVIEW'
    elif verified and state=='VIGENTE':
        status='VERIFIED'
    elif conf>=72 and identity_ok and critical_conc:
        status='STRONG_MATCH'
    elif conf>=45:
        status='REVIEW'
    else:
        status='NO_MATCH'
    message='Coincidencia calculada contra la base COFEPRIS cargada. La coincidencia regulatoria no demuestra por sí sola la autenticidad física del envase.'
    if contradiction:
        message='Se encontró un candidato, pero existen inconsistencias en: '+', '.join(contradiction)+'. No se considera verificado.'
    elif status=='REVIEW':
        message='Hay una coincidencia parcial, pero la evidencia no alcanza el umbral de verificación. No se considera un resultado positivo.'
    elif status=='VERIFIED':
        message='Los campos críticos coinciden con un registro COFEPRIS cargado y el estado del registro es VIGENTE. Esto no demuestra por sí solo la autenticidad física del envase.'
    return {'status':status,'found':True,'confidence':conf,'margin_vs_next':margin,'message':message,'matched_record':row,'field_checks':checks,'field_scores':{k:round(v*100) for k,v in scores.items()},'contradictions':contradiction,'source':row.get('source_url') or COFEPRIS_PAGE,'source_date':row.get('source_date',''),'viewer':COFEPRIS_VIEWER,'database_records':len(rows),'evidence':{'registry_exact':registry_exact,'identity_fields_ok':identity_ok,'concentration_ok':critical_conc,'candidate_margin':margin,'verification_gate_passed':verified}}

def extract_ocr_fields(ocr_text):
    text=str(ocr_text or '')
    clean=norm(text)
    lines=[x.strip() for x in re.split(r'[\r\n]+',clean) if x.strip()]
    patterns={
      'registry':r'(?:REG(?:ISTRO)?\s*(?:SANITARIO)?|SSA)\s*[:#-]?\s*([A-Z0-9./-]{4,})',
      'concentration':r'\b(\d+(?:[.,]\d+)?\s*(?:MG|MCG|G|ML|UI|%)(?:\s*/\s*(?:ML|G|DOSIS))?)\b',
      'lot':r'(?:LOTE|LOT|L)\s*[:#-]?\s*([A-Z0-9-]{3,})',
      'expiry':r'(?:CAD(?:UCIDAD)?|EXP(?:IRY)?)\s*[:#-]?\s*([0-9]{1,2}[/-][0-9]{1,2}[/-][0-9]{2,4}|[0-9]{2,4}[/-][0-9]{1,2})'
    }
    def grab(key):
        m=re.search(patterns[key],clean); return m.group(1) if m else ''
    registry,concentration,lot,expiry=grab('registry'),grab('concentration'),grab('lot'),grab('expiry')
    manufacturer=active=presentation=''
    for line in lines:
        for key,labels in [('manufacturer',['FABRICANTE','LABORATORIO','TITULAR']),('active_ingredient',['PRINCIPIO ACTIVO','SUSTANCIA ACTIVA']),('presentation',['PRESENTACION','FORMA FARMACEUTICA'])]:
            if any(norm(line).startswith(norm(lbl)) for lbl in labels):
                val=re.sub(r'^[^:：-]*[:：-]?\s*','',line,flags=re.I).strip()
                if key=='manufacturer' and not manufacturer: manufacturer=val
                elif key=='active_ingredient' and not active: active=val
                elif key=='presentation' and not presentation: presentation=val
    candidates=[]
    stop=['PRINCIPIO ACTIVO','FABRICANTE','LABORATORIO','PRESENTACION','FORMA FARMACEUTICA','REGISTRO SANITARIO','LOTE','CADUCIDAD','TITULAR','SUSTANCIA ACTIVA']
    for line in lines:
        n=norm(line)
        if len(n)>=3 and not re.fullmatch(r'[0-9\W_]+',n) and not any(k in n for k in stop):
            if not re.search(r'\b(?:MG|MCG|G|ML|UI|%)\b',n) and not re.fullmatch(r'[A-Z0-9./-]{8,}',n): candidates.append(line)
    name=max(candidates,key=len)[:160] if candidates else ''
    return {'name':name,'active':active,'concentration':concentration,'presentation':presentation,'manufacturer':manufacturer,'registry':registry,'lot':lot,'expiry':expiry}

@app.post('/analyze-and-verify')
def analyze_and_verify(req:AnalyzeVerifyRequest):
    # Compatibilidad con versiones Android anteriores: la imagen se acepta, pero
    # NO se envía a una IA. La identificación regulatoria depende exclusivamente
    # del OCR local/código y de la base COFEPRIS.
    if not req.image_base64 and not req.ocr_text and not req.barcode:
        raise HTTPException(status_code=400,detail='Falta evidencia: fotografía, OCR o código de barras.')
    fields=extract_ocr_fields(req.ocr_text)
    vr=VerifyRequest(barcode=req.barcode,name=fields['name'],active_ingredient=fields['active'],concentration=fields['concentration'],presentation=fields['presentation'],manufacturer=fields['manufacturer'],registry=fields['registry'],lot=fields['lot'],expiry=fields['expiry'])
    verification=verify(vr)
    return {'ok':True,'workflow':'photo->local-ocr+barcode(optional)->cofepris->deterministic-match','identification':{'commercial_name':fields['name'],'active_ingredient':fields['active'],'concentration':fields['concentration'],'presentation':fields['presentation'],'manufacturer':fields['manufacturer'],'health_registration':fields['registry'],'lot':fields['lot'],'expiry':fields['expiry'],'identification_confidence':0 if not fields['name'] else 50,'method':'OCR local; sin Gemini'},'verification':verification}

@app.post('/gemini/analyze')
def disabled_gemini_endpoint(req:GeminiRequest):
    raise HTTPException(status_code=410,detail='Gemini fue eliminado de MediCheck MX. Usa /analyze-and-verify con OCR local o /verify.')

@app.post('/sync')
def sync():
    expected=os.getenv('SYNC_TOKEN','').strip()
    supplied=os.getenv('SYNC_TOKEN_HEADER_VALUE','').strip()
    # Backward compatible: if no token is configured, preserve the existing
    # manual endpoint. If SYNC_TOKEN is configured, require the caller to send
    # X-MediCheck-Sync-Token through a reverse proxy or protected client.
    if expected and supplied != expected:
        raise HTTPException(status_code=403,detail='Sin autorización para sincronizar COFEPRIS.')
    try:
        from sync_cofepris import sync_official_sources
        return sync_official_sources(force=True)
    except Exception as e: raise HTTPException(status_code=500,detail=str(e))
