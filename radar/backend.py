"""Durable Radar backend for captures, evidence objects, source adapters, and run state."""
import hashlib,json,os,secrets
from pathlib import Path
from urllib.parse import urlsplit
from dataclasses import dataclass
from .engine import Radar,Conflict,uid,now,encode,digest
from .business import CONTENT,validate_business

@dataclass
class Response:
    status:int
    final_url:str
    body:bytes
    headers:dict
    complete:bool=True

def utc(value):
    if value is None:return None
    from datetime import datetime,timezone
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')

class Backend(Radar):
    def __init__(self,path,quota=100*1024*1024):
        if Path(path).exists():
            import sqlite3
            check=sqlite3.connect(f'file:{Path(path).resolve().as_posix()}?mode=ro',uri=True)
            try:
                if check.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
                    version=check.execute("SELECT value FROM meta WHERE key='db_version'").fetchone()
                    if version and version[0]!='2':raise ValueError('unsupported database version')
            finally:check.close()
        super().__init__(path); self.directory=Path(path).parent;self.objects=self.directory/'objects';self.objects.mkdir(exist_ok=True);self.quota=quota;self.adapters={}
        self.db.executescript('''
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS source_revisions(source_id TEXT NOT NULL REFERENCES sources(id),revision INTEGER NOT NULL,value TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(source_id,revision));
        CREATE TABLE IF NOT EXISTS subscription_revisions(subscription_id TEXT NOT NULL REFERENCES subscriptions(id),revision INTEGER NOT NULL,value TEXT NOT NULL,principal TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(subscription_id,revision));
        CREATE TABLE IF NOT EXISTS discovery_decisions(subscription_id TEXT NOT NULL REFERENCES subscriptions(id),revision INTEGER NOT NULL,value TEXT NOT NULL,principal TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(subscription_id,revision));
        CREATE TABLE IF NOT EXISTS evidence_objects(sha TEXT PRIMARY KEY,size INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS captures(id TEXT PRIMARY KEY,observation_id TEXT NOT NULL REFERENCES observations(id),requested_url TEXT NOT NULL,final_url TEXT NOT NULL,http_status INTEGER NOT NULL,headers TEXT NOT NULL,raw_sha TEXT NOT NULL REFERENCES evidence_objects(sha),extractor TEXT NOT NULL,diagnostic TEXT,synthetic INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS resource_attempts(id TEXT PRIMARY KEY,observation_id TEXT NOT NULL REFERENCES observations(id),requested_url TEXT NOT NULL,final_url TEXT,http_status INTEGER,headers TEXT NOT NULL,raw_sha TEXT REFERENCES evidence_objects(sha),error TEXT);
        CREATE TABLE IF NOT EXISTS resource_versions(id TEXT PRIMARY KEY,message_id TEXT NOT NULL,message_revision INTEGER NOT NULL,url TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,sha TEXT REFERENCES evidence_objects(sha),text_content TEXT,observed_at TEXT NOT NULL,FOREIGN KEY(message_id,message_revision) REFERENCES message_revisions(message_id,revision));
        CREATE TABLE IF NOT EXISTS discovery_plans(subscription_id TEXT PRIMARY KEY REFERENCES subscriptions(id),revision INTEGER NOT NULL,interval_seconds INTEGER NOT NULL,next_at TEXT NOT NULL,state TEXT NOT NULL,authority TEXT NOT NULL,reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS plan_details(subscription_id TEXT NOT NULL,message_id TEXT NOT NULL,reevaluate_at TEXT,end_at TEXT,analysis_id TEXT,PRIMARY KEY(subscription_id,message_id));
        CREATE TABLE IF NOT EXISTS evaluation_requests(subscription_id TEXT NOT NULL,message_id TEXT NOT NULL,reason TEXT NOT NULL,due_at TEXT NOT NULL,PRIMARY KEY(subscription_id,message_id));
        CREATE TABLE IF NOT EXISTS discovery_attempts(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),source_id TEXT NOT NULL REFERENCES sources(id),requested_url TEXT NOT NULL,final_url TEXT NOT NULL,status INTEGER NOT NULL,headers TEXT NOT NULL,raw_sha TEXT NOT NULL REFERENCES evidence_objects(sha),received_at TEXT NOT NULL,synthetic INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS transport_cache(batch_id TEXT NOT NULL,source_id TEXT NOT NULL,url TEXT NOT NULL,status INTEGER NOT NULL,final_url TEXT NOT NULL,headers TEXT NOT NULL,raw_sha TEXT NOT NULL REFERENCES evidence_objects(sha),PRIMARY KEY(batch_id,source_id,url));
        CREATE TABLE IF NOT EXISTS transport_locks(batch_id TEXT NOT NULL,source_id TEXT NOT NULL,url TEXT NOT NULL,owner TEXT NOT NULL,expires_at REAL NOT NULL,PRIMARY KEY(batch_id,source_id,url));
        CREATE TABLE IF NOT EXISTS run_intents(run_id TEXT PRIMARY KEY REFERENCES runs(id),discovery INTEGER NOT NULL,batch_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS run_targets(run_id TEXT NOT NULL REFERENCES runs(id),source_id TEXT NOT NULL REFERENCES sources(id),url TEXT NOT NULL,PRIMARY KEY(run_id,source_id,url));
        CREATE TABLE IF NOT EXISTS checkpoints(subscription_id TEXT NOT NULL,source_id TEXT NOT NULL,revision INTEGER NOT NULL,value TEXT NOT NULL,PRIMARY KEY(subscription_id,source_id));
        CREATE TABLE IF NOT EXISTS execution_errors(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,item_id TEXT,stage TEXT NOT NULL,error TEXT NOT NULL,created_at TEXT NOT NULL);
        COMMIT;
        ''')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for table in ('captures','discovery_attempts'):
                if 'fetch_method' not in {r[1] for r in self.db.execute('PRAGMA table_info('+table+')')}:self.db.execute("ALTER TABLE "+table+" ADD COLUMN fetch_method TEXT NOT NULL DEFAULT 'legacy'")
            self.db.commit()
        except BaseException:self.db.rollback();raise
        for table in ('message_revisions','resource_versions','captures','resource_attempts','observations','analyses','review_decisions','operations','source_revisions','subscription_revisions','discovery_decisions','evidence_objects','discovery_attempts'):
            for action in ('UPDATE','DELETE'):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable evidence/history'); END")
        index=self.db.execute("SELECT sql FROM sqlite_master WHERE name='one_active_run'").fetchone()
        if index and 'awaiting_analysis' not in index[0]:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('DROP INDEX IF EXISTS one_active_run')
                self.db.execute("CREATE UNIQUE INDEX one_active_run ON runs(subscription_id) WHERE status IN ('queued','running','awaiting_analysis','awaiting_executor')")
                self.db.commit()
            except BaseException:self.db.rollback();raise
        page_size=self.db.execute('PRAGMA page_size').fetchone()[0]
        self.db.execute('PRAGMA max_page_count='+str((32*1024*1024)//page_size))
        self.db.execute('PRAGMA wal_autocheckpoint=256')
        self.db.execute('PRAGMA journal_size_limit=8388608')
        self.db.execute("INSERT OR IGNORE INTO meta VALUES('db_version','2')")
        if self.db.execute("SELECT value FROM meta WHERE key='db_version'").fetchone()[0]!='2':raise ValueError('unsupported database version')

    def put_object(self,body):
        if not isinstance(body,bytes) or len(body)>10*1024*1024:raise ValueError('object size exceeded')
        sha=hashlib.sha256(body).hexdigest();target=self.objects/sha
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:raise ValueError('corrupt object')
            return sha
        # Serialize quota checks and object installation across processes; no automatic deletion.
        lock=os.fdopen(os.open(self.objects/'.lock',os.O_RDWR|os.O_CREAT),'r+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
        try:
            if os.name=='nt':
                import msvcrt;msvcrt.locking(lock.fileno(),msvcrt.LK_LOCK,1)
            else:
                import fcntl;fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
            used=sum(p.stat().st_size for p in self.objects.iterdir() if len(p.name)==64)
            if not target.exists():
                if used+len(body)>self.quota:raise ValueError('evidence quota exceeded')
                temp=self.objects/(sha+'.'+secrets.token_hex(8)+'.tmp')
                try:
                    with temp.open('xb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
                    os.replace(temp,target)
                finally:temp.unlink(missing_ok=True)
        finally:lock.close()
        return sha

    def allow_source(self,subscription,source):
        if not self.db.execute('SELECT 1 FROM subscription_sources WHERE subscription_id=? AND source_id=?',(subscription,source)).fetchone():raise PermissionError('source not allowed by subscription')

    def register_adapter(self,source,adapter):
        # Only application startup can register executable adapters; no JSON registration route.
        self.adapters[source]=adapter

    def capture(self,principal,key,p):
        required={'run_id','item_id','epoch'}
        if set(p)!=required:raise ValueError('capture accepts only run/item/epoch')
        saved=self.db.execute('SELECT request_hash,receipt FROM operations WHERE principal=? AND operation_key=?',(principal,key)).fetchone()
        if saved:
            if saved['request_hash']!=digest({'operation':'Backend.capture.<locals>.write','payload':p}):raise Conflict('operation key reused with different request')
            return json.loads(saved['receipt'])
        run=self._lease(p['run_id'],principal,p['epoch'])
        item=self.db.execute('SELECT * FROM run_items WHERE id=? AND run_id=?',(p['item_id'],p['run_id'])).fetchone()
        if not item:raise ValueError('unknown item')
        self.allow_source(run['subscription_id'],item['source_id'])
        adapter=self.adapters.get(item['source_id'])
        if adapter is None:raise ValueError('source adapter unavailable')
        root=self.db.execute('SELECT root_url FROM sources WHERE id=?',(item['source_id'],)).fetchone()[0]
        def allowed(url):
            a,b=urlsplit(root),urlsplit(url)
            return b.scheme in ('http','https') and (a.scheme,a.netloc)==(b.scheme,b.netloc) and not b.username and not b.password
        if not allowed(item['target']):raise ValueError('target outside source origin')
        from datetime import datetime,timezone
        import math
        lease_seconds=max(1,min(3600,math.ceil((datetime.fromisoformat(run['lease_until'].replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds())))
        response=None;content=None;diagnostic=None;resource_reads=[];resource_attempts=[];raw_sha=None;resource_review_reasons=[]
        def write():
            self._lease(p['run_id'],principal,p['epoch']);self.allow_source(run['subscription_id'],item['source_id'])
            if self.db.execute('SELECT root_url FROM sources WHERE id=?',(item['source_id'],)).fetchone()[0]!=root:raise Conflict('source origin changed during capture')
            mid=None;rev=None;accepted=content is not None
            # A failed or cached attempt can reference an existing identity but never creates a revision.
            external=adapter.identity(item['target'])
            previous=self.db.execute('SELECT * FROM messages WHERE source_id=? AND external_key=?',(item['source_id'],external)).fetchone()
            if previous:mid=previous['id'];rev=previous['current_revision']
            self.db.execute('INSERT OR IGNORE INTO evidence_objects VALUES(?,?)',(raw_sha,len(response.body)))
            if accepted:
                if not mid:
                    mid=uid();rev=0;self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,item['source_id'],external,0,now(),now()))
                old=self.db.execute('SELECT * FROM message_revisions WHERE message_id=? AND revision=?',(mid,rev)).fetchone()
                if old and old['extractor_version']!=adapter.version:raise Conflict('extractor changed: baseline comparison required')
                if not old or old['hash']!=digest(content):
                    rev+=1;self.db.execute('INSERT INTO message_revisions VALUES(?,?,?,?,?,?)',(mid,rev,now(),encode(content),digest(content),adapter.version));self.db.execute('UPDATE messages SET current_revision=? WHERE id=?',(rev,mid));self._change('message_revision',mid,rev)
                self.db.execute('UPDATE messages SET last_checked_at=? WHERE id=?',(now(),mid))
            oid=uid();self.db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(oid,p['run_id'],p['item_id'],mid,rev,now(),'fresh' if accepted else 'unknown','complete' if accepted else ('partial' if not response.complete else 'failed'),'complete' if all(x[1]=='read' for x in resource_reads) else 'partial',response.status,diagnostic))
            self.db.execute('INSERT INTO captures(id,observation_id,requested_url,final_url,http_status,headers,raw_sha,extractor,diagnostic,synthetic,fetch_method) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid(),oid,item['target'],response.final_url,response.status,encode(response.headers),raw_sha,adapter.version,diagnostic,int(adapter.synthetic),getattr(adapter,'transport_name','in-process-fixed-sample')))
            resources=[]
            for attempt in resource_attempts:
                if attempt['raw_sha']:self.db.execute('INSERT OR IGNORE INTO evidence_objects VALUES(?,?)',(attempt['raw_sha'],(self.objects/attempt['raw_sha']).stat().st_size))
                self.db.execute('INSERT INTO resource_attempts VALUES(?,?,?,?,?,?,?,?)',(uid(),oid,attempt['requested_url'],attempt['final_url'],attempt['status'],encode(attempt['headers']),attempt['raw_sha'],attempt['error']))
            if accepted:
                for manifest,status,sha,text in resource_reads:
                    if sha:self.db.execute('INSERT OR IGNORE INTO evidence_objects VALUES(?,?)',(sha,(self.objects/sha).stat().st_size))
                    latest=self.db.execute("SELECT status,sha FROM resource_versions WHERE message_id=? AND url=? ORDER BY rowid DESC LIMIT 1",(mid,manifest['url'])).fetchone()
                    previous_verified=self.db.execute("SELECT sha FROM resource_versions WHERE message_id=? AND url=? AND status='read' ORDER BY rowid DESC LIMIT 1",(mid,manifest['url'])).fetchone()
                    if status!='read' and previous_verified:
                        resource_review_reasons.append(f"Resource state is now unknown for {manifest['name']}.")
                    elif status=='read' and previous_verified and previous_verified['sha']!=sha:
                        resource_review_reasons.append(f"Resource content changed for {manifest['name']}.")
                    rid=uid();self.db.execute('INSERT INTO resource_versions VALUES(?,?,?,?,?,?,?,?,?)',(rid,mid,rev,manifest['url'],manifest['name'],status,sha,text,now()));resources.append(rid)
                    if status=='read' and (not latest or latest['sha']!=sha):self._change('resource_version',mid,rev)
            self.db.execute('UPDATE run_items SET observation_id=?,message_id=?,message_revision=?,state=?,analysis_id=NULL WHERE id=?',(oid,mid,rev,'collected' if accepted else 'fetch_failed',p['item_id']))
            if accepted:
                from .product import ensure_candidate, invalidate_for_resource_change
                ensure_candidate(self.db,run['subscription_id'],mid,rev,principal)
                if resource_review_reasons:
                    invalidate_for_resource_change(self.db,principal,run['subscription_id'],mid,rev,' '.join(resource_review_reasons))
            self._change('observation',mid or oid,rev or 0)
            return {'observation_id':oid,'message_id':mid,'message_revision':rev,'accepted':accepted,'resource_version_ids':resources}
        # Replays use the original request, without re-fetching potentially changed source data.
        if self.db.execute('SELECT 1 FROM operations WHERE principal=? AND operation_key=?',(principal,key)).fetchone():return self._write(principal,key,p,write)
        try:response=self.transport(adapter,item['source_id'],p['run_id'],item['target'])
        except (OSError,TimeoutError) as e:response=Response(0,item['target'],b'',{});diagnostic=type(e).__name__
        response.headers={k.lower():str(v) for k,v in response.headers.items() if k.lower() in ('content-type','age','etag','last-modified','date','cache-control')}
        raw_sha=self.put_object(response.body)
        if response.complete and response.status==200 and allowed(response.final_url) and not any(str(v) not in ('0','') for k,v in response.headers.items() if k.lower()=='age'):
            try:
                content=adapter.extract(response.body,base_url=response.final_url) if getattr(adapter,'supports_base_url',False) else adapter.extract(response.body);validate_business(CONTENT,content)
                if len(encode(content).encode('utf-8'))>262144:raise ValueError('canonical content exceeds 256KiB')
            except Exception as e:diagnostic='extraction_failed: '+str(e)[:200];content=None
        else:diagnostic=diagnostic or ('transport_incomplete' if not response.complete else 'http_or_freshness_unconfirmed')
        if content is not None:
            for index,manifest in enumerate(content['attachments']):
                self._lease(p['run_id'],principal,p['epoch']);self.allow_source(run['subscription_id'],item['source_id'])
                self.lease_action(principal,'capture-renew:'+digest({'operation_key':key,'epoch':p['epoch'],'asset':index}),'heartbeat',{'run_id':p['run_id'],'epoch':p['epoch'],'seconds':lease_seconds})
                status='failed';sha=None;text=None;attempt={'requested_url':manifest['url'],'final_url':None,'status':None,'headers':{},'raw_sha':None,'error':'origin_not_allowed'}
                if allowed(manifest['url']):
                    try:
                        asset=self.transport(adapter,item['source_id'],p['run_id'],manifest['url']);attempt.update(final_url=asset.final_url,status=asset.status,headers=asset.headers,raw_sha=self.put_object(asset.body),error=None)
                        attempt['headers']={k.lower():str(v) for k,v in asset.headers.items() if k.lower() in ('content-type','age','etag','last-modified','date','cache-control')}
                        if asset.complete and asset.status==200 and allowed(asset.final_url) and not any(str(v) not in ('0','') for k,v in asset.headers.items() if k.lower()=='age'):
                            sha=self.put_object(asset.body);status='read'
                            # Only explicitly decoded text can support quoted resource evidence.
                            if asset.headers.get('content-type','').startswith('text/plain'):text=asset.body.decode('utf-8')[:100000]
                    except (OSError,TimeoutError,UnicodeError,ValueError) as error:attempt['error']=type(error).__name__
                if status!='read' and attempt['error'] is None:attempt['error']='http_or_freshness_unconfirmed'
                resource_attempts.append(attempt)
                resource_reads.append((manifest,status,sha,text))
        return self._write(principal,key,p,write)

    def verify_resource(self,mid,revision,rid):
        row=self.db.execute('SELECT * FROM resource_versions WHERE id=? AND message_id=? AND message_revision=?',(rid,mid,revision)).fetchone()
        if not row:raise ValueError('resource belongs to another snapshot')
        return row

    def verify_evidence(self,mid,revision,ev):
        if (ev['message_id'],ev['message_revision'])!=(mid,revision):raise ValueError('evidence snapshot mismatch')
        if ev['resource_version_id']:
            row=self.verify_resource(mid,revision,ev['resource_version_id'])
            if row['status']!='read' or row['text_content'] is None:raise ValueError('resource quote unverified')
            if ev['quote'] not in row['text_content']:
                raw=(self.objects/row['sha']).read_bytes()
                if hashlib.sha256(raw).hexdigest()!=row['sha'] or ev['quote'] not in raw.decode('utf-8'):raise ValueError('resource quote unverified')
        else:
            content=json.loads(self.db.execute('SELECT content FROM message_revisions WHERE message_id=? AND revision=?',(mid,revision)).fetchone()[0]);blocks={b['block_id']:b for b in content['blocks']};block=blocks.get(ev['block_id'])
            if not block:raise ValueError('unknown evidence block')
            text=block['text']
            if ev['row'] is not None or ev['column'] is not None:
                if ev['row'] is None or ev['column'] is None:raise ValueError('table coordinates require row and column')
                try:text=block['rows'][ev['row']][ev['column']]
                except (IndexError,KeyError):raise ValueError('table evidence out of bounds')
            if ev['quote'] not in text:raise ValueError('quote absent from evidence')
    def save_subscription(self,principal,key,p):
        from .business import RULES,validate_business
        from .schemas import validate
        validate('subscription_save',{'operation_key':key,**p})
        validate_business(RULES,p['rules'])
        def write():
            existing=self.db.execute('SELECT revision FROM subscriptions WHERE id=?',(p['id'],)).fetchone();revision=existing[0] if existing else 0
            if revision!=p['expected_revision']:raise Conflict('subscription revision')
            for source in p['rules']['source_ids']:
                if not self.db.execute('SELECT 1 FROM sources WHERE id=?',(source,)).fetchone():raise ValueError('unknown subscription source')
            self.db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,rules=excluded.rules,status=excluded.status,revision=excluded.revision',(p['id'],p['name'],encode(p['rules']),p['status'],revision+1))
            self.db.execute('DELETE FROM subscription_sources WHERE subscription_id=?',(p['id'],))
            for source in p['rules']['source_ids']:self.db.execute('INSERT INTO subscription_sources VALUES(?,?)',(p['id'],source))
            self.db.execute('INSERT INTO subscription_revisions VALUES(?,?,?,?,?)',(p['id'],revision+1,encode(p),principal,now()))
            self._change('subscription',p['id'],revision+1);return {'id':p['id'],'revision':revision+1}
        return self._write(principal,key,p,write)

    def lease_action(self,principal,key,action,p):
        request={'action':action,**p}
        def write():
            run=self.db.execute('SELECT * FROM runs WHERE id=?',(p['run_id'],)).fetchone()
            if not run:raise ValueError('unknown run')
            subscription=self.db.execute('SELECT status FROM subscriptions WHERE id=?',(run['subscription_id'],)).fetchone()[0]
            if subscription=='archived' or (subscription=='paused' and run['trigger_kind']!='manual'):raise Conflict('subscription blocks automatic execution')
            seconds=p.get('seconds',60)
            from datetime import datetime,timezone,timedelta
            if action=='claim':
                if run['cancel_requested'] or run['status'] not in ('queued','running','awaiting_analysis','awaiting_executor') or (run['status']=='running' and run['lease_until'] and run['lease_until']>now()):raise Conflict('run unavailable')
                epoch=run['epoch']+1
            else:self._lease(p['run_id'],principal,p['epoch']);epoch=p['epoch']
            until=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='microseconds').replace('+00:00','Z')
            self.db.execute("UPDATE runs SET status='running',owner=?,epoch=?,lease_until=? WHERE id=?",(principal,epoch,until,p['run_id']));self._change('lease',p['run_id'],epoch)
            return {'owner':principal,'epoch':epoch,'lease_until':until}
        return self._write(principal,key,request,write)

    def discovery(self,principal,key,p):
        def write():
            run=self._lease(p['run_id'],principal,p['epoch']);self.allow_source(run['subscription_id'],p['source_id'])
            from urllib.parse import urlsplit
            root=self.db.execute('SELECT root_url FROM sources WHERE id=?',(p['source_id'],)).fetchone()[0];origin=urlsplit(root)
            for target in p['targets']:
                u=urlsplit(target)
                if (u.scheme,u.netloc)!=(origin.scheme,origin.netloc) or u.username or u.password:raise ValueError('target outside source origin')
                self.db.execute('INSERT OR IGNORE INTO run_items(id,run_id,source_id,target,state) VALUES(?,?,?,?,?)',(uid(),p['run_id'],p['source_id'],target,'discovered'))
            old=self.db.execute('SELECT revision FROM checkpoints WHERE subscription_id=? AND source_id=?',(run['subscription_id'],p['source_id'])).fetchone();revision=(old[0] if old else 0)+1
            self.db.execute('INSERT INTO checkpoints VALUES(?,?,?,?) ON CONFLICT(subscription_id,source_id) DO UPDATE SET revision=excluded.revision,value=excluded.value',(run['subscription_id'],p['source_id'],revision,encode({'targets':p['targets'],'persisted_at':now(),'coverage':p['coverage']})))
            return {'items':[dict(r) for r in self.db.execute('SELECT * FROM run_items WHERE run_id=? AND source_id=?',(p['run_id'],p['source_id']))],'checkpoint_revision':revision}
        return self._write(principal,key,p,write)

    def recheck(self,principal,key,p):
        """Queue a one-message forced source read without changing the subscription plan."""
        def write():
            subscription=self.db.execute('SELECT status FROM subscriptions WHERE id=?',(p['subscription_id'],)).fetchone()
            if not subscription or subscription['status']=='archived':raise Conflict('subscription unavailable')
            message=self.db.execute('SELECT * FROM messages WHERE id=?',(p['message_id'],)).fetchone()
            if not message:raise ValueError('message not found')
            self.allow_source(p['subscription_id'],message['source_id'])
            observed=self.db.execute('SELECT 1 FROM run_items i JOIN runs r ON r.id=i.run_id WHERE r.subscription_id=? AND i.message_id=? LIMIT 1',(p['subscription_id'],p['message_id'])).fetchone()
            if not observed:raise ValueError('message was not observed by this subscription')
            latest=self.db.execute('SELECT c.requested_url FROM captures c JOIN observations o ON o.id=c.observation_id WHERE o.message_id=? ORDER BY c.rowid DESC LIMIT 1',(p['message_id'],)).fetchone()
            if not latest:raise ValueError('message has no trusted capture URL')
            from urllib.parse import urlsplit
            root=urlsplit(self.db.execute('SELECT root_url FROM sources WHERE id=?',(message['source_id'],)).fetchone()[0]);target=urlsplit(latest['requested_url'])
            if (root.scheme,root.netloc)!=(target.scheme,target.netloc) or target.username or target.password:raise ValueError('stored target outside source origin')
            active=self.db.execute("SELECT * FROM runs WHERE subscription_id=? AND status IN ('queued','running','awaiting_analysis','awaiting_executor')",(p['subscription_id'],)).fetchone()
            if active and (active['status']!='queued' or self.db.execute('SELECT 1 FROM run_items WHERE run_id=?',(active['id'],)).fetchone()):raise Conflict('subscription run already executing; retry the explicit recheck after it finishes')
            if active:
                run_id=active['id']
                self.db.execute('INSERT OR IGNORE INTO run_targets VALUES(?,?,?)',(run_id,message['source_id'],latest['requested_url']))
                intent=self.db.execute('SELECT 1 FROM run_intents WHERE run_id=?',(run_id,)).fetchone()
                if not intent:self.db.execute('INSERT INTO run_intents VALUES(?,?,?)',(run_id,1,uid()))
            else:
                run_id=uid();self.db.execute('INSERT INTO runs(id,subscription_id,trigger_kind,status,started_at) VALUES(?,?,?,?,?)',(run_id,p['subscription_id'],'manual','queued',now()))
                self.db.execute('INSERT INTO run_intents VALUES(?,?,?)',(run_id,0,uid()))
                self.db.execute('INSERT INTO run_targets VALUES(?,?,?)',(run_id,message['source_id'],latest['requested_url']))
            self._change('manual_recheck',p['message_id'],message['current_revision'])
            return {'run_id':run_id,'message_id':p['message_id'],'message_revision':message['current_revision'],'status':'queued','forced':True}
        return self._write(principal,key,p,write)

    def save_discovery_plan(self,principal,key,p):
        def write():
            old=self.db.execute('SELECT * FROM discovery_plans WHERE subscription_id=?',(p['subscription_id'],)).fetchone();revision=old['revision'] if old else 0
            if revision!=p['expected_revision'] or (old and old['authority']=='user' and p['authority']!='user'):raise Conflict('plan revision or user override')
            revision+=1;self.db.execute('INSERT INTO discovery_plans VALUES(?,?,?,?,?,?,?) ON CONFLICT(subscription_id) DO UPDATE SET revision=excluded.revision,interval_seconds=excluded.interval_seconds,next_at=excluded.next_at,state=excluded.state,authority=excluded.authority,reason=excluded.reason',(p['subscription_id'],revision,p['interval_seconds'],utc(p['next_at']),p['state'],p['authority'],p['reason']));self.db.execute('INSERT INTO discovery_decisions VALUES(?,?,?,?,?)',(p['subscription_id'],revision,encode(p),principal,now()));self._change('discovery_plan',p['subscription_id'],revision);return {'revision':revision}
        return self._write(principal,key,p,write)

    def review(self,principal,key,p):
        # Add policy evaluation/end and fixed analysis basis while retaining old direct-engine API.
        from .schemas import validate
        validate('review',{'operation_key':key,**p})
        extra={k:p[k] for k in ('reevaluate_at','end_at','analysis_id') if k in p}
        def write():
            old=self.db.execute('SELECT * FROM review_plans WHERE subscription_id=? AND message_id=?',(p['subscription_id'],p['message_id'])).fetchone();revision=old['revision'] if old else 0
            if revision!=p['expected_revision'] or (old and old['authority']=='user' and p['authority']!='user'):raise Conflict('review revision or user override')
            analysis=self.db.execute('SELECT * FROM analyses WHERE id=? AND message_id=? AND subscription_id=?',(extra.get('analysis_id'),p['message_id'],p['subscription_id'])).fetchone()
            if not analysis:raise ValueError('review requires fixed analysis basis in same subscription')
            revision+=1;self.db.execute('INSERT INTO review_plans VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(subscription_id,message_id) DO UPDATE SET revision=excluded.revision,authority=excluded.authority,state=excluded.state,interval_seconds=excluded.interval_seconds,next_check_at=excluded.next_check_at,reason=excluded.reason',(p['subscription_id'],p['message_id'],revision,p['authority'],p['state'],p['interval_seconds'],utc(p['next_check_at']),p['reason']))
            self.db.execute('INSERT INTO plan_details VALUES(?,?,?,?,?) ON CONFLICT(subscription_id,message_id) DO UPDATE SET reevaluate_at=excluded.reevaluate_at,end_at=excluded.end_at,analysis_id=excluded.analysis_id',(p['subscription_id'],p['message_id'],utc(extra.get('reevaluate_at')),utc(extra.get('end_at')),extra.get('analysis_id')))
            self.db.execute('DELETE FROM evaluation_requests WHERE subscription_id=? AND message_id=?',(p['subscription_id'],p['message_id']))
            self.db.execute('INSERT INTO review_decisions(subscription_id,message_id,revision,value,created_at) VALUES(?,?,?,?,?)',(p['subscription_id'],p['message_id'],revision,encode(p),now()));self._change('review_plan',p['message_id'],revision);return {'revision':revision}
        return self._write(principal,key,p,write)

    def tick(self,principal,key,p):
        """Durably queue due requests; execution is a separate external worker."""
        from datetime import datetime,timedelta,timezone
        clock=utc(p['at'])
        def next_time(seconds):return (datetime.fromisoformat(clock.replace('Z','+00:00'))+timedelta(seconds=seconds)).isoformat().replace('+00:00','Z')
        def write():
            queued=[]
            for sub in self.db.execute("SELECT id FROM subscriptions WHERE status='active'").fetchall():
                sid=sub['id'];targets=[];due_discovery=False
                plan=self.db.execute("SELECT * FROM discovery_plans WHERE subscription_id=? AND state='active' AND next_at<=?",(sid,clock)).fetchone()
                if plan:due_discovery=True
                reviews=self.db.execute('SELECT p.*,d.reevaluate_at,d.end_at FROM review_plans p LEFT JOIN plan_details d USING(subscription_id,message_id) WHERE p.subscription_id=?',(sid,)).fetchall()
                for review in reviews:
                    expired=review['state']=='active' and review['end_at'] and review['end_at']<=clock
                    if (review['reevaluate_at'] and review['reevaluate_at']<=clock) or expired:
                        self.db.execute('INSERT OR IGNORE INTO evaluation_requests VALUES(?,?,?,?)',(sid,review['message_id'],'end_due' if expired else 'reevaluate_due',clock))
                    if expired and review['state']=='active':
                        self.db.execute("UPDATE review_plans SET state='ended',revision=revision+1 WHERE subscription_id=? AND message_id=?",(sid,review['message_id']));self._change('review_expired',review['message_id'],review['revision']+1)
                        self.db.execute('INSERT INTO review_decisions(subscription_id,message_id,revision,value,created_at) VALUES(?,?,?,?,?)',(sid,review['message_id'],review['revision']+1,encode({'state':'ended','authority':'system','reason':'configured end_at reached','previous_revision':review['revision']}),clock))
                    elif review['state']=='active' and review['next_check_at'] and review['next_check_at']<=clock:
                        row=self.db.execute('SELECT m.source_id,c.requested_url FROM messages m JOIN observations o ON o.message_id=m.id JOIN captures c ON c.observation_id=o.id WHERE m.id=? ORDER BY c.rowid DESC LIMIT 1',(review['message_id'],)).fetchone()
                        if row:targets.append((row['source_id'],row['requested_url']))
                if not due_discovery and not targets:continue
                active=self.db.execute("SELECT id FROM runs WHERE subscription_id=? AND status IN ('queued','running','awaiting_analysis','awaiting_executor')",(sid,)).fetchone()
                if active:
                    # Keep due times unchanged: overlapping demand survives and is queued on next tick.
                    continue
                rid=uid();self.db.execute('INSERT INTO runs(id,subscription_id,trigger_kind,status,started_at) VALUES(?,?,?,?,?)',(rid,sid,'scheduled','queued',clock));queued.append(rid)
                self.db.execute('INSERT INTO run_intents VALUES(?,?,?)',(rid,int(due_discovery),clock))
                for source,url in targets:self.db.execute('INSERT OR IGNORE INTO run_targets VALUES(?,?,?)',(rid,source,url))
                # Plan times advance on successful execution only, never on enqueue.
                self._change('run',rid,1)
            return {'queued':queued,'evaluation_requests':[dict(r) for r in self.db.execute('SELECT * FROM evaluation_requests')]}
        return self._write(principal,key,p,write)

    def finish(self,principal,key,run_id,owner,epoch,status):
        # Keep run completion and its schedule advancement in one transaction.
        p={'run_id':run_id,'owner':owner,'epoch':epoch,'status':status}
        def write():
            from datetime import datetime,timezone,timedelta
            run=self._lease(run_id,owner,epoch);items=self.db.execute('SELECT * FROM run_items WHERE run_id=?',(run_id,)).fetchall()
            if status not in ('succeeded','partial','failed'):raise ValueError('invalid finish')
            if status=='succeeded' and any(item['state'] not in ('analyzed','skipped_existing') for item in items):raise Conflict('unfinished items')
            self.db.execute('UPDATE runs SET status=?,finished_at=?,lease_until=NULL WHERE id=?',(status,now(),run_id))
            if status=='succeeded':
                plan=self.db.execute('SELECT * FROM discovery_plans WHERE subscription_id=?',(run['subscription_id'],)).fetchone()
                intent=self.db.execute('SELECT discovery FROM run_intents WHERE run_id=?',(run_id,)).fetchone()
                if plan and (intent is None or intent[0]):self.db.execute('UPDATE discovery_plans SET next_at=? WHERE subscription_id=?',((datetime.now(timezone.utc)+timedelta(seconds=plan['interval_seconds'])).isoformat().replace('+00:00','Z'),run['subscription_id']))
                for item in items:
                    review=self.db.execute('SELECT * FROM review_plans WHERE subscription_id=? AND message_id=?',(run['subscription_id'],item['message_id'])).fetchone()
                    if review and review['state']=='active' and item['observation_id'] and self.db.execute('SELECT 1 FROM run_targets WHERE run_id=? AND source_id=? AND url=?',(run_id,item['source_id'],item['target'])).fetchone():self.db.execute('UPDATE review_plans SET next_check_at=? WHERE subscription_id=? AND message_id=?',((datetime.now(timezone.utc)+timedelta(seconds=review['interval_seconds'])).isoformat().replace('+00:00','Z'),run['subscription_id'],item['message_id']))
            self._change('run',run_id,epoch);return {'run_id':run_id,'status':status}
        return self._write(principal,key,p,write)

    def analysis(self,principal,key,p):
        from .business import RESULT,validate_business
        validate_business(RESULT,p['result'])
        content=self.db.execute('SELECT content FROM message_revisions WHERE message_id=? AND revision=?',(p['message_id'],p['message_revision'])).fetchone()
        if not content:raise ValueError('unknown analysis snapshot')
        manifest={a['url']:a['name'] for a in json.loads(content[0])['attachments']}
        described=p['result']['resources']
        if len(described)!=len({a['url'] for a in described}) or {a['url'] for a in described}!=set(manifest):raise ValueError('analysis must account for every manifest resource once')
        for resource in described:
            if resource['name']!=manifest[resource['url']]:raise ValueError('resource manifest name mismatch')
            if resource['resource_version_id']:
                stored=self.verify_resource(p['message_id'],p['message_revision'],resource['resource_version_id'])
                if stored['url']!=resource['url'] or (resource['status']=='read' and stored['status']!='read') or (resource['status']=='failed' and stored['status']!='failed'):raise ValueError('resource state or URL mismatch')
        declared={r['resource_version_id'] for r in described if r['resource_version_id']}
        def verify_pins(value):
            if isinstance(value,dict):
                if value.get('resource_version_id') and value['resource_version_id'] not in declared:raise ValueError('resource evidence must be pinned in analysis resource manifest')
                for child in value.values():verify_pins(child)
            elif isinstance(value,list):
                for child in value:verify_pins(child)
        verify_pins(p['result'])
        for item in p['result']['items']:
            for material in item['materials']:
                if material['resource_version_id']:self.verify_resource(p['message_id'],p['message_revision'],material['resource_version_id'])
        return super().analysis(principal,key,p)

    def transport(self,adapter,source,run_id,url):
        """Reuse a successful or failed actual fetch only within the same scheduled batch.
        Manual requests always perform a new check. A DB lease coordinates processes.
        """
        import time
        intent=self.db.execute('SELECT batch_id FROM run_intents WHERE run_id=?',(run_id,)).fetchone()
        if intent is None:return adapter.fetch(url)
        batch=intent[0];owner=uid();deadline=time.monotonic()+30
        while True:
            row=self.db.execute('SELECT * FROM transport_cache WHERE batch_id=? AND source_id=? AND url=?',(batch,source,url)).fetchone()
            if row:return Response(row['status'],row['final_url'],(self.objects/row['raw_sha']).read_bytes(),json.loads(row['headers']))
            self.db.execute('BEGIN IMMEDIATE')
            try:
                cached=self.db.execute('SELECT * FROM transport_cache WHERE batch_id=? AND source_id=? AND url=?',(batch,source,url)).fetchone()
                if cached:
                    self.db.commit()
                    return Response(cached['status'],cached['final_url'],(self.objects/cached['raw_sha']).read_bytes(),json.loads(cached['headers']))
                lock=self.db.execute('SELECT * FROM transport_locks WHERE batch_id=? AND source_id=? AND url=?',(batch,source,url)).fetchone()
                acquired=not lock or lock['expires_at']<=time.time()
                if acquired:self.db.execute('INSERT INTO transport_locks VALUES(?,?,?,?,?) ON CONFLICT(batch_id,source_id,url) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at',(batch,source,url,owner,time.time()+25))
                self.db.commit()
            except BaseException:self.db.rollback();raise
            if acquired:break
            if time.monotonic()>=deadline:raise TimeoutError('shared fetch in progress')
            time.sleep(.05)
        try:
            response=adapter.fetch(url)
            if not response.complete:return response
            sha=self.put_object(response.body)
            safe={k.lower():str(v) for k,v in response.headers.items() if k.lower() in ('content-type','age','etag','last-modified','date','cache-control')}
            self.db.execute('BEGIN IMMEDIATE')
            try:
                lock=self.db.execute('SELECT owner FROM transport_locks WHERE batch_id=? AND source_id=? AND url=?',(batch,source,url)).fetchone()
                if not lock or lock[0]!=owner:raise Conflict('fetch lease superseded')
                self.db.execute('INSERT OR IGNORE INTO evidence_objects VALUES(?,?)',(sha,len(response.body)))
                self.db.execute('INSERT OR IGNORE INTO transport_cache VALUES(?,?,?,?,?,?,?)',(batch,source,url,response.status,response.final_url,encode(safe),sha));self.db.commit()
            except BaseException:self.db.rollback();raise
            return Response(response.status,response.final_url,response.body,safe)
        finally:self.db.execute('DELETE FROM transport_locks WHERE batch_id=? AND source_id=? AND url=? AND owner=?',(batch,source,url,owner))

    def record_discovery(self,principal,key,run_id,epoch,source,requested,response,synthetic,fetch_method='legacy'):
        sha=self.put_object(response.body)
        payload={'run_id':run_id,'epoch':epoch,'source':source,'url':requested,'response_sha':sha,'status':response.status}
        def write():
            run=self._lease(run_id,principal,epoch);self.allow_source(run['subscription_id'],source)
            safe={k.lower():str(v) for k,v in response.headers.items() if k.lower() in ('content-type','age','etag','last-modified','date','cache-control')}
            self.db.execute('INSERT OR IGNORE INTO evidence_objects VALUES(?,?)',(sha,len(response.body)))
            ident=uid();self.db.execute('INSERT INTO discovery_attempts(id,run_id,source_id,requested_url,final_url,status,headers,raw_sha,received_at,synthetic,fetch_method) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ident,run_id,source,requested,response.final_url,response.status,encode(safe),sha,now(),int(synthetic),fetch_method));self._change('discovery_attempt',run_id,epoch);return {'id':ident}
        return self._write(principal,key,payload,write)

    def _lease(self,run_id,owner,epoch):
        run=self._fence(run_id,owner,epoch)
        state=self.db.execute('SELECT status FROM subscriptions WHERE id=?',(run['subscription_id'],)).fetchone()[0]
        if state=='archived' or (state=='paused' and run['trigger_kind']!='manual'):raise Conflict('subscription blocks automatic execution')
        return run

    def analysis_is_current(self,p):
        current=self.db.execute('SELECT current_revision FROM messages WHERE id=?',(p['message_id'],)).fetchone()[0]
        if current!=p['message_revision']:return False
        for resource in p['result']['resources']:
            verified=self.db.execute("SELECT sha FROM resource_versions WHERE message_id=? AND url=? AND status='read' ORDER BY rowid DESC LIMIT 1",(p['message_id'],resource['url'])).fetchone()
            pinned=self.db.execute('SELECT sha FROM resource_versions WHERE id=?',(resource['resource_version_id'],)).fetchone() if resource['resource_version_id'] else None
            if verified and (not pinned or verified[0]!=pinned[0]):return False
        return True

    def startup_recover(self,principal,instance_id):
        """Only the server holding the exclusive process lock may call this fence."""
        def write():
            runs=self.db.execute("SELECT id,epoch FROM runs WHERE status='running'").fetchall()
            recovered=[r['id'] for r in runs]
            for run in runs:
                self.db.execute("UPDATE runs SET status='queued',owner=NULL,lease_until=NULL,epoch=epoch+1 WHERE id=?",(run['id'],));self._change('restart_recovery',run['id'],run['epoch']+1)
            from .product import recover_processing
            recover_processing(self.db,principal,recovered)
            return {'recovered_run_ids':recovered}
        return self._write(principal,'startup:'+instance_id,{'instance_id':instance_id},write)

    def _fence(self,run_id,owner,epoch):
        return super()._lease(run_id,owner,epoch)
