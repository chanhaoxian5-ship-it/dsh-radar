import unittest,json
import test_phase_a as phase
from radar.engine import Conflict
from radar.service import Forbidden
class ManualProposalTest(unittest.TestCase):
 def setUp(self):
  phase.PhaseATest.setUp(self);self.owner=self.s.provision(['read','manage','analyze','execute','user'],['*'],'user')
 tearDown=phase.PhaseATest.tearDown;call=phase.PhaseATest.call
 def proposal(self):
  return self.call(self.runner,'rule_proposal_create',operation_key='proposal',target_method='discovery_plan',payload={'subscription_id':'s','expected_revision':0,'interval_seconds':172800,'next_at':'2030-01-01T00:00:00Z','state':'active','reason':'Explicit fixed test user requirement'},user_intent='固定样本：每两天发现一次，作为人工规则保护')
 def test_agent_cannot_claim_owner_and_owner_confirmation_protects_rule(self):
  proposal=self.proposal()
  with self.assertRaises(Forbidden):self.call(self.runner,'rule_proposal_decide',operation_key='fake-owner',proposal_id=proposal['proposal_id'],expected_proposal_revision=1,decision='approve')
  args={'operation_key':'confirm','proposal_id':proposal['proposal_id'],'expected_proposal_revision':1,'decision':'approve'}
  result=self.s.call(self.owner,'rule_proposal_decide',args);self.assertEqual(result['status'],'approved');self.assertEqual(result,self.s.call(self.owner,'rule_proposal_decide',args))
  plan=self.call(self.runner,'plans',subscription_id='s')['discovery'][0];self.assertEqual(plan['authority'],'user');self.assertEqual(plan['interval_seconds'],172800)
  with self.assertRaises(Conflict):self.call(self.runner,'discovery_plan',operation_key='override',subscription_id='s',expected_revision=plan['revision'],interval_seconds=3600,next_at='2030-01-01T00:00:00Z',state='active',authority='agent',reason='model suggestion')
 def test_rejection_does_not_apply(self):
  proposal=self.proposal();r=self.call(self.owner,'rule_proposal_decide',operation_key='reject',proposal_id=proposal['proposal_id'],expected_proposal_revision=1,decision='reject');self.assertEqual(r['status'],'rejected');self.assertEqual(self.call(self.runner,'plans',subscription_id='s')['discovery'],[])
 def test_conflict_preserves_existing_rule(self):
  proposal=self.proposal();self.call(self.owner,'discovery_plan',operation_key='manual',subscription_id='s',expected_revision=0,interval_seconds=7200,next_at='2030-01-01T00:00:00Z',state='active',authority='user',reason='newer human rule')
  r=self.call(self.owner,'rule_proposal_decide',operation_key='conflict',proposal_id=proposal['proposal_id'],expected_proposal_revision=1,decision='approve');self.assertEqual(r['status'],'conflict');self.assertEqual(self.call(self.runner,'plans',subscription_id='s')['discovery'][0]['interval_seconds'],7200)
 def test_resume_after_process_loss_between_rule_commit_and_proposal_settlement(self):
  from radar.service import Service
  proposal=self.proposal();original=self.s.engine._write
  def fail_settle(principal,key,payload,action):
   if key=='recover:settle':raise RuntimeError('fixed test process loss before settlement')
   return original(principal,key,payload,action)
  self.s.engine._write=fail_settle
  args={'operation_key':'recover','proposal_id':proposal['proposal_id'],'expected_proposal_revision':1,'decision':'approve'}
  with self.assertRaises(RuntimeError):self.s.call(self.owner,'rule_proposal_decide',args)
  self.s.close();self.s=Service(self.temp.name)
  rows=self.call(self.runner,'rule_proposals',subscription_id='s');self.assertEqual(rows[0]['status'],'applying');self.assertEqual(rows[0]['decision_key'],'recover')
  result=self.s.call(self.owner,'rule_proposal_decide',args);self.assertEqual(result['status'],'approved')
  self.assertEqual(self.call(self.runner,'plans',subscription_id='s')['discovery'][0]['revision'],1)
