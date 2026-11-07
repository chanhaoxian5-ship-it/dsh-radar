"""Coordinate leased collection runs and invoke an optional external analysis callback without launching a model client or secondary Agent runtime."""
from .engine import uid,now,encode

class Worker:
    def __init__(self,service,token,analyze):self.service=service;self.token=token;self.analyze=analyze
    def call(self,method,**args):return self.service.call(self.token,method,args)
    def step(self,run_id):
        s=self.service;e=s.engine
        run=self.call('run',run_id=run_id)
        # One credential explicitly combines this executor's collection/analysis rights.
        lease=self.call('claim',operation_key=f"claim:{run_id}:{run['epoch']+1}",run_id=run_id,seconds=600)
        base={'run_id':run_id,'epoch':lease['epoch']}
        forced_rechecks={(r['source_id'],r['url']) for r in s.db.execute('SELECT source_id,url FROM run_targets WHERE run_id=?',(run_id,))}
        stage='discovery';current_item=None
        try:
            existing=run['items']
            if not existing:
                targets=[dict(r) for r in s.db.execute('SELECT * FROM run_targets WHERE run_id=?',(run_id,))]
                grouped={}
                for row in targets:grouped.setdefault(row['source_id'],[]).append(row['url'])
                intent=s.db.execute('SELECT discovery FROM run_intents WHERE run_id=?',(run_id,)).fetchone()
                if intent is None or intent[0]:
                    for row in s.db.execute('SELECT source_id FROM subscription_sources WHERE subscription_id=?',(run['subscription_id'],)):
                        adapter=e.adapters.get(row['source_id'])
                        if adapter is None:raise ValueError('source adapter unavailable')
                        try:found=adapter.discover(fetch=lambda url:e.transport(adapter,row['source_id'],run_id,url))
                        finally:
                            response=getattr(adapter,'last_discovery_response',None)
                            responses=getattr(adapter,'discovery_responses',None) or ([(adapter.listing,response)] if response is not None else [])
                            for index,(requested,observed) in enumerate(responses):e.record_discovery(lease['owner'],f"listing:{run_id}:{row['source_id']}:{lease['epoch']}:{index}",run_id,lease['epoch'],row['source_id'],requested,observed,adapter.synthetic,getattr(adapter,'transport_name','in-process-fixed-sample'))
                        grouped.setdefault(row['source_id'],[]).extend(found)
                grouped={source:list(dict.fromkeys(urls)) for source,urls in grouped.items()}
                for source,urls in grouped.items():self.call('discover',operation_key=f'discover:{run_id}:{source}',**base,source_id=source,targets=urls,coverage=getattr(e.adapters.get(source),'coverage_summary','adapter-declared bounded discovery; not full-site coverage'))
            for item in self.call('run',run_id=run_id)['items']:
                current_item=item['id'];stage='capture'
                if item['state']=='analyzed' or item['state']=='skipped_existing':continue
                if item['state']=='discovered':
                    adapter=e.adapters.get(item['source_id'])
                    known=None
                    try:
                        external=adapter.identity(item['target']) if adapter else None
                        if external:known=s.db.execute('SELECT * FROM messages WHERE source_id=? AND external_key=? AND current_revision>0',(item['source_id'],external)).fetchone()
                    except ValueError:
                        known=None
                    if known and (item['source_id'],item['target']) not in forced_rechecks:
                        from .product import ensure_candidate
                        def settle_existing():
                            e._lease(run_id,lease['owner'],lease['epoch'])
                            current=s.db.execute('SELECT current_revision FROM messages WHERE id=?',(known['id'],)).fetchone()
                            if not current:raise ValueError('known message disappeared')
                            ensure_candidate(s.db,run['subscription_id'],known['id'],current[0],lease['owner'])
                            task=s.db.execute('SELECT * FROM curation_items WHERE subscription_id=? AND message_id=? AND message_revision=?',(run['subscription_id'],known['id'],current[0])).fetchone()
                            if task and task['state'] in ('pending','processing','retry'):
                                s.db.execute("UPDATE run_items SET state='collected',message_id=?,message_revision=?,analysis_id=NULL WHERE id=?",(known['id'],current[0],item['id']))
                                e._change('existing_item_queued_for_analysis',known['id'],current[0])
                                return {'state':'collected','analysis_required':True}
                            s.db.execute("UPDATE run_items SET state='skipped_existing',message_id=?,message_revision=?,analysis_id=? WHERE id=?",(known['id'],current[0],task['analysis_id'] if task else None,item['id']))
                            e._change('existing_item_skipped',known['id'],current[0])
                            return {'state':'skipped_existing','analysis_required':False}
                        decision=e._write(lease['owner'],f"known:{run_id}:{item['id']}:{lease['epoch']}",{'run_id':run_id,'item_id':item['id'],'message_id':known['id']},settle_existing)
                        if not decision['analysis_required']:continue
                        item=next(i for i in self.call('run',run_id=run_id)['items'] if i['id']==item['id'])
                if item['state'] not in ('collected','analysis_failed'):
                    receipt=self.call('capture',operation_key=f"capture:{run_id}:{item['id']}:{lease['epoch']}",**base,item_id=item['id'])
                    if not receipt['accepted']:raise ValueError('capture unconfirmed')
                    item=next(i for i in self.call('run',run_id=run_id)['items'] if i['id']==item['id'])
                if self.analyze is None:continue
                self.call('heartbeat',operation_key=f"heartbeat:{run_id}:{item['id']}:{lease['epoch']}",**base,seconds=600)
                stage='analysis'
                prepared=self.call('queue_prepare',operation_key=f"queue-prepare:{run_id}:{item['id']}:{lease['epoch']}",subscription_id=run['subscription_id'],run_id=run_id,item_id=item['id'],epoch=lease['epoch'],message_id=item['message_id'],message_revision=item['message_revision'])
                if not prepared['should_analyze']:continue
                try:
                    snapshot=self.call('message',message_id=item['message_id'])
                    result=self.analyze(snapshot,item,run['subscription_id'])
                    old=s.db.execute('SELECT COALESCE(MAX(revision),0) FROM analyses WHERE message_id=? AND message_revision=? AND subscription_id=?',(item['message_id'],item['message_revision'],run['subscription_id'])).fetchone()[0]
                    self.call('analysis',operation_key=f"analysis:{run_id}:{item['id']}:{old+1}",**base,item_id=item['id'],owner=lease['owner'],message_id=item['message_id'],message_revision=item['message_revision'],expected_revision=old,result=result)
                except Exception:raise
            stage='finish'
            if self.analyze is None:
                unfinished=s.db.execute("SELECT count(*) FROM run_items WHERE run_id=? AND state NOT IN ('analyzed','skipped_existing')",(run_id,)).fetchone()[0]
                if not unfinished:
                    return self.call('finish',operation_key=f'finish:{run_id}',**base,status='succeeded')
                def handoff():
                    e._lease(run_id,lease['owner'],lease['epoch'])
                    s.db.execute("UPDATE runs SET status='awaiting_analysis',owner=NULL,lease_until=NULL WHERE id=?",(run_id,));e._change('analysis_handoff',run_id,lease['epoch'])
                    return {'run_id':run_id,'status':'awaiting_analysis'}
                return e._write(lease['owner'],f'handoff:{run_id}:{lease["epoch"]}',base,handoff)
            return self.call('finish',operation_key=f'finish:{run_id}',**base,status='succeeded')
        except Exception as error:
            def release():
                e._fence(run_id,lease['owner'],lease['epoch'])
                s.db.execute('INSERT INTO execution_errors(run_id,item_id,stage,error,created_at) VALUES(?,?,?,?,?)',(run_id,current_item,stage,type(error).__name__+': '+str(error)[:500],now()))
                if stage=='analysis' and current_item:
                    s.db.execute("UPDATE run_items SET state='analysis_failed',error=? WHERE id=? AND run_id=?",(type(error).__name__,current_item,run_id))
                    item=s.db.execute('SELECT message_id,message_revision FROM run_items WHERE id=? AND run_id=?',(current_item,run_id)).fetchone()
                    if item and item['message_id'] and item['message_revision']:
                        from .product import fail_analysis
                        fail_analysis(s.db,lease['owner'],run['subscription_id'],item['message_id'],item['message_revision'],type(error).__name__+': '+str(error))
                s.db.execute("UPDATE runs SET status=?,owner=NULL,lease_until=NULL WHERE id=?",('queued' if self.analyze is not None else 'awaiting_executor',run_id))
                e._change('run_retry',run_id,lease['epoch']);return {'run_id':run_id,'status':'queued' if self.analyze is not None else 'awaiting_executor','error':str(error)}
            return e._write(lease['owner'],f'yield:{run_id}:{lease["epoch"]}',{'run_id':run_id,'epoch':lease['epoch']},release)
    def drain(self,limit=10):
        results=[]
        for row in self.service.db.execute("SELECT r.id FROM runs r JOIN subscriptions s ON s.id=r.subscription_id WHERE (r.status='queued' OR (r.status='running' AND r.lease_until<=?)) AND (s.status='active' OR (s.status='paused' AND r.trigger_kind='manual')) ORDER BY r.started_at LIMIT ?",(now(),limit)).fetchall():results.append(self.step(row['id']))
        return results
