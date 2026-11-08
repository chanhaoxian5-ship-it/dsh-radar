"""Persist Agent rule proposals; only user-authority principals may approve them."""
import json
from .engine import Conflict,encode,uid,now
from .schemas import validate

def initialize(db):
 db.executescript('''
 CREATE TABLE IF NOT EXISTS rule_proposals(id TEXT PRIMARY KEY,subscription_id TEXT NOT NULL REFERENCES subscriptions(id),method TEXT NOT NULL,payload TEXT NOT NULL,user_intent TEXT NOT NULL,proposer TEXT NOT NULL,session_id TEXT,status TEXT NOT NULL,revision INTEGER NOT NULL,approver TEXT,application_receipt TEXT,error TEXT,created_at TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS rule_proposal_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,proposal_id TEXT NOT NULL REFERENCES rule_proposals(id),status TEXT NOT NULL,principal TEXT NOT NULL,created_at TEXT NOT NULL);
 ''')
 db.execute('BEGIN IMMEDIATE')
 try:
  columns={row[1] for row in db.execute('PRAGMA table_info(rule_proposals)')}
  for name,kind in (('decision_key','TEXT'),('decision_expected_revision','INTEGER')):
   if name not in columns:db.execute('ALTER TABLE rule_proposals ADD COLUMN '+name+' '+kind)
  db.commit()
 except BaseException:db.rollback();raise

def create(service,identity,key,p):
 payload=p['payload'];service.check(identity,'analyze',payload['subscription_id'])
 target={**payload,'authority':'agent','operation_key':'proposal-validation'};validate(p['target_method'],target)
 def write():
  ident=uid();session=service.db.execute('SELECT session_id FROM session_bindings WHERE principal_id=?',(identity['id'],)).fetchone()
  service.db.execute('INSERT INTO rule_proposals(id,subscription_id,method,payload,user_intent,proposer,session_id,status,revision,approver,application_receipt,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(ident,payload['subscription_id'],p['target_method'],encode(payload),p['user_intent'],identity['id'],session[0] if session else None,'pending',1,None,None,None,now()))
  service.db.execute('INSERT INTO rule_proposal_events(proposal_id,status,principal,created_at) VALUES(?,?,?,?)',(ident,'pending',identity['id'],now()));service.engine._change('rule_proposal',ident,1)
  return {'proposal_id':ident,'status':'pending','revision':1,'authority':'requires_owner_confirmation'}
 return service.engine._write(identity['id'],key,p,write)

def decide(service,identity,key,p):
 if identity['authority']!='user':raise PermissionError('human owner authority required')
 proposal=service.db.execute('SELECT * FROM rule_proposals WHERE id=?',(p['proposal_id'],)).fetchone()
 if not proposal:raise ValueError('proposal not found')
 service.check(identity,'user',proposal['subscription_id'])
 def final():
  current=service.db.execute('SELECT * FROM rule_proposals WHERE id=?',(p['proposal_id'],)).fetchone()
  return {'proposal_id':current['id'],'status':current['status'],'revision':current['revision'],'application_receipt':json.loads(current['application_receipt']) if current['application_receipt'] else None,'error':current['error']}
 # A lost final response is recovered with the same complete request and receipt.
 if service.db.execute('SELECT 1 FROM operations WHERE principal=? AND operation_key=?',(identity['id'],key)).fetchone():return service.engine._write(identity['id'],key,p,final)
 def reserve():
  current=service.db.execute('SELECT * FROM rule_proposals WHERE id=?',(p['proposal_id'],)).fetchone()
  if current['revision']!=p['expected_proposal_revision'] or current['status']!='pending':raise Conflict('proposal changed')
  state='applying' if p['decision']=='approve' else 'rejected'
  service.db.execute('UPDATE rule_proposals SET status=?,revision=revision+1,approver=?,decision_key=?,decision_expected_revision=? WHERE id=?',(state,identity['id'],key,p['expected_proposal_revision'],current['id']))
  service.db.execute('INSERT INTO rule_proposal_events(proposal_id,status,principal,created_at) VALUES(?,?,?,?)',(current['id'],state,identity['id'],now()));service.engine._change('rule_proposal',current['id'],current['revision']+1)
  return {'reserved':state}
 # The reservation fences competing approvals/rejections. Its request is immutable.
 service.engine._write(identity['id'],key+':reserve',p,reserve)
 current=service.db.execute('SELECT * FROM rule_proposals WHERE id=?',(p['proposal_id'],)).fetchone()
 if current['status']=='applying':
  if current['approver']!=identity['id']:raise PermissionError('proposal owned by another confirmer')
  payload=json.loads(current['payload']);payload.update(authority='user',operation_key='proposal-apply:'+current['id'])
  try:
   receipt=service._call(identity,current['method'],payload);state='approved';error=None
  except (ValueError,Conflict,PermissionError) as exception:
   receipt=None;state='conflict';error=type(exception).__name__+': '+str(exception)[:500]
  def settle():
   row=service.db.execute('SELECT * FROM rule_proposals WHERE id=?',(p['proposal_id'],)).fetchone()
   if row['status']!='applying' or row['approver']!=identity['id']:raise Conflict('proposal settlement changed')
   service.db.execute('UPDATE rule_proposals SET status=?,revision=revision+1,application_receipt=?,error=? WHERE id=?',(state,encode(receipt) if receipt else None,error,row['id']))
   service.db.execute('INSERT INTO rule_proposal_events(proposal_id,status,principal,created_at) VALUES(?,?,?,?)',(row['id'],state,identity['id'],now()));service.engine._change('rule_proposal',row['id'],row['revision']+1);return {'status':state}
  # If the process dies after rule commit, retrying uses the fixed inner receipt.
  service.engine._write(identity['id'],key+':settle',{'proposal_id':current['id'],'state':state,'receipt':receipt,'error':error},settle)
 return service.engine._write(identity['id'],key,p,final)
