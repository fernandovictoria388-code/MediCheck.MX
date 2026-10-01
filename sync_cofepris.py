from pathlib import Path
import requests, re, json, datetime, sqlite3, os, unicodedata, time, hashlib, threading
from bs4 import BeautifulSoup
from pypdf import PdfReader

BASE=Path(__file__).parent
DATA=BASE/'data'; RAW=DATA/'cofepris_raw'; ARCHIVE=DATA/'cofepris_archive'
RAW.mkdir(parents=True,exist_ok=True); ARCHIVE.mkdir(parents=True,exist_ok=True)
PAGE='https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos'
VIEWER='https://registros.cofepris.gob.mx/BRSDM/default.aspx'
PROXY_PAGE='https://r.jina.ai/https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos'
HEADERS={'User-Agent':'MediCheckMX/49 (COFEPRIS synchronization)','Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','Accept-Language':'es-MX,es;q=0.9,en;q=0.7','Cache-Control':'no-cache'}
_LOCK=threading.Lock()

def norm(v):
    s=unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().upper()
    return re.sub(r'\s+',' ',s.strip())

def years(env, default):
    raw=os.getenv(env,default)
    return [x.strip() for x in raw.split(',') if x.strip()]

def fetch_page():
    di=[]
    for url in (PAGE,PAGE+'?idiom=es'):
        try:
            r=requests.get(url,headers=HEADERS,timeout=60)
            di.append({'url':url,'status':r.status_code,'bytes':len(r.content)})
            if r.ok and len(r.text)>2000 and ('Registros_' in r.text or 'Visor de Registros' in r.text):
                return r.text,di,'official'
        except Exception as e: di.append({'url':url,'error':str(e)})
    try:
        r=requests.get(PROXY_PAGE,headers=HEADERS,timeout=90)
        di.append({'url':PROXY_PAGE,'status':r.status_code,'bytes':len(r.content)})
        if r.ok and len(r.text)>1000: return r.text,di,'proxy-discovery'
    except Exception as e: di.append({'url':PROXY_PAGE,'error':str(e)})
    raise RuntimeError('COFEPRIS_HTML_FETCH_FAILED '+json.dumps(di,ensure_ascii=False))

def absurl(h):
    h=(h or '').strip().strip('<>')
    if h.startswith('//'): return 'https:'+h
    if h.startswith('/'): return 'https://www.gob.mx'+h
    if re.match(r'^https?://',h,re.I): return h
    return 'https://www.gob.mx/'+h.lstrip('/')

def discover():
    text,diag,mode=fetch_page(); soup=BeautifulSoup(text,'html.parser'); c=[]
    for a in soup.find_all('a',href=True): c.append((' '.join(a.stripped_strings),absurl(a['href'])))
    for m in re.finditer(r'\[([^\]]+)\]\(([^)]+)\)',text): c.append((m.group(1).strip(),absurl(m.group(2))))
    for u in re.findall(r'https?://[^\s\)\]<>"]+',text): c.append(('',absurl(u)))
    wanted=[]
    record_years=years('COFEPRIS_YEARS','2022,2023,2024,2025,2026')
    status_years=years('COFEPRIS_STATUS_YEARS','2022,2023,2024,2025,2026')
    cats=[('alopaticos','Registros_Alopaticos_otorgados_'),('herbolarios','Registros_Herbolarios_otorgados_'),('homeopaticos','Registros_Homeopaticos_otorgados_'),('vitaminicos','Registros_Vitaminicos_otorgados_')]
    for y in record_years:
        for k,p in cats: wanted.append((f'{k}_{y}',p+y,'record',y,''))
    for y in status_years:
        wanted += [(f'revoked_{y}',f'Registros Revocados Medicamentos {y}','status',y,'REVOCADO'),(f'cancelled_{y}',f'Registros Cancelados Medicamentos {y}','status',y,'CANCELADO')]
    out=[]
    for key,label,kind,y,status in wanted:
        target=norm(label); hit=None
        for txt,u in c:
            blob=norm(txt+' '+u)
            if target in blob or norm(label+'.pdf') in blob: hit=u; break
        if hit: out.append({'key':key,'label':label,'url':hit,'kind':kind,'year':y,'status':status})
    # De-duplicate by source URL and keep diagnostics for reproducibility.
    seen=set(); uniq=[]
    for x in out:
        if x['url'] not in seen: seen.add(x['url']); uniq.append(x)
    if not uniq: raise RuntimeError('No se localizaron documentos compatibles. '+json.dumps({'mode':mode,'diagnostics':diag},ensure_ascii=False))
    return uniq

REG=[re.compile(r'\b\d{1,8}[A-Z]?\d{0,4}\s*(?:SSA|SS[A-Z]?|MEX)\b',re.I),re.compile(r'\b[A-Z0-9]{1,10}\s*\d{2,6}\s*SSA\b',re.I)]
def reg_match(line):
    for p in REG:
        m=p.search(line)
        if m:return re.sub(r'\s+',' ',m.group(0)).strip()
    return ''

def clean(x): return re.sub(r'\s+',' ',str(x or '')).strip(' |\t')
def pdf_text(path):
    reader=PdfReader(path); out=[]
    for page in reader.pages:
        try:t=page.extract_text(extraction_mode='layout') or ''
        except Exception:t=page.extract_text() or ''
        out.append(t)
    return '\n'.join(out)

def parse_lines(text,url,date):
    lines=[clean(x) for x in text.splitlines() if clean(x)]; rows=[]; seen=set()
    for i,line in enumerate(lines):
        reg=reg_match(line)
        if not reg: continue
        idx=line.upper().find(reg.upper()); before=line[:idx].strip(' |'); after=line[idx+len(reg):].strip(' |')
        name=before or after or (lines[i+1] if i+1<len(lines) else '')
        if norm(name) in {norm(reg),'REGISTRO','NUMERO REGISTRO','NOMBRE','DENOMINACION DISTINTIVA','DENOMINACION GENERICA','TITULAR'}: name=''
        key=(norm(reg),norm(name))
        if key in seen: continue
        seen.add(key); rows.append((reg,name,'','','','VIGENTE',url,date,line))
    return rows

def download(url,dest,archive):
    last=None
    for n in range(3):
        try:
            r=requests.get(url,headers=HEADERS,timeout=180,allow_redirects=True); r.raise_for_status()
            if len(r.content)<1000: raise RuntimeError('archivo demasiado pequeño')
            dest.write_bytes(r.content); archive.write_bytes(r.content)
            return r,hashlib.sha256(r.content).hexdigest()
        except Exception as e: last=e; time.sleep(2*(n+1))
    raise last

def sync_official_sources(force=False):
    if not _LOCK.acquire(blocking=False): return {'ok':False,'skipped':True,'reason':'Otra sincronización COFEPRIS está en curso.'}
    try:
        from app import db
        c=db(); now=datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0); iso=now.isoformat(); stamp=now.strftime('%Y%m%dT%H%M%SZ')
        found=discover(); downloaded=[]; failures=[]; total=0; status_total=0
        for src in found:
            try:
                dest=RAW/(src['key']+'.pdf'); archive=ARCHIVE/(src['key']+'_'+stamp+'.pdf')
                rr,digest=download(src['url'],dest,archive); txt=pdf_text(dest); rows=[]
                if src['kind']=='status':
                    for line in txt.splitlines():
                        reg=reg_match(clean(line))
                        if reg:
                            c.execute('INSERT OR REPLACE INTO statuses VALUES (?,?,?,?,?)',(norm(reg),src['status'],clean(line),src['url'],iso[:10])); status_total+=1
                else:
                    rows=parse_lines(txt,src['url'],iso[:10])
                    for reg,name,ai,conc,pres,status,u,date,frag in rows:
                        # Do not replace a rich row from the supplied COFEPRIS
                        # dataset with sparse PDF-extracted data. Merge only
                        # non-empty fields and retain the existing status unless
                        # an explicit revocation/cancellation source says otherwise.
                        reg_key = norm(reg)
                        existing = c.execute('SELECT * FROM records WHERE UPPER(registry)=?', (reg_key,)).fetchone()
                        if existing:
                            updates = {
                                'name': name, 'active_ingredient': ai,
                                'concentration': conc, 'presentation': pres,
                            }
                            assignments=[]; values=[]
                            for col,val in updates.items():
                                if val and str(val).strip() and not str(existing[col] or '').strip():
                                    assignments.append(f'{col}=?'); values.append(val)
                            # Source metadata may advance, but identity fields already
                            # populated by the richer dataset are never overwritten by
                            # a sparse PDF parser. Preserve the original status as well.
                            for col,val in {'source_url':u,'source_date':date,'source_fragment':frag}.items():
                                if val and str(val).strip():
                                    assignments.append(f'{col}=?'); values.append(val)
                            if assignments:
                                values.append(reg_key)
                                c.execute(f"UPDATE records SET {', '.join(assignments)} WHERE UPPER(registry)=?", values)
                        else:
                            c.execute('''INSERT INTO records (registry,name,active_ingredient,concentration,presentation,status,source_url,source_date,source_fragment) VALUES (?,?,?,?,?,?,?,?,?)''',(reg_key,name,ai,conc,pres,status,u,date,frag))
                        total+=1
                c.execute('INSERT OR REPLACE INTO sync_sources VALUES (?,?,?,?,?,?,?,?)',(src['key'],src['label'],src['url'],src['kind'],src['year'],iso,len(rr.content),len(rows)))
                downloaded.append({'key':src['key'],'label':src['label'],'url':src['url'],'sha256':digest,'bytes':len(rr.content),'records':len(rows),'kind':src['kind']})
            except Exception as e: failures.append({'key':src.get('key'),'url':src.get('url'),'error':str(e)})
        c.commit(); count=c.execute('SELECT COUNT(*) FROM records').fetchone()[0]; scount=c.execute('SELECT COUNT(*) FROM statuses').fetchone()[0]
        manifest={'last_sync':iso,'official_page':PAGE,'viewer':VIEWER,'sources':downloaded,'failures':failures,'record_count':count,'status_count':scount}
        (DATA/'sync_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        if not downloaded: raise RuntimeError('No se pudo descargar ninguna fuente oficial. '+json.dumps(failures,ensure_ascii=False))
        return {'ok':not failures,'last_sync':iso,'record_count':count,'status_count':scount,'downloaded':downloaded,'failures':failures,'official_page':PAGE,'viewer':VIEWER}
    finally: _LOCK.release()

def sync_if_due():
    hours=float(os.getenv('AUTO_SYNC_HOURS','24'))
    manifest=DATA/'sync_manifest.json'
    if manifest.exists():
        try:
            d=json.loads(manifest.read_text(encoding='utf-8')); last=datetime.datetime.fromisoformat(d.get('last_sync',''))
            age=(datetime.datetime.now(datetime.timezone.utc)-last).total_seconds()/3600
            if age < hours and not d.get('failures'):
                return {'ok':True,'skipped':True,'reason':f'Última sincronización hace {age:.1f} h'}
        except Exception: pass
    return sync_official_sources()

if __name__=='__main__': print(json.dumps(sync_official_sources(force=True),ensure_ascii=False,indent=2))
