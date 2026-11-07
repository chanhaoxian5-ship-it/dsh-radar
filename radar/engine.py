"""Isolated radar persistence prototype. Not a network API or installed plugin."""
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def uid():
    return uuid.uuid4().hex


class Conflict(ValueError):
    pass


class Radar:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY, name TEXT NOT NULL, rules TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','paused','archived')), revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, name TEXT NOT NULL, root_url TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL REFERENCES subscriptions(id), trigger_kind TEXT NOT NULL, status TEXT NOT NULL, owner TEXT, epoch INTEGER NOT NULL DEFAULT 0, lease_until TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, finished_at TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(subscription_id) WHERE status IN ('queued','running','awaiting_analysis','awaiting_executor');
        CREATE TABLE IF NOT EXISTS run_items(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), source_id TEXT NOT NULL REFERENCES sources(id), target TEXT NOT NULL, state TEXT NOT NULL, observation_id TEXT, message_id TEXT, message_revision INTEGER, analysis_id TEXT, error TEXT, UNIQUE(run_id,source_id,target));
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), external_key TEXT NOT NULL, current_revision INTEGER NOT NULL DEFAULT 0, first_seen_at TEXT NOT NULL, last_checked_at TEXT NOT NULL, UNIQUE(source_id,external_key));
        CREATE TABLE IF NOT EXISTS message_revisions(message_id TEXT NOT NULL REFERENCES messages(id), revision INTEGER NOT NULL, observed_at TEXT NOT NULL, content TEXT NOT NULL, hash TEXT NOT NULL, extractor_version TEXT NOT NULL, PRIMARY KEY(message_id,revision));
        CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), item_id TEXT NOT NULL REFERENCES run_items(id), message_id TEXT, accepted_revision INTEGER, received_at TEXT NOT NULL, freshness TEXT NOT NULL, body_status TEXT NOT NULL, asset_status TEXT NOT NULL, http_status INTEGER, error TEXT);
        CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY, message_id TEXT NOT NULL, message_revision INTEGER NOT NULL, subscription_id TEXT NOT NULL REFERENCES subscriptions(id), revision INTEGER NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(message_id,message_revision) REFERENCES message_revisions(message_id,revision), UNIQUE(message_id,message_revision,subscription_id,revision));
        CREATE TABLE IF NOT EXISTS match_revisions(id INTEGER PRIMARY KEY AUTOINCREMENT, subscription_id TEXT NOT NULL, message_id TEXT NOT NULL, message_revision INTEGER NOT NULL, analysis_id TEXT NOT NULL REFERENCES analyses(id), decision TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS matches(subscription_id TEXT NOT NULL, message_id TEXT NOT NULL, message_revision INTEGER NOT NULL, analysis_id TEXT NOT NULL REFERENCES analyses(id), decision TEXT NOT NULL, PRIMARY KEY(subscription_id,message_id));
        CREATE TABLE IF NOT EXISTS review_plans(subscription_id TEXT NOT NULL REFERENCES subscriptions(id), message_id TEXT NOT NULL REFERENCES messages(id), revision INTEGER NOT NULL, authority TEXT NOT NULL, state TEXT NOT NULL, interval_seconds INTEGER, next_check_at TEXT, reason TEXT NOT NULL, PRIMARY KEY(subscription_id,message_id));
        CREATE TABLE IF NOT EXISTS review_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, subscription_id TEXT NOT NULL, message_id TEXT NOT NULL, revision INTEGER NOT NULL, value TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS operations(principal TEXT NOT NULL, operation_key TEXT NOT NULL, request_hash TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(principal,operation_key));
        CREATE TABLE IF NOT EXISTS changes(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL);
        ''')
        self.db.execute("INSERT OR IGNORE INTO meta VALUES('service_epoch',?)", (uid(),))

    def close(self):
        self.db.close()

    def _write(self, principal, key, payload, action):
        if not isinstance(principal, str) or not principal or not isinstance(key, str) or not key or len(key) > 200:
            raise ValueError('principal and operation key required')
        fingerprint = digest({"operation": action.__qualname__, "payload": payload})
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.db.execute('SELECT request_hash,receipt FROM operations WHERE principal=? AND operation_key=?', (principal,key)).fetchone()
            if old:
                if old['request_hash'] != fingerprint:
                    raise Conflict('operation key reused with different request')
                self.db.commit()
                return json.loads(old['receipt'])
            result = action()
            self.db.execute('INSERT INTO operations VALUES(?,?,?,?)', (principal,key,fingerprint,encode(result)))
            self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise

    def _change(self, kind, entity, revision):
        self.db.execute('INSERT INTO changes(kind,entity_id,revision,created_at) VALUES(?,?,?,?)',(kind,entity,revision,now()))

    def subscription(self, principal, key, value):
        def write():
            if set(value) != {'id','name','rules','status','expected_revision'} or value['status'] not in ('active','paused','archived') or not isinstance(value['rules'],dict) or not value['name']:
                raise ValueError('invalid subscription')
            row=self.db.execute('SELECT revision FROM subscriptions WHERE id=?',(value['id'],)).fetchone()
            rev=row['revision'] if row else 0
            if rev != value['expected_revision']: raise Conflict('subscription revision')
            self.db.execute('INSERT INTO subscriptions VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,rules=excluded.rules,status=excluded.status,revision=excluded.revision',(value['id'],value['name'],encode(value['rules']),value['status'],rev+1))
            self._change('subscription',value['id'],rev+1)
            return {'id':value['id'],'revision':rev+1}
        return self._write(principal,key,value,write)

    def source(self, source_id, name, url):
        self.db.execute('INSERT INTO sources VALUES(?,?,?)',(source_id,name,url))

    def start(self, principal, key, subscription_id, trigger):
        payload={'subscription_id':subscription_id,'trigger':trigger}
        def write():
            sub=self.db.execute('SELECT status FROM subscriptions WHERE id=?',(subscription_id,)).fetchone()
            if not sub or sub['status']=='archived' or (sub['status']=='paused' and trigger!='manual'): raise Conflict('subscription unavailable')
            if trigger not in ('manual','scheduled','event'): raise ValueError('invalid trigger')
            active=self.db.execute("SELECT id FROM runs WHERE subscription_id=? AND status IN ('queued','running','awaiting_analysis','awaiting_executor')",(subscription_id,)).fetchone()
            if active:
                intent=self.db.execute('SELECT discovery FROM run_intents WHERE run_id=?',(active['id'],)).fetchone() if hasattr(self,'adapters') else None
                if intent and not intent[0]:
                    row=self.db.execute('SELECT status FROM runs WHERE id=?',(active['id'],)).fetchone()
                    if row[0]!='queued' or self.db.execute('SELECT 1 FROM run_items WHERE run_id=?',(active['id'],)).fetchone():
                        raise Conflict('recheck already executing; retry discovery after it finishes')
                    self.db.execute('UPDATE run_intents SET discovery=1 WHERE run_id=?',(active['id'],))
                return {'run_id':active['id'],'existing':True}
            rid=uid(); self.db.execute('INSERT INTO runs(id,subscription_id,trigger_kind,status,started_at) VALUES(?,?,?,?,?)',(rid,subscription_id,trigger,'queued',now()))
            self._change('run',rid,1)
            return {'run_id':rid,'existing':False}
        return self._write(principal,key,payload,write)

    def claim(self, run_id, owner, seconds=60):
        if not owner or not 1 <= seconds <= 3600: raise ValueError('invalid lease')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
            if not row or row['cancel_requested'] or row['status'] not in ('queued','running') or (row['status']=='running' and row['lease_until'] and row['lease_until'] > now()): raise Conflict('run unavailable')
            epoch=row['epoch']+1
            until=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='microseconds').replace('+00:00','Z')
            self.db.execute("UPDATE runs SET status='running',owner=?,epoch=?,lease_until=? WHERE id=?",(owner,epoch,until,run_id)); self.db.commit()
            return {'owner':owner,'epoch':epoch,'lease_until':until}
        except BaseException:
            self.db.rollback(); raise

    def _lease(self, run_id, owner, epoch):
        row=self.db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row or row['status']!='running' or row['owner']!=owner or row['epoch']!=epoch or row['cancel_requested'] or not row['lease_until'] or row['lease_until']<=now(): raise Conflict('lease expired or superseded')
        return row

    def discover(self, run_id, owner, epoch, source_id, targets):
        if not isinstance(targets,list) or len(targets)>500 or any(not isinstance(t,str) or not t or len(t)>2000 for t in targets): raise ValueError('invalid targets')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run_id,owner,epoch)
            for target in targets: self.db.execute('INSERT OR IGNORE INTO run_items(id,run_id,source_id,target,state) VALUES(?,?,?,?,?)',(uid(),run_id,source_id,target,'discovered'))
            result=[dict(r) for r in self.db.execute('SELECT * FROM run_items WHERE run_id=? AND source_id=?',(run_id,source_id))]
            self.db.commit(); return result
        except BaseException: self.db.rollback(); raise

    def observation(self, principal, key, p):
        def write():
            required={'run_id','item_id','owner','epoch','external_key','freshness','body_status','asset_status','http_status','content','extractor_version','error'}
            if set(p)!=required or p['freshness'] not in ('fresh','cached','unknown') or p['body_status'] not in ('complete','partial','failed') or p['asset_status'] not in ('complete','partial','unknown'): raise ValueError('invalid observation')
            self._lease(p['run_id'],p['owner'],p['epoch'])
            item=self.db.execute('SELECT * FROM run_items WHERE id=? AND run_id=?',(p['item_id'],p['run_id'])).fetchone()
            if not item: raise ValueError('unknown item')
            msg=self.db.execute('SELECT * FROM messages WHERE source_id=? AND external_key=?',(item['source_id'],p['external_key'])).fetchone()
            mid=msg['id'] if msg else None; revision=msg['current_revision'] if msg else None
            valid=p['freshness']=='fresh' and p['body_status']=='complete' and p['http_status']==200
            if valid:
                c=p['content']
                if not isinstance(c,dict) or set(c)!={'title','blocks','attachments'} or not isinstance(c['title'],str) or not c['title'] or not isinstance(c['blocks'],list) or not c['blocks'] or not isinstance(c['attachments'],list): raise ValueError('invalid content')
                ids=[]
                for block in c['blocks']:
                    if not isinstance(block,dict) or set(block)!={'block_id','type','text'} or block['type'] not in ('paragraph','heading','table','image') or not isinstance(block['text'],str) or not isinstance(block['block_id'],str) or not block['block_id']: raise ValueError('invalid block')
                    ids.append(block['block_id'])
                if len(ids)!=len(set(ids)): raise ValueError('duplicate block')
                if not mid:
                    mid=uid(); revision=0; self.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,item['source_id'],p['external_key'],0,now(),now()))
                previous=self.db.execute('SELECT * FROM message_revisions WHERE message_id=? AND revision=?',(mid,revision)).fetchone() if revision else None
                if previous and previous['extractor_version']!=p['extractor_version']: raise Conflict('extractor versions not comparable')
                if not previous or previous['hash']!=digest(c):
                    revision+=1
                    self.db.execute('INSERT INTO message_revisions VALUES(?,?,?,?,?,?)',(mid,revision,now(),encode(c),digest(c),p['extractor_version']))
                    self.db.execute('UPDATE messages SET current_revision=? WHERE id=?',(revision,mid)); self._change('message_revision',mid,revision)
                self.db.execute('UPDATE messages SET last_checked_at=? WHERE id=?',(now(),mid))
            oid=uid(); self.db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(oid,p['run_id'],p['item_id'],mid,revision,now(),p['freshness'],p['body_status'],p['asset_status'],p['http_status'],p['error']))
            self.db.execute('UPDATE run_items SET observation_id=?,message_id=?,message_revision=?,state=? WHERE id=?',(oid,mid,revision,'collected' if valid else 'fetch_failed',p['item_id']))
            return {'observation_id':oid,'message_id':mid,'message_revision':revision}
        return self._write(principal,key,p,write)

    def analysis(self, principal, key, p):
        def write():
            required={'run_id','item_id','owner','epoch','message_id','message_revision','expected_revision','result'}
            if set(p)!=required: raise ValueError('invalid analysis')
            run=self._lease(p['run_id'],p['owner'],p['epoch']); item=self.db.execute('SELECT * FROM run_items WHERE id=? AND run_id=?',(p['item_id'],p['run_id'])).fetchone()
            if not item or item['message_id']!=p['message_id'] or item['message_revision']!=p['message_revision']: raise Conflict('item mismatch')
            source=self.db.execute('SELECT content FROM message_revisions WHERE message_id=? AND revision=?',(p['message_id'],p['message_revision'])).fetchone()
            result=p['result']
            if not source or not isinstance(result,dict) or (result.get('schema_version')!=2 and set(result)!={'summary','items','decision','reason','uncertainties'}) or result['decision'] not in ('relevant','irrelevant','uncertain') or not isinstance(result['items'],list) or not isinstance(result['uncertainties'],list): raise ValueError('invalid result')
            blocks={b['block_id']:b['text'] for b in json.loads(source['content'])['blocks']}
            if result.get('schema_version')==2:
                from .business import RESULT, validate_business
                validate_business(RESULT,result)
                def walk(value):
                    if isinstance(value,dict):
                        if set(value)=={'message_id','message_revision','block_id','resource_version_id','row','column','quote'}:
                            self.verify_evidence(p['message_id'],p['message_revision'],value)
                        for child in value.values(): walk(child)
                    elif isinstance(value,list):
                        for child in value: walk(child)
                walk(result)
                for resource in result['resources']:
                    if resource['resource_version_id']:self.verify_resource(p['message_id'],p['message_revision'],resource['resource_version_id'])
                    elif resource['status']!='unread':raise ValueError('resource without immutable version must be unread')
            else:
                for entry in result['items']:
                    if not isinstance(entry,dict) or set(entry)!={'action','evidence'} or not isinstance(entry['evidence'],list) or not entry['evidence']: raise ValueError('evidence required')
                    for ev in entry['evidence']:
                        if set(ev)!={'block_id','quote'} or ev['block_id'] not in blocks or not isinstance(ev['quote'],str) or not ev['quote'] or ev['quote'] not in blocks[ev['block_id']]: raise ValueError('invalid evidence')
            sub=run['subscription_id']; old=self.db.execute('SELECT COALESCE(MAX(revision),0) n FROM analyses WHERE message_id=? AND message_revision=? AND subscription_id=?',(p['message_id'],p['message_revision'],sub)).fetchone()['n']
            if old!=p['expected_revision']: raise Conflict('analysis revision')
            aid=uid(); rev=old+1
            self.db.execute('INSERT INTO analyses VALUES(?,?,?,?,?,?,?)',(aid,p['message_id'],p['message_revision'],sub,rev,encode(result),now()))
            self.db.execute('INSERT INTO match_revisions(subscription_id,message_id,message_revision,analysis_id,decision,reason,created_at) VALUES(?,?,?,?,?,?,?)',(sub,p['message_id'],p['message_revision'],aid,result['decision'],result['reason'],now()))
            if not hasattr(self,'analysis_is_current') or self.analysis_is_current(p):
                self.db.execute('INSERT INTO matches VALUES(?,?,?,?,?) ON CONFLICT(subscription_id,message_id) DO UPDATE SET message_revision=excluded.message_revision,analysis_id=excluded.analysis_id,decision=excluded.decision WHERE excluded.message_revision>=matches.message_revision',(sub,p['message_id'],p['message_revision'],aid,result['decision']))
            self.db.execute("UPDATE run_items SET analysis_id=?,state='analyzed' WHERE id=?",(aid,p['item_id']))
            from .product import complete_analysis
            complete_analysis(self.db,principal,sub,p['message_id'],p['message_revision'],aid,result)
            self._change('analysis',aid,rev)
            return {'analysis_id':aid,'revision':rev,'message_revision':p['message_revision']}
        return self._write(principal,key,p,write)

    def review(self, principal, key, p):
        def write():
            if set(p)!={'subscription_id','message_id','expected_revision','authority','state','interval_seconds','next_check_at','reason'} or p['authority'] not in ('user','agent') or p['state'] not in ('active','paused','ended') or not p['reason']: raise ValueError('invalid plan')
            if p['state']=='active' and (not isinstance(p['interval_seconds'],int) or not 3600<=p['interval_seconds']<=31536000 or not p['next_check_at']): raise ValueError('invalid active plan')
            old=self.db.execute('SELECT * FROM review_plans WHERE subscription_id=? AND message_id=?',(p['subscription_id'],p['message_id'])).fetchone()
            rev=old['revision'] if old else 0
            if rev!=p['expected_revision'] or (old and old['authority']=='user' and p['authority']=='agent'): raise Conflict('review revision or user override')
            rev+=1
            self.db.execute('INSERT INTO review_plans VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(subscription_id,message_id) DO UPDATE SET revision=excluded.revision,authority=excluded.authority,state=excluded.state,interval_seconds=excluded.interval_seconds,next_check_at=excluded.next_check_at,reason=excluded.reason',(p['subscription_id'],p['message_id'],rev,p['authority'],p['state'],p['interval_seconds'],p['next_check_at'],p['reason']))
            self.db.execute('INSERT INTO review_decisions(subscription_id,message_id,revision,value,created_at) VALUES(?,?,?,?,?)',(p['subscription_id'],p['message_id'],rev,encode(p),now())); self._change('review_plan',p['message_id'],rev)
            return {'revision':rev}
        return self._write(principal,key,p,write)

    def receipt(self, principal, key):
        row=self.db.execute('SELECT receipt FROM operations WHERE principal=? AND operation_key=?',(principal,key)).fetchone()
        return json.loads(row['receipt']) if row else None

    def heartbeat(self, run_id, owner, epoch, seconds=60):
        if not isinstance(seconds,int) or not 1<=seconds<=3600: raise ValueError('invalid lease duration')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._lease(run_id,owner,epoch)
            until=(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat(timespec='microseconds').replace('+00:00','Z')
            self.db.execute('UPDATE runs SET lease_until=? WHERE id=?',(until,run_id)); self.db.commit()
            return {'lease_until':until,'epoch':epoch}
        except BaseException: self.db.rollback(); raise

    def cancel(self, principal, key, run_id):
        def write():
            row=self.db.execute('SELECT status FROM runs WHERE id=?',(run_id,)).fetchone()
            if not row: raise ValueError('run not found')
            if row['status'] in ('queued','running','awaiting_analysis','awaiting_executor'):
                self.db.execute("UPDATE runs SET cancel_requested=1,status='cancelled',finished_at=? WHERE id=?",(now(),run_id))
                self._change('run',run_id,2)
                return {'run_id':run_id,'status':'cancelled'}
            return {'run_id':run_id,'status':row['status']}
        return self._write(principal,key,{'run_id':run_id},write)

    def finish(self, principal, key, run_id, owner, epoch, status):
        p={'run_id':run_id,'owner':owner,'epoch':epoch,'status':status}
        def write():
            self._lease(run_id,owner,epoch)
            if status not in ('succeeded','partial','failed'): raise ValueError('invalid final status')
            states=[r['state'] for r in self.db.execute('SELECT state FROM run_items WHERE run_id=?',(run_id,))]
            if status=='succeeded' and any(state not in ('analyzed','skipped_existing') for state in states): raise Conflict('unfinished items')
            self.db.execute('UPDATE runs SET status=?,finished_at=?,lease_until=NULL WHERE id=?',(status,now(),run_id)); self._change('run',run_id,2)
            return {'run_id':run_id,'status':status}
        return self._write(principal,key,p,write)
