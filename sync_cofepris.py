from pathlib import Path
import requests, re, json, datetime, sqlite3, os, unicodedata, time
from bs4 import BeautifulSoup
from pypdf import PdfReader

BASE=Path(__file__).parent
DATA=BASE/'data'
RAW=DATA/'cofepris_raw'
RAW.mkdir(parents=True, exist_ok=True)
PAGE='https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos'
VIEWER='https://registros.cofepris.gob.mx/BRSDM/'
HEADERS_LIST=[
 {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36','Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','Accept-Language':'es-MX,es;q=0.9,en;q=0.7','Referer':'https://www.gob.mx/cofepris/','Cache-Control':'no-cache'},
 {'User-Agent':'MediCheckMX/31 official COFEPRIS sync','Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8','Accept-Language':'es-MX,es;q=0.9'}
]
def fetch_official_page():
    urls=[PAGE,PAGE+'?idiom=es','https://www.gob.mx/cofepris/documentos/registros-sanitarios-medicamentos?tab=documents']
    diagnostics=[]
    for url in urls:
        for headers in HEADERS_LIST:
            try:
                r=requests.get(url,headers=headers,timeout=60,allow_redirects=True)
                diagnostics.append({'url':url,'status':r.status_code,'final_url':r.url,'bytes':len(r.content),'content_type':r.headers.get('content-type','')})
                if r.ok and len(r.text)>5000 and ('Registros_Alopaticos' in r.text or 'Visor de Registros' in r.text):
                    return r.text,diagnostics
            except Exception as e: diagnostics.append({'url':url,'error':str(e)})
    raise RuntimeError('COFEPRIS_HTML_FETCH_FAILED: '+json.dumps(diagnostics,ensure_ascii=False))

def absolute_url(href):
    href=(href or '').strip()
    if href.startswith('//'): return 'https:'+href
    if href.startswith('/'): return 'https://www.gob.mx'+href
    if re.match(r'^https?://',href,re.I): return href
    return 'https://www.gob.mx/'+href.lstrip('/')

def discover():
    html,diagnostics=fetch_official_page()
    soup=BeautifulSoup(html,'html.parser'); candidates=[]
    for a in soup.find_all('a',href=True):
        href=absolute_url(a.get('href','')); txt=' '.join(a.stripped_strings); ctx=[]; cur=a
        for _ in range(8):
            cur=getattr(cur,'parent',None)
            if cur is None: break
            t=' '.join(cur.stripped_strings)
            if t: ctx.append(t)
            if len(t)>5000: break
        candidates.append((txt,href,' '.join(ctx)))
    record_years=get_years('COFEPRIS_YEARS','2026'); status_years=get_years('COFEPRIS_STATUS_YEARS','2025')
    wanted=[]; cats=[('alopaticos','Registros_Alopaticos_otorgados_'),('herbolarios','Registros_Herbolarios_otorgados_'),('vitaminicos','Registros_Vitaminicos_otorgados_'),('homeopaticos','Registros_Homeopaticos_otorgados_')]
    for year in record_years:
        for key,prefix in cats: wanted.append((f'{key}_{year}',prefix+year,'record',year,None))
    for year in status_years:
        wanted.append((f'revoked_{year}',f'Registros Revocados Medicamentos {year}','status',year,'REVOKED'))
        wanted.append((f'cancelled_{year}',f'Registros Cancelados Medicamentos {year}','status',year,'CANCELLED'))
    out=[]
    for key,label,kind,year,status in wanted:
        target=norm(label); target2=target[:-4] if target.endswith('.PDF') else target; found=False
        for txt,href,ctx in candidates:
            blob=norm(txt+' '+ctx)
            if target in blob or target2 in blob:
                out.append({'key':key,'label':label,'url':href,'kind':kind,'year':year,**({'status':status} if status else {})}); found=True; break
        if found: continue
        pos=-1
        for needle in (label,label+'.pdf' if not label.lower().endswith('.pdf') else label[:-4]):
            m=re.search(re.escape(needle),html,re.I)
            if m: pos=m.start(); break
        if pos>=0:
            window=html[max(0,pos-20000):min(len(html),pos+20000)]
            patterns=[
                '(?:href|data-href|data-url)\\s*=\\s*["\\\']([^"\\\']+)["\\\']',
                '(?:window\\.open|location(?:\\.href)?|download)\\s*\\(\\s*["\\\']([^"\\\']+)["\\\']',
                'https?://[^"\' <>\\s]+',
                '/cms/uploads/attachment/file/[^"\' <>\\s]+'
            ]
            for pat in patterns:
                for mm in re.finditer(pat,window,re.I):
                    raw=mm.group(1) if mm.groups() else mm.group(0); href=absolute_url(raw)
                    if 'gob.mx' in href or '/cms/uploads/attachment/file/' in href:
                        out.append({'key':key,'label':label,'url':href,'kind':kind,'year':year,**({'status':status} if status else {})}); found=True; break
                if found: break
    seen=set(); unique=[]
    for x in out:
        k=(x['key'],x['url'])
        if k not in seen: seen.add(k); unique.append(x)
    if not unique: raise RuntimeError('COFEPRIS no devolvió enlaces compatibles. DIAGNOSTICO='+json.dumps(diagnostics,ensure_ascii=False))
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


def clean_cell(x):
    return re.sub(r'\s+',' ',str(x or '')).strip(' |\t')


def parse_record_lines(text,url,date):
    lines=[clean_cell(x) for x in text.splitlines() if clean_cell(x)]
    rows=[]
    headers=[]
    for i,line in enumerate(lines):
        n=norm(line)
        if 'NUMERO' in n and 'REGISTRO' in n: headers.append(i)

    # Generic parser: preserves the complete source row in source_fragment even when
    # a PDF's visual columns cannot be reliably separated.
    for i,line in enumerate(lines):
        reg=reg_match(line)
        if not reg: continue
        before=line[:max(0,line.upper().find(reg.upper()))].strip(' |')
        after=line[max(0,line.upper().find(reg.upper()))+len(reg):].strip(' |')
        name=before or after
        if not name and i+1<len(lines): name=lines[i+1]
        if norm(name) in {norm(reg),'REGISTRO','NUMERO REGISTRO'}: name=''
        # Avoid treating obvious header/metadata rows as medicine names.
        if norm(name) in {'NOMBRE','DENOMINACION DISTINTIVA','DENOMINACION GENERICA','TITULAR'}: name=''
        rows.append((reg,name,'','','','', 'ACTIVE',url,date,line))

    seen=set(); unique=[]
    for x in rows:
        key=(norm(x[0]),norm(x[1]))
        if key not in seen:
            seen.add(key); unique.append(x)
    return unique


def download(url,dest):
    last=None
    for attempt in range(3):
        try:
            r=requests.get(url,headers=HEADERS_LIST[0],timeout=120,allow_redirects=True)
            r.raise_for_status()
            if len(r.content)<1000: raise RuntimeError('archivo demasiado pequeño')
            dest.write_bytes(r.content)
            return r
        except Exception as e:
            last=e; time.sleep(1.5*(attempt+1))
    raise last


def sync_official_sources():
    from app import db
    c=db(); now=datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    found=discover(); downloaded=[]; total=0; stat_total=0; failures=[]
    if not found:
        raise RuntimeError('COFEPRIS no devolvió enlaces compatibles. La página oficial puede haber cambiado.')

    for src in found:
        try:
            dest=RAW/(src['key']+'.pdf')
            rr=download(src['url'],dest)
            ctype=(rr.headers.get('content-type') or '').lower()
            text=pdf_text(dest)
            rows=[]
            if src['kind']=='status':
                for line in text.splitlines():
                    line=clean_cell(line); reg=reg_match(line)
                    if reg:
                        c.execute('INSERT OR REPLACE INTO statuses VALUES (?,?,?,?,?)',(norm(reg),src['status'],line,src['url'],now[:10])); stat_total+=1
            else:
                rows=parse_record_lines(text,src['url'],now[:10])
                for row in rows:
                    c.execute('''INSERT OR REPLACE INTO records
                    (registry,name,active_ingredient,concentration,presentation,manufacturer,status,source_url,source_date,source_fragment)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',row)
                    total+=1
            c.execute('INSERT OR REPLACE INTO sync_sources VALUES (?,?,?,?,?,?,?,?)',(src['key'],src['label'],src['url'],src['kind'],src['year'],now,len(rr.content),len(rows)))
            downloaded.append({'key':src['key'],'label':src['label'],'url':src['url'],'bytes':len(rr.content),'records':len(rows),'kind':src['kind'],'content_type':ctype})
        except Exception as e:
            failures.append({'key':src['key'],'url':src['url'],'error':str(e)})

    c.commit()
    manifest={'last_sync':now,'official_page':PAGE,'viewer':VIEWER,'sources':downloaded,'failures':failures,'configured_record_years':get_years('COFEPRIS_YEARS','2026'),'configured_status_years':get_years('COFEPRIS_STATUS_YEARS','2025')}
    (DATA/'sync_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    if not downloaded:
        raise RuntimeError('No se pudo descargar ninguna fuente oficial. Revisa conectividad del servidor.')
    return {'ok':True,'last_sync':now,'record_count':c.execute('SELECT COUNT(*) FROM records').fetchone()[0],'status_count':c.execute('SELECT COUNT(*) FROM statuses').fetchone()[0],'downloaded':downloaded,'failures':failures,'official_page':PAGE,'viewer':VIEWER,'note':'Los datos provienen de enlaces descubiertos en la página oficial de COFEPRIS. Los campos que no pueden extraerse de forma segura del PDF quedan vacíos; no se inventan.'}

if __name__=='__main__': print(json.dumps(sync_official_sources(),ensure_ascii=False,indent=2))
