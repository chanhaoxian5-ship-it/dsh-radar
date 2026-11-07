import copy,json,tempfile,unittest
from pathlib import Path
from radar.service import Service,Forbidden
from radar.engine import Conflict
from radar.worker import Worker
from support_phase import FixtureAdapter,RULES,analyze

class PhaseATest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.s=Service(self.temp.name);self.adapter=FixtureAdapter();self.s.engine.register_adapter('c',self.adapter)
  self.owner=self.s.provision(['read','manage','execute','analyze','collect','schedule'],['*'],'user');self.runner=self.s.provision(['read','execute','collect','analyze'],['s']);self.worker=Worker(self.s,self.runner,analyze)
  self.call(self.owner,'source_save',operation_key='source',id='c',name='synthetic',root_url='https://fixture.invalid',expected_revision=0)
  self.call(self.owner,'subscription_save',operation_key='sub',id='s',name='合成关注',rules=RULES,status='active',expected_revision=0)
 def tearDown(self):self.s.close();self.temp.cleanup()
 def call(self,token,method,**args):return self.s.call(token,method,args)
 def run(self,result=None): # unittest's run protocol is retained below via renamed helper
  return super().run(result)
 def execute(self,key='run'):
  # This fixture explicitly rechecks its existing notice for update/failure scenarios.
  old=self.s.db.execute("SELECT i.message_id FROM run_items i JOIN runs r ON r.id=i.run_id WHERE r.subscription_id='s' AND i.message_id IS NOT NULL ORDER BY i.rowid DESC LIMIT 1").fetchone()
  request={'subscription_id':'s','message_id':old[0]} if old else {'subscription_id':'s','trigger':'manual'}
  rid=self.call(self.runner,'recheck' if old else 'run_start',operation_key=key,**request)['run_id'];result=self.worker.step(rid);return rid,result
 def plan(self,authority='user',state='active',expected=0):
  a=self.s.db.execute('SELECT * FROM analyses ORDER BY rowid DESC').fetchone()
  return {'operation_key':'plan-'+str(expected)+'-'+authority,'subscription_id':'s','message_id':a['message_id'],'expected_revision':expected,'authority':authority,'state':state,'interval_seconds':3600 if state=='active' else None,'next_check_at':'2020-01-01T00:00:00Z' if state=='active' else None,'reason':'合成：截止前回查','reevaluate_at':'2035-01-01T00:00:00Z','end_at':'2036-01-01T00:00:00Z','analysis_id':a['id']}
 def test_complete_loop_update_same_publication_and_diff(self):
  rid,result=self.execute();self.assertEqual(result['status'],'succeeded');p=self.plan();self.s.call(self.owner,'review',p)
  self.adapter.version_number=2;self.adapter.asset=b'form-v2'
  tick=self.call(self.owner,'tick',operation_key='tick1',at='2026-11-07T00:00:00Z');self.assertEqual(len(tick['queued']),1)
  self.assertEqual(self.worker.drain()[0]['status'],'succeeded')
  mid=p['message_id'];detail=self.call(self.runner,'message',message_id=mid)
  self.assertEqual(detail['current_revision'],2);self.assertEqual([json.loads(r['content'])['published_date'] for r in detail['revisions']],['2026-10-13','2026-10-13'])
  self.assertNotEqual(detail['resources'][0]['sha'],detail['resources'][1]['sha']);self.assertTrue(self.call(self.runner,'diff',message_id=mid,before=1,after=2)['diff'])
  self.assertTrue(detail['analyses'][0]['stale']);self.assertFalse(detail['analyses'][1]['stale'])
 def test_analysis_failure_restart_only_retries_analysis(self):
  def fail(*args):raise RuntimeError('synthetic model unavailable')
  self.worker.analyze=fail;rid,result=self.execute();self.assertEqual(result['status'],'queued');count=len(self.adapter.calls)
  self.s.close();self.s=Service(self.temp.name);self.s.engine.register_adapter('c',self.adapter);self.worker=Worker(self.s,self.runner,analyze)
  self.assertEqual(self.worker.drain()[0]['status'],'succeeded');self.assertEqual(len(self.adapter.calls),count)
 def test_resource_failure_keeps_old_bytes_and_new_immutable_failure(self):
  self.execute();self.adapter.fail_asset=True;self.execute('r2')
  rows=self.s.db.execute('SELECT * FROM resource_versions ORDER BY rowid').fetchall();self.assertEqual([r['status'] for r in rows],['read','failed']);self.assertIsNone(rows[1]['sha']);self.assertTrue((Path(self.temp.name)/'objects'/rows[0]['sha']).exists());self.assertEqual(self.s.db.execute('SELECT count(*) FROM message_revisions').fetchone()[0],1)
 def test_forged_observation_and_source_outside_subscription_rejected(self):
  with self.assertRaises(ValueError):self.call(self.runner,'observation',operation_key='fake',freshness='fresh',body_status='complete')
  rid=self.call(self.runner,'run_start',operation_key='r',subscription_id='s',trigger='manual')['run_id'];lease=self.call(self.runner,'claim',operation_key='claim',run_id=rid)
  self.call(self.owner,'source_save',operation_key='source2',id='evil',name='other',root_url='https://other.invalid',expected_revision=0)
  with self.assertRaises(PermissionError):self.call(self.runner,'discover',operation_key='d',run_id=rid,epoch=lease['epoch'],source_id='evil',targets=['https://other.invalid/x'],coverage='synthetic')
 def test_claim_response_loss_same_epoch_and_changed_request_rejected(self):
  rid=self.call(self.runner,'run_start',operation_key='r',subscription_id='s',trigger='manual')['run_id'];a={'operation_key':'claim','run_id':rid}
  first=self.s.call(self.runner,'claim',a);self.assertEqual(first,self.s.call(self.runner,'claim',a))
  with self.assertRaises(Conflict):self.call(self.runner,'claim',operation_key='claim',run_id=rid,seconds=300)
 def test_user_override_and_plan_conflict_preserve_source(self):
  self.execute();self.s.call(self.owner,'review',self.plan());p=self.plan('agent',expected=1)
  with self.assertRaises(Conflict):self.s.call(self.runner,'review',p)
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM message_revisions').fetchone()[0],1)
 def test_cached_attempt_does_not_replace_revision(self):
  self.execute();self.adapter.cached=True;self.adapter.version_number=2;rid,result=self.execute('r2');self.assertEqual(result['status'],'queued');self.assertEqual(self.s.db.execute('SELECT count(*) FROM message_revisions').fetchone()[0],1);self.assertEqual(self.s.db.execute('SELECT count(*) FROM captures').fetchone()[0],2)
 def test_schema_evidence_must_match_exact_revision(self):
  self.execute();p=self.plan();snapshot=self.call(self.runner,'message',message_id=p['message_id']);item=self.s.db.execute('SELECT * FROM run_items').fetchone();r=analyze(snapshot,item,'s');r['items'][0]['evidence'][0]['message_revision']=2
  from radar.business import RESULT,validate_business
  validate_business(RESULT,r)
  with self.assertRaises(ValueError):self.s.engine.verify_evidence(p['message_id'],1,r['items'][0]['evidence'][0])

class IsolationAndRecoveryTest(unittest.TestCase):
 setUp=PhaseATest.setUp
 tearDown=PhaseATest.tearDown
 call=PhaseATest.call
 execute=PhaseATest.execute
 plan=PhaseATest.plan
 def test_two_process_claim_competition_and_timeout_old_worker(self):
  from support_compete import compete
  rid=self.call(self.runner,'run_start',operation_key='run',subscription_id='s',trigger='manual')['run_id']
  first,p1=compete(self.temp.name,self.runner,rid,'one');second,p2=compete(self.temp.name,self.runner,rid,'two')
  # Both OS processes are launched before either response is consumed.
  first.stdin.write(p1);first.stdin.close();second.stdin.write(p2);second.stdin.close()
  outputs=[]
  for proc in (first,second):
   text=proc.stdout.read();stderr=proc.stderr.read();proc.wait(timeout=15);self.assertEqual(proc.returncode,0,stderr);outputs.append(json.loads(text));proc.stdout.close();proc.stderr.close()
  self.assertEqual(sum(o['ok'] for o in outputs),1)
  epoch=next(o['value']['epoch'] for o in outputs if o['ok']);self.s.db.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00Z' WHERE id=?",(rid,))
  new=self.call(self.runner,'claim',operation_key='new-claim',run_id=rid)
  self.assertEqual(new['epoch'],epoch+1)
  with self.assertRaises(Conflict):self.call(self.runner,'heartbeat',operation_key='old-heartbeat',run_id=rid,epoch=epoch)
 def test_cross_subscription_detail_and_old_analysis_late(self):
  # Separate subscriptions observe the same public source but own independent relevance.
  self.execute();mid=self.s.db.execute('SELECT id FROM messages').fetchone()[0]
  self.call(self.owner,'subscription_save',operation_key='other',id='other',name='other',rules=RULES,status='active',expected_revision=0)
  other=self.s.provision(['read','collect','execute','analyze'],['other'])
  with self.assertRaises(Forbidden):self.call(other,'message',message_id=mid)
  Worker(self.s,other,analyze).step(self.call(other,'run_start',operation_key='o-run',subscription_id='other',trigger='manual')['run_id'])
  self.assertEqual(len(self.call(other,'message',message_id=mid)['analyses']),1)
  # Hold an old-version work item; a later run in another subscription sees a source change.
  rid=self.call(self.runner,'run_start',operation_key='late',subscription_id='s',trigger='manual')['run_id'];lease=self.call(self.runner,'claim',operation_key='late-claim',run_id=rid)
  base={'run_id':rid,'epoch':lease['epoch']};item=self.call(self.runner,'discover',operation_key='late-discover',**base,source_id='c',targets=self.adapter.discover(),coverage='synthetic')['items'][0]
  self.call(self.runner,'capture',operation_key='late-capture',**base,item_id=item['id']);olditem=next(i for i in self.call(self.runner,'run',run_id=rid)['items'] if i['id']==item['id']);oldresult=analyze(self.call(self.runner,'message',message_id=mid),olditem,'s')
  self.adapter.version_number=2
  Worker(self.s,other,analyze).step(self.call(other,'recheck',operation_key='o-new',subscription_id='other',message_id=mid)['run_id'])
  self.call(self.runner,'analysis',operation_key='late-analysis',**base,item_id=item['id'],owner=lease['owner'],message_id=mid,message_revision=1,expected_revision=1,result=oldresult)
  detail=self.call(self.runner,'message',message_id=mid);self.assertEqual(detail['current_revision'],2);self.assertTrue(all(a['stale'] for a in detail['analyses']))
 def test_object_backup_restore_and_corruption_rejection(self):
  from radar.maintenance import backup,restore,check
  self.execute();root=Path(self.temp.name);backup(root/'radar.sqlite3',root/'backup.sqlite3');restore(root/'backup.sqlite3',root/'restored')
  restored=Service(root/'restored');self.assertEqual(restored.db.execute('SELECT count(*) FROM captures').fetchone()[0],1);restored.close();check(root/'restored'/'radar.sqlite3')
  sha=self.s.db.execute('SELECT sha FROM evidence_objects LIMIT 1').fetchone()[0];(Path(str(root/'backup.sqlite3')+'.objects')/sha).write_bytes(b'corrupt')
  with self.assertRaises(ValueError):restore(root/'backup.sqlite3',root/'corrupt-restore')
 def test_paused_subscription_manual_and_review_expiry_evaluation(self):
  self.execute();p=self.plan();p['end_at']='2020-01-01T00:00:00Z';p['reevaluate_at']='2019-01-01T00:00:00Z';self.s.call(self.owner,'review',p)
  self.call(self.owner,'tick',operation_key='end-tick',at='2026-11-07T00:00:00Z');plans=self.call(self.runner,'plans',subscription_id='s');self.assertEqual(plans['reviews'][0]['state'],'ended');self.assertEqual(len(plans['evaluations']),1)
  self.call(self.owner,'subscription_save',operation_key='pause',id='s',name='paused',rules=RULES,status='paused',expected_revision=1)
  with self.assertRaises(Conflict):self.call(self.runner,'run_start',operation_key='automatic',subscription_id='s',trigger='scheduled')
  self.assertEqual(self.execute('manual-paused')[1]['status'],'succeeded')

class AdditionalPhaseATest(unittest.TestCase):
 setUp=PhaseATest.setUp
 tearDown=PhaseATest.tearDown
 call=PhaseATest.call
 execute=PhaseATest.execute
 plan=PhaseATest.plan
 def test_attachment_only_change_produces_resource_event_without_body_revision(self):
  self.execute();self.adapter.asset=b'changed-at-same-url';self.execute('next')
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM message_revisions').fetchone()[0],1)
  hashes=[r[0] for r in self.s.db.execute('SELECT sha FROM resource_versions ORDER BY rowid')];self.assertNotEqual(*hashes)
  mid=self.s.db.execute('SELECT id FROM messages').fetchone()[0];analyses=self.call(self.runner,'message',message_id=mid)['analyses'];self.assertTrue(analyses[0]['stale']);self.assertFalse(analyses[1]['stale'])
  self.assertEqual(self.s.db.execute("SELECT count(*) FROM changes WHERE kind='resource_version'").fetchone()[0],2)
 def test_capture_and_heartbeat_response_loss(self):
  rid=self.call(self.runner,'run_start',operation_key='r',subscription_id='s',trigger='manual')['run_id'];lease=self.call(self.runner,'claim',operation_key='c',run_id=rid);base={'run_id':rid,'epoch':lease['epoch']}
  item=self.call(self.runner,'discover',operation_key='d',**base,source_id='c',targets=self.adapter.discover(),coverage='synthetic')['items'][0]
  args={'operation_key':'capture',**base,'item_id':item['id']};receipt=self.s.call(self.runner,'capture',args);count=len(self.adapter.calls)
  self.assertEqual(receipt,self.s.call(self.runner,'capture',args));self.assertEqual(len(self.adapter.calls),count)
  heartbeat={'operation_key':'h',**base,'seconds':200};first=self.s.call(self.runner,'heartbeat',heartbeat);self.assertEqual(first,self.s.call(self.runner,'heartbeat',heartbeat))
 def test_unavailable_executor_is_explicit_not_silent_success(self):
  self.s.engine.adapters.clear();worker=Worker(self.s,self.runner,None)
  rid=self.call(self.runner,'run_start',operation_key='r',subscription_id='s',trigger='manual')['run_id'];result=worker.step(rid)
  self.assertEqual(result['status'],'awaiting_executor');self.assertIn('adapter unavailable',self.call(self.runner,'run',run_id=rid)['errors'][0]['error'])
 def test_split_collector_analysis_handoff_permissions(self):
  collector=self.s.provision(['read','execute','collect'],['s']);worker=Worker(self.s,collector,None)
  rid=self.call(collector,'run_start',operation_key='r',subscription_id='s',trigger='manual')['run_id'];self.assertEqual(worker.step(rid)['status'],'awaiting_analysis')
  analyst=self.s.provision(['read','analyze','execute'],['s']);a=self.call(analyst,'claim',operation_key='analysis-claim',run_id=rid);item=self.call(analyst,'run',run_id=rid)['items'][0]
  result=analyze(self.call(analyst,'message',message_id=item['message_id']),item,'s')
  self.call(analyst,'analysis',operation_key='analyze',run_id=rid,item_id=item['id'],owner=a['owner'],epoch=a['epoch'],message_id=item['message_id'],message_revision=item['message_revision'],expected_revision=0,result=result)
  self.assertEqual(self.call(analyst,'finish',operation_key='f',run_id=rid,epoch=a['epoch'],status='succeeded')['status'],'succeeded')
  self.assertNotIn('capture',self.call(analyst,'contract')['methods']);self.assertIn('capture',self.call(collector,'contract')['methods'])
 def test_discovery_and_review_due_together_do_not_drop_discovery(self):
  self.execute();self.s.call(self.owner,'review',self.plan())
  self.call(self.owner,'discovery_plan',operation_key='dp',subscription_id='s',expected_revision=0,interval_seconds=3600,next_at='2020-01-01T00:00:00Z',state='active',authority='user',reason='synthetic discovery')
  self.call(self.owner,'tick',operation_key='tick',at='2026-11-07T00:00:00Z');self.assertEqual(self.worker.drain()[0]['status'],'succeeded')
  self.assertEqual(self.s.db.execute('SELECT revision FROM checkpoints WHERE subscription_id=?',('s',)).fetchone()[0],2)
 def test_object_quota_does_not_leave_success_reference(self):
  self.s.engine.quota=1;rid,result=self.execute();self.assertEqual(result['status'],'queued');self.assertEqual(self.s.db.execute('SELECT count(*) FROM observations').fetchone()[0],0)
  self.assertIn('quota exceeded',result['error'])

class SharedReviewTest(unittest.TestCase):
 setUp=PhaseATest.setUp
 tearDown=PhaseATest.tearDown
 call=PhaseATest.call
 execute=PhaseATest.execute
 plan=PhaseATest.plan
 def test_two_subscriptions_share_recheck_bytes_but_keep_analyses_separate(self):
  self.execute();p=self.plan();self.s.call(self.owner,'review',p)
  self.call(self.owner,'subscription_save',operation_key='other',id='other',name='other',rules=RULES,status='active',expected_revision=0)
  other=self.s.provision(['read','collect','execute','analyze'],['other']);other_worker=Worker(self.s,other,analyze)
  rid=self.call(other,'run_start',operation_key='r',subscription_id='other',trigger='manual')['run_id'];other_worker.step(rid)
  a=self.s.db.execute("SELECT * FROM analyses WHERE subscription_id='other'").fetchone();p.update(operation_key='other-plan',subscription_id='other',analysis_id=a['id']);self.s.call(self.owner,'review',p)
  self.adapter.calls.clear();tick=self.call(self.owner,'tick',operation_key='batch',at='2026-11-07T00:00:00Z');self.assertEqual(len(tick['queued']),2)
  for rid in tick['queued']:
   sub=self.s.db.execute('SELECT subscription_id FROM runs WHERE id=?',(rid,)).fetchone()[0]
   self.assertEqual((self.worker if sub=='s' else other_worker).step(rid)['status'],'succeeded')
  self.assertEqual(len(self.adapter.calls),2) # one article + one attachment for the whole batch
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM analyses').fetchone()[0],2)

class StrictPersistenceTest(unittest.TestCase):
 setUp=PhaseATest.setUp
 tearDown=PhaseATest.tearDown
 call=PhaseATest.call
 execute=PhaseATest.execute
 plan=PhaseATest.plan
 def test_evidence_reads_scope_and_snapshot(self):
  self.execute();item=self.s.db.execute('SELECT * FROM run_items').fetchone();resource=self.s.db.execute('SELECT * FROM resource_versions').fetchone()
  args={'run_id':item['run_id'],'observation_id':item['observation_id'],'resource_version_id':resource['id'],'offset':0,'length':100}
  import base64
  self.assertEqual(base64.b64decode(self.s.call(self.runner,'evidence_read',args)['data_base64']),b'form-v1')
  outsider=self.s.provision(['read'],['different'])
  with self.assertRaises(Forbidden):self.s.call(outsider,'evidence_read',args)
 def test_restored_principal_reissue_preserves_receipt_recovery(self):
  from radar.maintenance import backup,restore
  root=Path(self.temp.name);principal=self.s.authenticate(self.runner)['id'];receipt=self.call(self.runner,'run_start',operation_key='recoverable',subscription_id='s',trigger='manual')
  backup(root/'radar.sqlite3',root/'b.sqlite3');restore(root/'b.sqlite3',root/'restored');restored=Service(root/'restored')
  try:
   with self.assertRaises(Forbidden):restored.call(self.runner,'receipt',{'operation_key':'recoverable'})
   newtoken=restored.reissue(principal);self.assertEqual(restored.call(newtoken,'receipt',{'operation_key':'recoverable'}),receipt)
  finally:restored.close()
 def test_immutable_source_and_resource_history(self):
  import sqlite3
  self.execute()
  with self.assertRaises(sqlite3.IntegrityError):self.s.db.execute("UPDATE message_revisions SET content='{}'")
  with self.assertRaises(sqlite3.IntegrityError):self.s.db.execute("DELETE FROM resource_versions")
 def test_pausing_subscription_blocks_already_queued_automatic_run(self):
  rid=self.call(self.runner,'run_start',operation_key='auto',subscription_id='s',trigger='scheduled')['run_id']
  self.call(self.owner,'subscription_save',operation_key='pause',id='s',name='paused',rules=RULES,status='paused',expected_revision=1)
  self.assertEqual(self.worker.drain(),[])
  with self.assertRaises(Conflict):self.call(self.runner,'claim',operation_key='claim-paused',run_id=rid)
 def test_old_lease_analysis_cannot_overwrite_newer_match(self):
  rid=self.call(self.runner,'run_start',operation_key='old',subscription_id='s',trigger='manual')['run_id'];lease=self.call(self.runner,'claim',operation_key='oc',run_id=rid);base={'run_id':rid,'epoch':lease['epoch']}
  item=self.call(self.runner,'discover',operation_key='od',**base,source_id='c',targets=self.adapter.discover(),coverage='synthetic')['items'][0]
  self.call(self.runner,'capture',operation_key='o-cap',**base,item_id=item['id']);item=self.call(self.runner,'run',run_id=rid)['items'][0];oldresult=analyze(self.call(self.runner,'message',message_id=item['message_id']),item,'s')
  self.call(self.runner,'finish',operation_key='partial',**base,status='partial');self.adapter.version_number=2;self.execute('new')
  with self.assertRaises(Conflict):self.call(self.runner,'analysis',operation_key='late',**base,item_id=item['id'],owner=lease['owner'],message_id=item['message_id'],message_revision=1,expected_revision=0,result=oldresult)
  self.assertEqual(self.s.db.execute("SELECT message_revision FROM matches WHERE subscription_id='s'").fetchone()[0],2)

class DshSessionIdentityTest(unittest.TestCase):
 setUp=PhaseATest.setUp
 tearDown=PhaseATest.tearDown
 call=PhaseATest.call
 def test_gateway_bound_principals_reject_old_session_submit(self):
  gateway=self.s.provision(['delegate'],['*'])
  def dispatch(session,method,args):return self.s.call(gateway,'session_dispatch',{'session_id':session,'method':method,'args':args})
  rid=self.call(self.runner,'run_start',operation_key='r',subscription_id='s',trigger='manual')['run_id'];Worker(self.s,self.runner,None).step(rid)
  lease=dispatch('session-fixture-new','claim',{'operation_key':'claim','run_id':rid})
  self.assertEqual(lease,dispatch('session-fixture-new','claim',{'operation_key':'claim','run_id':rid}))
  item=self.call(self.runner,'run',run_id=rid)['items'][0];result=analyze(self.call(self.runner,'message',message_id=item['message_id']),item,'s')
  payload={'operation_key':'analysis','run_id':rid,'epoch':lease['epoch'],'owner':lease['owner'],'item_id':item['id'],'message_id':item['message_id'],'message_revision':item['message_revision'],'expected_revision':0,'result':result}
  with self.assertRaises(Forbidden):dispatch('session-fixture-old','analysis',payload)
  with self.assertRaises(Conflict):dispatch('session-fixture-old','claim',{'operation_key':'steal','run_id':rid})
  dispatch('session-fixture-new','analysis',payload)
  self.assertEqual(dispatch('session-fixture-new','finish',{'operation_key':'finish','run_id':rid,'epoch':lease['epoch'],'status':'succeeded'})['status'],'succeeded')
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM session_bindings').fetchone()[0],2)

class ResourceBasisTest(unittest.TestCase):
 setUp=PhaseATest.setUp
 tearDown=PhaseATest.tearDown
 call=PhaseATest.call
 execute=PhaseATest.execute
 def test_analysis_pinning_old_attachment_cannot_replace_current_projection(self):
  self.execute();item=self.s.db.execute('SELECT * FROM run_items').fetchone();snapshot=self.call(self.runner,'message',message_id=item['message_id']);old=analyze(snapshot,item,'s')
  self.adapter.asset=b'new-version';self.execute('new-resource');projected=self.s.db.execute("SELECT analysis_id FROM matches WHERE subscription_id='s'").fetchone()[0]
  rid=self.call(self.runner,'run_start',operation_key='old-basis',subscription_id='s',trigger='manual')['run_id'];lease=self.call(self.runner,'claim',operation_key='oc',run_id=rid);base={'run_id':rid,'epoch':lease['epoch']};work=self.call(self.runner,'discover',operation_key='od',**base,source_id='c',targets=self.adapter.discover(),coverage='synthetic')['items'][0]
  self.call(self.runner,'capture',operation_key='cap',**base,item_id=work['id'])
  self.call(self.runner,'analysis',operation_key='old-resource-analysis',**base,owner=lease['owner'],item_id=work['id'],message_id=item['message_id'],message_revision=1,expected_revision=2,result=old)
  self.assertEqual(self.s.db.execute("SELECT analysis_id FROM matches WHERE subscription_id='s'").fetchone()[0],projected)
 def test_incomplete_transport_preserves_raw_without_new_revision(self):
  from radar.backend import Response
  self.execute();original=self.adapter.fetch
  def incomplete(url):
   r=original(url)
   return Response(r.status,r.final_url,r.body,r.headers,False)
  self.adapter.fetch=incomplete;self.adapter.version_number=2;self.execute('partial')
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM message_revisions').fetchone()[0],1)
  self.assertEqual(self.s.db.execute('SELECT body_status FROM observations ORDER BY rowid DESC LIMIT 1').fetchone()[0],'partial')
 def test_paused_review_can_request_evaluation_without_restarting_capture(self):
  self.execute();a=self.s.db.execute('SELECT * FROM analyses').fetchone()
  self.call(self.owner,'review',operation_key='p',subscription_id='s',message_id=a['message_id'],expected_revision=0,authority='user',state='paused',interval_seconds=None,next_check_at=None,reason='manual pause',reevaluate_at='2020-01-01T00:00:00Z',end_at=None,analysis_id=a['id'])
  result=self.call(self.owner,'tick',operation_key='reeval',at='2026-11-07T00:00:00Z');self.assertEqual(result['queued'],[]);self.assertEqual(len(result['evaluation_requests']),1)

class StartupFenceTest(unittest.TestCase):
 setUp=PhaseATest.setUp
 tearDown=PhaseATest.tearDown
 call=PhaseATest.call
 def test_startup_fences_unexpired_worker_and_retains_collected_work(self):
  rid=self.call(self.runner,'run_start',operation_key='r',subscription_id='s',trigger='manual')['run_id'];lease=self.call(self.runner,'claim',operation_key='c',run_id=rid);base={'run_id':rid,'epoch':lease['epoch']}
  item=self.call(self.runner,'discover',operation_key='d',**base,source_id='c',targets=self.adapter.discover(),coverage='synthetic')['items'][0];self.call(self.runner,'capture',operation_key='cap',**base,item_id=item['id']);count=len(self.adapter.calls)
  self.s.engine.startup_recover('local-server-fixture','instance-fixture')
  with self.assertRaises(Conflict):self.call(self.runner,'heartbeat',operation_key='old',**base)
  self.assertEqual(Worker(self.s,self.runner,analyze).drain()[0]['status'],'succeeded');self.assertEqual(len(self.adapter.calls),count)

class StorageBoundTest(unittest.TestCase):
 setUp=PhaseATest.setUp
 tearDown=PhaseATest.tearDown
 call=PhaseATest.call
 def test_sqlite_full_rolls_back_configuration_and_receipt(self):
  import sqlite3
  pages=self.s.db.execute('PRAGMA page_count').fetchone()[0];self.s.db.execute('PRAGMA max_page_count='+str(pages+1))
  rules=copy.deepcopy(RULES);rules['include']=['x'*1000 for _ in range(100)]
  with self.assertRaises(sqlite3.OperationalError):self.call(self.owner,'subscription_save',operation_key='quota-config',id='s',name='oversized',rules=rules,status='active',expected_revision=1)
  self.assertEqual(self.s.db.execute("SELECT revision FROM subscriptions WHERE id='s'").fetchone()[0],1)
  self.assertIsNone(self.call(self.owner,'receipt',operation_key='quota-config'))

class MultiprocessTransportTest(unittest.TestCase):
 setUp=PhaseATest.setUp
 tearDown=PhaseATest.tearDown
 call=PhaseATest.call
 def test_two_processes_share_scheduled_article_and_attachment_fetch(self):
  import subprocess,sys
  self.call(self.owner,'subscription_save',operation_key='second-sub',id='second',name='second',rules=RULES,status='active',expected_revision=0)
  for sub in ('s','second'):self.call(self.owner,'discovery_plan',operation_key='plan-'+sub,subscription_id=sub,expected_revision=0,interval_seconds=3600,next_at='2020-01-01T00:00:00Z',state='active',authority='user',reason='synthetic concurrency')
  runs=self.call(self.owner,'tick',operation_key='tick',at='2026-11-07T00:00:00Z')['queued'];self.assertEqual(len(runs),2)
  tokens={sub:self.s.provision(['read','collect','analyze','execute'],[sub]) for sub in ('s','second')};log=Path(self.temp.name)/'fetches.txt'
  code="""import sys,json,time
from pathlib import Path
sys.path.insert(0,'tests')
from support_phase import FixtureAdapter,analyze
from radar.service import Service
from radar.worker import Worker
p=json.load(sys.stdin)
class Adapter(FixtureAdapter):
 def fetch(self,url):
  with open(p['log'],'a',encoding='utf-8') as stream:stream.write(url+'\\n')
  time.sleep(.1)
  return super().fetch(url)
s=Service(p['directory']);s.engine.register_adapter('c',Adapter())
try:print(json.dumps(Worker(s,p['token'],analyze).step(p['run_id'])))
finally:s.close()
"""
  processes=[]
  for rid in runs:
   sub=self.s.db.execute('SELECT subscription_id FROM runs WHERE id=?',(rid,)).fetchone()[0]
   proc=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8');processes.append((proc,{'directory':self.temp.name,'log':str(log),'token':tokens[sub],'run_id':rid}))
  for proc,payload in processes:proc.stdin.write(json.dumps(payload));proc.stdin.close()
  for proc,_ in processes:
   stdout=proc.stdout.read();stderr=proc.stderr.read();proc.wait(timeout=15);self.assertEqual(proc.returncode,0,stderr);self.assertEqual(json.loads(stdout)['status'],'succeeded');proc.stdout.close();proc.stderr.close()
  self.assertEqual(log.read_text(encoding='utf-8').splitlines(),['https://fixture.invalid/notice/1','https://fixture.invalid/form.txt'])
