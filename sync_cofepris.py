from pathlib import Path
import requests, re, json, datetime, sqlite3, os, unicodedata, time, hashlib
from bs4 import BeautifulSoup
from pypdf import PdfReader

BASE=Path(__file__).parent
DATA=BASE/'data'
RAW=DATA/'cofepris_raw'
ARCHIVE=DATA/'cofepris_archive'
RAW.mkdir(parents=True, exist_ok=True)
ARCHIVE.mkdir(parents=True, exist_ok=True)
PAGE='https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos'
VIEWER='https://registros.cofepris.gob.mx/BRSDM/'
PROXY_PAGE='https://r.jina.ai/https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos'
HEADERS_LIST=[
 {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36','Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','Accept-Language':'es-MX,es;q=0.9,en;q=0.7','Referer':'https://www.gob.mx/cofepris/','Cache-Control':'no-cache'},
 {'User-Agent':'MediCheckMX/37 official COFEPRIS sync','Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','Accept-Language':'es-MX,es;q=0.9'}
]

def norm(v):
    s=unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().upper()
    return re.sub(r'\s+',' ',s.strip())

def get_years(env, default):
    raw=os.getenv(env,default)
    return [x.strip() for x in raw.split(',') if x.strip()]

def fetch_official_page():
    urls=[PAGE,PAGE+'?idiom=es',PAGE+'?tab=documents']
    diagnostics=[]
    for url in urls:
        for headers in HEADERS_LIST:
            try:
                r=requests.get(url,headers=headers,timeout=60,allow_redirects=True)
                diagnostics.append({'url':url,'status':r.status_code,'final_url':r.url,'bytes':len(r.content),'content_type':r.headers.get('content-type','')})
                if r.ok and len(r.text)>5000 and ('Registros_Alopaticos' in r.text or 'Visor de Registros' in r.text):
                    return r.text, diagnostics, 'official'
            except Exception as e:
                diagnostics.append({'url':url,'error':str(e)})
    # COFEPRIS/gob.mx can return a tiny anti-bot HTML shell to cloud hosts.
    # Use a text-rendering proxy only to discover links; downloads are still made
    # directly from the official COFEPRIS/gob.mx URLs.
    try:
        r=requests.get(PROXY_PAGE,headers={'User-Agent':'MediCheckMX/37'},timeout=90)
        diagnostics.append({'url':PROXY_PAGE,'status':r.status_code,'final_url':r.url,'bytes':len(r.content),'content_type':r.headers.get('content-type','')})
        if r.ok and len(r.text)>1000 and ('Registros_Alopaticos' in r.text or 'Visor de Registros' in r.text):
            return r.text, diagnostics, 'proxy-discovery'
    except Exception as e:
        diagnostics.append({'url':PROXY_PAGE,'error':str(e)})
    raise RuntimeError('COFEPRIS_HTML_FETCH_FAILED: '+json.dumps(diagnostics,ensure_ascii=False))

def absolute_url(href):
    href=(href or '').strip().strip('<>')
    if href.startswith('//'): return 'https:'+href
    if href.startswith('/'): return 'https://www.gob.mx'+href
    if re.match(r'^https?://',href,re.I): return href
    return 'https://www.gob.mx/'+href.lstrip('/')

def discover():
    text, diagnostics, discovery_mode=fetch_official_page()
    candidates=[]
    # Works with both HTML and the markdown emitted by the discovery proxy.
    soup=BeautifulSoup(text,'html.parser')
    for a in soup.find_all('a',href=True):
        href=absolute_url(a.get('href','')); txt=' '.join(a.stripped_strings)
        candidates.append((txt,href,txt))
    for m in re.finditer(r'\[([^\]]+)\]\(([^)]+)\)',text):
        txt=m.group(1).strip(); href=absolute_url(m.group(2).strip())
        candidates.append((txt,href,txt))
    # Plain URLs in proxy output.
    for u in re.findall(r'https?://[^\s\)\]<>"\']+',text):
        candidates.append(('',absolute_url(u),''))

    record_years=get_years('COFEPRIS_YEARS','2026')
    status_years=get_years('COFEPRIS_STATUS_YEARS','2025')
    wanted=[]
    cats=[('alopaticos','Registros_Alopaticos_otorgados_'),('herbolarios','Registros_Herbolarios_otorgados_'),('vitaminicos','Registros_Vitaminicos_otorgados_'),('homeopaticos','Registros_Homeopaticos_otorgados_')]
    for year in record_years:
        for key,prefix in cats: wanted.append((f'{key}_{year}',prefix+year,'record',year,None))
    for year in status_years:
        wanted.append((f'revoked_{year}',f'Registros Revocados Medicamentos {year}','status',year,'REVOKED'))
        wanted.append((f'cancelled_{year}',f'Registros Cancelados Medicamentos {year}','status',year,'CANCELLED'))

    out=[]
    for key,label,kind,year,status in wanted:
        target=norm(label)
        found=False
        # Prefer a candidate whose visible label contains the requested title.
        for txt,href,ctx in candidates:
            blob=norm(txt+' '+ctx+' '+href)
            if target in blob or (target+'.PDF') in blob:
                out.append({'key':key,'label':label,'url':href,'kind':kind,'year':year,**({'status':status} if status else {})}); found=True; break
        if found: continue
        # Search nearby raw text/HTML for a direct attachment URL.
        for m in re.finditer(re.escape(label),text,re.I):
            window=text[max(0,m.start()-12000):min(len(text),m.end()+12000)]
            for raw in re.findall(r'https?://[^\s\)\]<>"\']+',window):
                href=absolute_url(raw)
                if 'gob.mx' in href and ('attachment' in href or label.lower().replace(' ','_') in href.lower()):
                    out.append({'key':key,'label':label,'url':href,'kind':kind,'year':year,**({'status':status} if status else {})}); found=True; break
            if found: break
        if found: continue
        # Last chance: markdown link immediately following the title.
        pat=re.compile(r'\[[^\]]*'+re.escape(label)+r'[^\]]*\]\(([^)]+)\)',re.I)
        mm=pat.search(text)
        if mm:
            out.append({'key':key,'label':label,'url':absolute_url(mm.group(1)),'kind':kind,'year':year,**({'status':status} if status else {})})

    seen=set(); unique=[]
    for x in out:
        k=(x['key'],x['url'])
        if k not in seen:
            seen.add(k); unique.append(x)
    if not unique:
        raise RuntimeError('COFEPRIS no devolvió enlaces compatibles. DIAGNOSTICO='+json.dumps({'mode':discovery_mode,'diagnostics':diagnostics},ensure_ascii=False))
    return unique


def pdf_text(path):
    chunks=[]
    reader=PdfReader(path)
    for page in reader.pages:
        try: t=page.extract_text(extraction_mode='layout') or ''
        except Exception: t=page.extract_text() or ''
        chunks.append(t)
    return '\n'.join(chunks)

REG_PATTERNS=[
    re.compile(r'\b\d{1,8}[A-Z]?\d{0,4}\s*(?:SSA|SS[A-Z]?|MEX)\b',re.I),
    re.compile(r'\b[A-Z0-9]{1,10}\s*\d{2,6}\s*SSA\b',re.I),
]
def reg_match(line):
    for p in REG_PATTERNS:
        m=p.search(line)
        if m: return re.sub(r'\s+',' ',m.group(0)).strip()
    return ''

def clean_cell(x): return re.sub(r'\s+',' ',str(x or '')).strip(' |\t')

def parse_record_lines(text,url,date):
    lines=[clean_cell(x) for x in text.splitlines() if clean_cell(x)]
    rows=[]
    for i,line in enumerate(lines):
        reg=reg_match(line)
        if not reg: continue
        idx=line.upper().find(reg.upper())
        before=line[:max(0,idx)].strip(' |')
        after=line[idx+len(reg):].strip(' |')
        name=before or after or (lines[i+1] if i+1<len(lines) else '')
        if norm(name) in {norm(reg),'REGISTRO','NUMERO REGISTRO','NOMBRE','DENOMINACION DISTINTIVA','DENOMINACION GENERICA','TITULAR'}: name=''
        rows.append((reg,name,'','','','', 'ACTIVE',url,date,line))
    seen=set(); unique=[]
    for x in rows:
        key=(norm(x[0]),norm(x[1]))
        if key not in seen: seen.add(key); unique.append(x)
    return unique

def download(url,dest,archive_dest):
    last=None
    for attempt in range(3):
        try:
            # Fixed: the old implementation referenced an undefined HEAD variable.
            r=requests.get(url,headers=HEADERS_LIST[0],timeout=180,allow_redirects=True)
            r.raise_for_status()
            if len(r.content)<1000: raise RuntimeError('archivo demasiado pequeño')
            digest=hashlib.sha256(r.content).hexdigest()
            dest.write_bytes(r.content)
            archive_dest.write_bytes(r.content)
            return r,digest
        except Exception as e:
            last=e; time.sleep(2*(attempt+1))
    raise last

def sync_official_sources():
    from app import db
    c=db(); now=datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0); iso=now.isoformat(); stamp=now.strftime('%Y%m%dT%H%M%SZ')
    found=discover(); downloaded=[]; total=0; stat_total=0; failures=[]
    for src in found:
        try:
            dest=RAW/(src['key']+'.pdf')
            archive=ARCHIVE/(f"{src['key']}_{stamp}.pdf")
            rr,digest=download(src['url'],dest,archive)
            ctype=(rr.headers.get('content-type') or '').lower(); text=pdf_text(dest); rows=[]
            if src['kind']=='status':
                for line in text.splitlines():
                    line=clean_cell(line); reg=reg_match(line)
                    if reg:
                        c.execute('INSERT OR REPLACE INTO statuses VALUES (?,?,?,?,?)',(norm(reg),src['status'],line,src['url'],iso[:10])); stat_total+=1
            else:
                rows=parse_record_lines(text,src['url'],iso[:10])
                for row in rows:
                    c.execute('''INSERT OR REPLACE INTO records (registry,name,active_ingredient,concentration,presentation,manufacturer,status,source_url,source_date,source_fragment) VALUES (?,?,?,?,?,?,?,?,?,?)''',row); total+=1
            c.execute('INSERT OR REPLACE INTO sync_sources VALUES (?,?,?,?,?,?,?,?)',(src['key'],src['label'],src['url'],src['kind'],src['year'],iso,len(rr.content),len(rows)))
            downloaded.append({'key':src['key'],'label':src['label'],'url':src['url'],'archive':str(archive.relative_to(BASE)),'sha256':digest,'bytes':len(rr.content),'records':len(rows),'kind':src['kind'],'content_type':ctype})
        except Exception as e:
            failures.append({'key':src.get('key'),'url':src.get('url'),'error':str(e)})
    c.commit()
    manifest={'last_sync':iso,'official_page':PAGE,'viewer':VIEWER,'sources':downloaded,'failures':failures,'configured_record_years':get_years('COFEPRIS_YEARS','2026'),'configured_status_years':get_years('COFEPRIS_STATUS_YEARS','2025')}
    (DATA/'sync_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if not downloaded: raise RuntimeError('No se pudo descargar ninguna fuente oficial. '+json.dumps(failures,ensure_ascii=False))
    return {'ok':not failures,'last_sync':iso,'record_count':c.execute('SELECT COUNT(*) FROM records').fetchone()[0],'status_count':c.execute('SELECT COUNT(*) FROM statuses').fetchone()[0],'downloaded':downloaded,'failures':failures,'official_page':PAGE,'viewer':VIEWER,'archive_directory':'data/cofepris_archive','note':'Los documentos originales se archivan con fecha y SHA-256. La descarga final se realiza desde URLs oficiales; el proxy se usa únicamente para descubrir enlaces cuando gob.mx entrega HTML incompleto al servidor.'}

if __name__=='__main__': print(json.dumps(sync_official_sources(),ensure_ascii=False,indent=2))
