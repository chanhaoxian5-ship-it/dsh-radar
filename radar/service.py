"""Authenticated application boundary for host tools and the local HTTP service."""
import hashlib, json, secrets, sqlite3
from pathlib import Path
from .engine import Conflict, encode, uid, now
from .backend import Backend
from . import phase_contract, product
from .schemas import validate, SCHEMAS

class Forbidden(PermissionError): pass

class Service:
    def __init__(self, directory):
        self.plugin_version=json.loads((Path(__file__).resolve().parents[1]/'package.json').read_text(encoding='utf-8-sig'))['version'];self.runtime_state={'worker_state':'not_managed','last_error':None}
        self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
        self.engine=Backend(self.directory/'radar.sqlite3')
        self.db=self.engine.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS credentials(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, permissions TEXT NOT NULL, scopes TEXT NOT NULL, authority TEXT NOT NULL, revoked_at TEXT);
        CREATE TABLE IF NOT EXISTS source_options(source_id TEXT PRIMARY KEY REFERENCES sources(id),value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS session_bindings(session_id TEXT PRIMARY KEY,principal_id TEXT UNIQUE NOT NULL REFERENCES credentials(id));
        CREATE TABLE IF NOT EXISTS subscription_sources(subscription_id TEXT NOT NULL REFERENCES subscriptions(id),source_id TEXT NOT NULL REFERENCES sources(id),PRIMARY KEY(subscription_id,source_id));
        ''')
        from .proposals import initialize
        initialize(self.db)
        product.initialize(self.db)
        self.load_adapters()
    def load_adapters(self):
        from .sources.cau import CauAdapter
        for row in self.db.execute('SELECT * FROM sources'):
            if row['root_url'].rstrip('/')=='https://cast1.cau.edu.cn':
                option=self.db.execute('SELECT value FROM source_options WHERE source_id=?',(row['id'],)).fetchone();options=json.loads(option[0]) if option else {}
                existing=self.engine.adapters.get(row['id'])
                if existing is None or isinstance(existing,CauAdapter):self.engine.register_adapter(row['id'],CauAdapter(**options))
            elif isinstance(self.engine.adapters.get(row['id']),CauAdapter):self.engine.adapters.pop(row['id'],None)
    def provision(self, permissions, scopes, authority='agent'):
        allowed={'read','manage','collect','analyze','execute','user','delegate','schedule'}
        if not set(permissions)<=allowed or authority not in ('agent','user'): raise ValueError('invalid permissions')
        token=secrets.token_urlsafe(32); ident=uid()
        self.db.execute('INSERT INTO credentials VALUES(?,?,?,?,?,NULL)',(ident,hashlib.sha256(token.encode()).hexdigest(),encode(permissions),encode(scopes),authority))
        return token
    def reissue(self,principal):
        """Local operator-only credential rotation; preserve principal and operation receipts.
        Intentionally not exposed as an Agent or HTTP method.
        """
        token=secrets.token_urlsafe(32)
        if not self.db.execute('SELECT 1 FROM credentials WHERE id=?',(principal,)).fetchone():raise ValueError('unknown principal')
        self.db.execute('UPDATE credentials SET token_hash=?,revoked_at=NULL WHERE id=?',(hashlib.sha256(token.encode()).hexdigest(),principal))
        return token
    def authenticate(self, token):
        row=self.db.execute('SELECT * FROM credentials WHERE token_hash=? AND revoked_at IS NULL',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row: raise Forbidden('invalid credential')
        return dict(row)
    def check(self, identity, permission, sub=None):
        if permission not in json.loads(identity['permissions']): raise Forbidden('permission required: '+permission)
        scopes=json.loads(identity['scopes'])
        if sub is not None and '*' not in scopes and sub not in scopes: raise Forbidden('subscription scope')
    def sub_for_run(self, run_id):
        row=self.db.execute('SELECT subscription_id FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row: raise ValueError('run not found')
        return row['subscription_id']
    def message_visible(self, identity, mid):
        scopes=json.loads(identity['scopes'])
        if '*' in scopes: return True
        rows=self.db.execute('SELECT DISTINCT r.subscription_id FROM run_items i JOIN runs r ON r.id=i.run_id WHERE i.message_id=?',(mid,))
        return any(row[0] in scopes for row in rows)
    def call(self, token, method, args):
        return self._call(self.authenticate(token),method,args)
    def _call(self,identity,method,args):
        if method in product.PERMISSIONS:
            validate(method,args)
            return product.call(self,identity,method,dict(args))
        principal=identity['id'];args=dict(args)
        if len(encode(args).encode())>262144: raise ValueError('request too large')
        permissions={'contract':'read','subscriptions':'read','messages':'read','message':'read','run':'read','receipt':'read','changes':'read','subscription_save':'manage','source_save':'manage','run_start':'execute','recheck':'execute','claim':'execute','heartbeat':'execute','discover':'collect','observation':'collect','analysis':'analyze','review':'analyze','cancel':'execute','finish':'execute','capture':'collect','discovery_plan':'analyze','tick':'schedule','plans':'read','diff':'read','retry_run':'execute','evidence_read':'read','runs':'read','sources':'read','rule_proposal_create':'analyze','rule_proposals':'read','rule_proposal_decide':'user','runtime':'read'}
        permissions.update(product.PERMISSIONS)
        if method=='session_dispatch':
            self.check(identity,'delegate');validate(method,args)
            if args['method']=='session_dispatch':raise Forbidden('recursive delegation rejected')
            def bind():
                old=self.db.execute('SELECT principal_id FROM session_bindings WHERE session_id=?',(args['session_id'],)).fetchone()
                if old:return {'principal_id':old[0]}
                ident=uid()
                self.db.execute('INSERT INTO credentials VALUES(?,?,?,?,?,NULL)',(ident,hashlib.sha256(secrets.token_bytes(32)).hexdigest(),encode(['read','manage','analyze','execute']),identity['scopes'],'agent'))
                self.db.execute('INSERT INTO session_bindings VALUES(?,?)',(args['session_id'],ident))
                return {'principal_id':ident}
            binding=self.engine._write(principal,'session-bind:'+hashlib.sha256(args['session_id'].encode()).hexdigest(),{'session_id':args['session_id']},bind)
            bound=self.db.execute('SELECT * FROM credentials WHERE id=? AND revoked_at IS NULL',(binding['principal_id'],)).fetchone()
            if not bound:raise Forbidden('session credential revoked')
            return self._call(dict(bound),args['method'],args['args'])
        permission=permissions.get(method)
        if permission is None: raise ValueError('unknown method')
        self.check(identity,permission)
        validate(method,args)
        if method=='runtime':
            import os
            return {'plugin_version':self.plugin_version,'pid':os.getpid(),**self.runtime_state}
        if method=='contract':
            visible=[m for m in SCHEMAS if m!='observation' and permissions.get(m) in json.loads(identity['permissions'])]
            target=args.get('for_method')
            if target and target not in visible:raise Forbidden('requested method is not available to this principal')
            return {'plugin_version':self.plugin_version,'schema_version':1,'business_schema_version':2,'service_epoch':self.db.execute("SELECT value FROM meta WHERE key='service_epoch'").fetchone()[0],'methods':visible,'permanent_delete':False,'schema_lookup':'contract with for_method','schemas':{m:SCHEMAS[m] for m in ([target] if target else visible) if args.get('include_schemas',True)}}
        if method=='sources':
            scopes=json.loads(identity['scopes']);result=[]
            for source in self.db.execute('SELECT s.*,COALESCE((SELECT MAX(revision) FROM source_revisions r WHERE r.source_id=s.id),0) revision FROM sources s'):
                if '*' not in scopes and not any(self.db.execute('SELECT 1 FROM subscription_sources WHERE source_id=? AND subscription_id=?',(source['id'],sub)).fetchone() for sub in scopes):continue
                row=dict(source);options=self.db.execute('SELECT value FROM source_options WHERE source_id=?',(source['id'],)).fetchone();row['options']=json.loads(options[0]) if options else {};result.append(row)
            return result
        if method=='runs':
            if args.get('subscription_id'):self.check(identity,'read',args['subscription_id'])
            scopes=json.loads(identity['scopes']);limit=args.get('limit',50);rows=[]
            for row in self.db.execute('SELECT * FROM runs ORDER BY started_at DESC'):
                if '*' not in scopes and row['subscription_id'] not in scopes:continue
                if args.get('subscription_id') and row['subscription_id']!=args['subscription_id']:continue
                rows.append(dict(row))
                if len(rows)>=limit:break
            return rows
        if method=='subscriptions':
            rows=[dict(r) for r in self.db.execute('SELECT * FROM subscriptions ORDER BY id')]
            scopes=json.loads(identity['scopes']); return [r for r in rows if '*' in scopes or r['id'] in scopes]
        if method=='receipt': return self.engine.receipt(principal,args['operation_key'])
        if method in ('messages','message'):
            if method=='message':
                mid=args['message_id']
                if not self.message_visible(identity,mid): raise Forbidden('message scope')
                row=self.db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
                if not row: raise ValueError('message not found')
                result=dict(row); result['resources']=[dict(r) for r in self.db.execute('SELECT * FROM resource_versions WHERE message_id=? ORDER BY rowid',(mid,))]; result['revisions']=[dict(r) for r in self.db.execute('SELECT * FROM message_revisions WHERE message_id=? ORDER BY revision',(mid,))]
                scopes=json.loads(identity['scopes']); result['matches']=[dict(r) for r in self.db.execute('SELECT * FROM matches WHERE message_id=?',(mid,)) if '*' in scopes or r['subscription_id'] in scopes]; result['analyses']=[dict(r) for r in self.db.execute('SELECT * FROM analyses WHERE message_id=?',(mid,)) if '*' in scopes or r['subscription_id'] in scopes]
                for analysis in result['analyses']:
                    analysis['stale']=analysis['message_revision']!=row['current_revision'];analysis['resource_state_unknown']=False
                    for resource in json.loads(analysis['result']).get('resources',[]):
                        latest=self.db.execute('SELECT * FROM resource_versions WHERE message_id=? AND url=? ORDER BY rowid DESC LIMIT 1',(mid,resource['url'])).fetchone()
                        if latest and latest['status']!='read':analysis['resource_state_unknown']=True;analysis['stale']=True
                        verified=self.db.execute("SELECT sha FROM resource_versions WHERE message_id=? AND url=? AND status='read' ORDER BY rowid DESC LIMIT 1",(mid,resource['url'])).fetchone()
                        referenced=self.db.execute('SELECT sha FROM resource_versions WHERE id=?',(resource['resource_version_id'],)).fetchone() if resource['resource_version_id'] else None
                        if verified and (not referenced or referenced[0]!=verified[0]):analysis['stale']=True
                return result
            limit=args.get('limit',50)
            if type(limit) is not int or not 1<=limit<=100: raise ValueError('invalid limit')
            rows=self.db.execute('SELECT m.*,r.content FROM messages m LEFT JOIN message_revisions r ON r.message_id=m.id AND r.revision=m.current_revision WHERE m.id>? ORDER BY m.id',(args.get('after',''),))
            visible=[]
            for row in rows:
                if self.message_visible(identity,row['id']): visible.append(dict(row))
                if len(visible)>=limit: break
            return {'items':visible,'next_cursor':visible[-1]['id'] if visible else None}
        if method=='changes':
            epoch=self.db.execute("SELECT value FROM meta WHERE key='service_epoch'").fetchone()[0]
            if args.get('service_epoch')!=epoch: raise Conflict('service_epoch_changed: rebuild consumer')
            after=args.get('after',0)
            if type(after) is not int or after<0: raise ValueError('invalid cursor')
            # Scoped consumers receive only message events; no foreign entity IDs leak.
            rows=[dict(r) for r in self.db.execute('SELECT * FROM changes WHERE seq>? ORDER BY seq LIMIT 100',(after,))]
            scopes=json.loads(identity['scopes']); visible=[r for r in rows if '*' in scopes or (r['kind']=='message_revision' and self.message_visible(identity,r['entity_id']))]
            return {'items':visible,'next_cursor':rows[-1]['seq'] if rows else after,'service_epoch':epoch}
        key=args.pop('operation_key',None)
        if method=='rule_proposals':
            self.check(identity,'read',args['subscription_id'])
            return [dict(r) for r in self.db.execute('SELECT * FROM rule_proposals WHERE subscription_id=? ORDER BY created_at DESC',(args['subscription_id'],))]
        if method=='rule_proposal_create':
            from .proposals import create
            return create(self,identity,key,args)
        if method=='rule_proposal_decide':
            from .proposals import decide
            return decide(self,identity,key,args)
        if method=='subscription_save':
            self.check(identity,'manage',args['id']); return self.engine.save_subscription(principal,key,args)
        if method=='source_save':
            if '*' not in json.loads(identity['scopes']): raise Forbidden('global management required')
            if set(args) not in ({'id','name','root_url','expected_revision'},{'id','name','root_url','expected_revision','options'}): raise ValueError('invalid source')
            from urllib.parse import urlsplit
            source_url=urlsplit(args['root_url'])
            if source_url.scheme not in ('http','https') or not source_url.netloc or source_url.username or source_url.password or source_url.query or source_url.fragment:raise ValueError('source root must be a public HTTP origin/path without credentials or query')
            if source_url.netloc=='cast1.cau.edu.cn' and 'options' in args:
                from .sources.cau import CauAdapter
                CauAdapter(**args['options'])
            def write():
                revision=self.db.execute('SELECT COALESCE(MAX(revision),0) FROM source_revisions WHERE source_id=?',(args['id'],)).fetchone()[0]
                if revision!=args['expected_revision']:raise Conflict('source revision')
                existing=self.db.execute('SELECT root_url FROM sources WHERE id=?',(args['id'],)).fetchone()
                if existing and existing[0]!=args['root_url'] and self.db.execute('SELECT 1 FROM messages WHERE source_id=?',(args['id'],)).fetchone():raise Conflict('source origin with history is immutable')
                self.db.execute('INSERT INTO sources VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,root_url=excluded.root_url',(args['id'],args['name'],args['root_url']))
                old_options=self.db.execute('SELECT value FROM source_options WHERE source_id=?',(args['id'],)).fetchone();effective={**args,'options':args.get('options',json.loads(old_options[0]) if old_options else {})}
                self.db.execute('INSERT INTO source_options VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET value=excluded.value',(args['id'],encode(effective['options'])))
                self.db.execute('INSERT INTO source_revisions VALUES(?,?,?,?)',(args['id'],revision+1,encode(effective),now()));self.engine._change('source',args['id'],revision+1); return {'id':args['id'],'revision':revision+1}
            result=self.engine._write(principal,key,args,write);self.load_adapters();return result
        if method=='discovery_plan':
            self.check(identity,permission,args['subscription_id'])
            if args['authority']!=identity['authority']:raise Forbidden('authority cannot be self-assigned')
            return self.engine.save_discovery_plan(principal,key,args)
        if method=='tick':
            if '*' not in json.loads(identity['scopes']):raise Forbidden('scheduler requires explicit all-subscription execution scope')
            return self.engine.tick(principal,key,args)
        if method=='plans':
            self.check(identity,'read',args['subscription_id'])
            return {'coverage':[{'source_id':r['source_id'],**json.loads(r['value'])} for r in self.db.execute('SELECT * FROM checkpoints WHERE subscription_id=?',(args['subscription_id'],))],'last_successful_run_at':self.db.execute("SELECT MAX(finished_at) FROM runs WHERE subscription_id=? AND status='succeeded'",(args['subscription_id'],)).fetchone()[0],'discovery':[dict(r) for r in self.db.execute('SELECT * FROM discovery_plans WHERE subscription_id=?',(args['subscription_id'],))],'reviews':[dict(r) for r in self.db.execute('SELECT p.*,d.reevaluate_at,d.end_at,d.analysis_id FROM review_plans p LEFT JOIN plan_details d USING(subscription_id,message_id) WHERE p.subscription_id=?',(args['subscription_id'],))],'evaluations':[dict(r) for r in self.db.execute('SELECT * FROM evaluation_requests WHERE subscription_id=?',(args['subscription_id'],))]}
        if method=='diff':
            import difflib
            if not self.message_visible(identity,args['message_id']):raise Forbidden('message scope')
            revisions=[]
            for revision in (args['before'],args['after']):
                row=self.db.execute('SELECT content FROM message_revisions WHERE message_id=? AND revision=?',(args['message_id'],revision)).fetchone()
                if not row:raise ValueError('revision not found')
                content=json.loads(row[0]);lines=[content['title']]
                if content.get('published_at_raw'):lines.append('发布时间：'+content['published_at_raw'])
                lines.extend(b['text'] for b in content['blocks'])
                lines.extend('附件：'+a['name']+' '+a['url'] for a in content['attachments'])
                revisions.append('\n'.join(lines).splitlines())
            grouped={}
            for resource in self.db.execute('SELECT * FROM resource_versions WHERE message_id=? ORDER BY rowid',(args['message_id'],)):grouped.setdefault(resource['url'],[]).append(dict(resource))
            resource_changes=[]
            for versions in grouped.values():
                latest=versions[-1];success=[r for r in versions if r['status']=='read']
                if latest['status']!='read':resource_changes.append({'name':latest['name'],'state':'unknown','observed_at':latest['observed_at']})
                elif len(success)>1 and success[-1]['sha']!=success[-2]['sha']:resource_changes.append({'name':latest['name'],'state':'changed','observed_at':latest['observed_at']})
            return {'diff':list(difflib.unified_diff(*revisions,fromfile='修订'+str(args['before']),tofile='修订'+str(args['after']))),'resource_changes':resource_changes}
        if method=='review':
            self.check(identity,permission,args['subscription_id'])
            if args['authority']!=identity['authority']: raise Forbidden('authority cannot be self-assigned')
            if not self.message_visible(identity,args['message_id']): raise Forbidden('message scope')
            return self.engine.review(principal,key,args)
        if method=='recheck':
            self.check(identity,'execute',args['subscription_id'])
            return self.engine.recheck(principal,key,args)
        if method=='run_start':
            self.check(identity,permission,args['subscription_id'])
            return self.engine.start(principal,key,args['subscription_id'],args['trigger'])
        sub=self.sub_for_run(args['run_id']); self.check(identity,permission,sub)
        if method=='run':
            row=dict(self.db.execute('SELECT * FROM runs WHERE id=?',(args['run_id'],)).fetchone()); row['errors']=[dict(r) for r in self.db.execute('SELECT stage,error,created_at FROM execution_errors WHERE run_id=?',(args['run_id'],))]; row['items']=[dict(r) for r in self.db.execute('SELECT * FROM run_items WHERE run_id=?',(args['run_id'],))];
            for item in row['items']:
                observation=self.db.execute('SELECT freshness,body_status,asset_status,http_status,error FROM observations WHERE id=?',(item['observation_id'],)).fetchone()
                item['last_observation']=dict(observation) if observation else None
            return row
        # Authenticated worker identity, never producer-supplied session text.
        if method in ('observation','analysis'):
            if args.get('owner')!=principal: raise Forbidden('worker owner mismatch')
            return getattr(self.engine,method)(principal,key,args)
        if method=='evidence_read':
            import base64,re
            observation=self.db.execute('SELECT * FROM observations WHERE id=? AND run_id=?',(args['observation_id'],args['run_id'])).fetchone()
            if not observation:raise ValueError('observation outside requested run')
            if args['resource_version_id']:
                resource=self.db.execute('SELECT * FROM resource_versions WHERE id=? AND message_id=? AND message_revision=?',(args['resource_version_id'],observation['message_id'],observation['accepted_revision'])).fetchone()
                if not resource:raise Forbidden('resource outside observation snapshot')
                sha=resource['sha'];status=resource['status']
            else:
                capture=self.db.execute('SELECT raw_sha FROM captures WHERE observation_id=?',(args['observation_id'],)).fetchone()
                if not capture:raise ValueError('raw capture unavailable')
                sha=capture[0];status=observation['body_status']
            if sha is None:return {'status':status,'data_base64':None,'sha256':None}
            if not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('invalid object identity')
            body=(self.engine.objects/sha).read_bytes()
            if hashlib.sha256(body).hexdigest()!=sha:raise ValueError('corrupt evidence object')
            return {'status':status,'sha256':sha,'size':len(body),'offset':args['offset'],'data_base64':base64.b64encode(body[args['offset']:args['offset']+args['length']]).decode('ascii')}
        if method=='capture': return self.engine.capture(principal,key,args)
        if method=='claim': return self.engine.lease_action(principal,key,'claim',args)
        if method=='heartbeat': return self.engine.lease_action(principal,key,'heartbeat',args)
        if method=='discover': return self.engine.discovery(principal,key,args)
        if method=='retry_run':
            def write():
                run=self.db.execute('SELECT * FROM runs WHERE id=?',(args['run_id'],)).fetchone()
                if run['epoch']!=args['expected_epoch'] or run['status'] not in ('failed','partial','awaiting_executor','awaiting_analysis','queued'):raise Conflict('retry state changed')
                self.db.execute("UPDATE runs SET status='queued',owner=NULL,lease_until=NULL,cancel_requested=0 WHERE id=?",(args['run_id'],));self.engine._change('run_retry',args['run_id'],run['epoch']);return {'run_id':args['run_id'],'status':'queued'}
            return self.engine._write(principal,key,args,write)
        if method=='cancel': return self.engine.cancel(principal,key,args['run_id'])
        if method=='finish': return self.engine.finish(principal,key,args['run_id'],principal,args['epoch'],args['status'])

    def close(self): self.engine.close()
