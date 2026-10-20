"""Extract verified official injury-report source text; no inferred player availability."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .core import identity, iso, now
from .store import Store

PDF_VERSION='6.21.1'
PARSER_VERSION='pypdf-6.21.1-layout-v1'


class PDFDependencyError(RuntimeError):
    pass


def pdf_pages(path):
    try:
        import pypdf
    except ImportError:
        raise PDFDependencyError('Install the pinned PDF reader from requirements.txt in the build command') from None
    if pypdf.__version__!=PDF_VERSION:
        raise PDFDependencyError('Installed PDF reader differs from the reviewed pinned version')
    reader=pypdf.PdfReader(str(path),strict=True)
    if reader.is_encrypted:
        raise ValueError('Official report unexpectedly requires PDF decryption')
    if len(reader.pages)>100:
        raise ValueError('Report exceeds extraction page limit')
    result=[]
    for number,page in enumerate(reader.pages,1):
        text=page.extract_text(extraction_mode='layout') or ''
        result.append({'page':number,'text':text})
        if sum(len(p['text']) for p in result)>2*1024*1024:
            raise ValueError('Report text exceeds extraction size limit')
    return result


def extract(path):
    store=Store(path)
    try:
        latest=store.db.execute('SELECT payload FROM source_checks ORDER BY attempt DESC LIMIT 1').fetchone()
        if latest is None:
            return {'status':'not_ready','reason':'Run the sources check first to archive an official injury report'}
        result=json.loads(latest['payload'])['sources'].get('official_injury_report',{})
        if result.get('status')!='pdf_archived':
            return {'status':'not_ready','reason':'The latest source check did not archive a verified report; no older report is silently substituted'}
        url=result['source_url']
        artifact=next((a for a in result.get('artifacts',[]) if a.get('url')==url),None)
        digest=artifact.get('sha256') if artifact else None
        if not digest or not re.fullmatch(r'[0-9a-f]{64}',digest):
            raise ValueError('Missing report content identity')
        manifest=store.db.execute('SELECT * FROM source_artifacts WHERE id=?',(identity([url,digest]),)).fetchone()
        if not manifest or manifest['url']!=url or manifest['sha256']!=digest:
            raise ValueError('Report does not match its source manifest')
        directory=store.path.parent.resolve()/'source-artifacts'
        if not directory.resolve().is_relative_to(store.path.parent.resolve()):
            raise ValueError('Source directory escapes database directory')
        source=directory/(digest+'.source')
        if source.is_symlink() or not source.is_file() or source.stat().st_size>24*1024*1024:
            raise ValueError('Report artifact is missing, redirected or too large')
        raw=source.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest or not raw.lstrip().startswith(b'%PDF-'):
            raise ValueError('Report bytes fail source-hash or PDF verification')
        key=identity([digest,PARSER_VERSION])
        previous=store.db.execute('SELECT payload FROM injury_texts WHERE id=?',(key,)).fetchone()
        if previous:
            return {**json.loads(previous['payload']),'cached':True}
        try:
            pages=pdf_pages(source)
        except PDFDependencyError as error:
            return {'status':'dependency_missing','required_package':f'pypdf=={PDF_VERSION}',
                    'reason':str(error),'build_command':'python3 -m pip install --require-hashes -r requirements.txt && python3 -m unittest discover -s tests -v'}
        payload={'status':'text_extracted' if any(p['text'].strip() for p in pages) else 'no_extractable_text',
                 'source_url':url,'source_sha256':digest,'parser_version':PARSER_VERSION,
                 'source_first_observed_at':manifest['first_observed'],'processed_at':iso(now()),
                 'filename_timestamp_label':result.get('filename_timestamp_label'),'publication_time_utc':result.get('publication_time_utc'),
                 'page_count':len(pages),'character_count':sum(len(p['text']) for p in pages),'pages':pages,
                 'player_claims_mapped':False,'cached':False,
                 'note':'Original PDF text only. No player-status mapping, minutes restriction, probability adjustment, or live prediction is inferred. Text order/layout can differ from the visual PDF; review source context.'}
        with store.db:
            store.db.execute('INSERT OR IGNORE INTO injury_texts VALUES (?,?)',(key,json.dumps(payload,sort_keys=True)))
        # Concurrent readers always return the original stored extraction; never replace it.
        return json.loads(store.db.execute('SELECT payload FROM injury_texts WHERE id=?',(key,)).fetchone()[0])
    except Exception as error:
        store.operation('injury_text',False,f'Extraction failed ({type(error).__name__}); no replacement evidence supplied')
        return {'status':'error','error_type':type(error).__name__,
                'reason':'Report integrity, source identity, or PDF processing failed; the original source and ledger remain unchanged'}
    finally:
        store.close()


def summary(result):
    """Keep the default shell output short; the full pages remain in the audit database."""
    output={key:value for key,value in result.items() if key!='pages'}
    if result.get('pages'):
        output['first_page_text_preview']=result['pages'][0]['text'][:5000]
    return output
