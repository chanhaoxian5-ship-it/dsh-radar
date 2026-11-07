import tempfile,unittest
from radar.service import Service, Forbidden

class AuthorizationTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(); self.s=Service(self.temp.name)
  self.admin=self.s.provision(['read','manage','execute','analyze'],['*'],'user')
  self.reader=self.s.provision(['read'],['a'])
  self.agent=self.s.provision(['read','analyze','execute'],['a'])
  for sub in ['a','b']: self.s.call(self.admin,'subscription_save',{'operation_key':sub,'id':sub,'name':sub,'rules':{'schema_version':2,'purpose':'test','audiences':[],'include':[],'exclude':[],'source_ids':[]},'status':'active','expected_revision':0})
 def tearDown(self): self.s.close(); self.temp.cleanup()
 def test_scopes_and_permissions(self):
  self.assertEqual([s['id'] for s in self.s.call(self.reader,'subscriptions',{})],['a'])
  with self.assertRaises(Forbidden): self.s.call(self.reader,'subscription_save',{'id':'a'})
  with self.assertRaises(Forbidden): self.s.call(self.agent,'run_start',{'operation_key':'x','subscription_id':'b','trigger':'manual'})
  with self.assertRaises(Forbidden): self.s.call(self.agent,'observation',{})
 def test_authenticated_worker_and_authority(self):
  run=self.s.call(self.admin,'run_start',{'operation_key':'r','subscription_id':'a','trigger':'manual'})['run_id']
  claimed=self.s.call(self.agent,'claim',{'operation_key':'claim','run_id':run})
  self.assertEqual(claimed['owner'],self.s.authenticate(self.agent)['id'])
  with self.assertRaises(Forbidden): self.s.call(self.agent,'review',{'operation_key':'p','subscription_id':'a','message_id':'m','expected_revision':0,'authority':'user','state':'paused','interval_seconds':None,'next_check_at':None,'reason':'test','reevaluate_at':None,'end_at':None,'analysis_id':'a'})
 def test_revocation_and_receipt(self):
  self.assertEqual(self.s.call(self.admin,'receipt',{'operation_key':'a'})['id'],'a')
  self.assertIsNone(self.s.call(self.reader,'receipt',{'operation_key':'a'}))
  self.s.db.execute('UPDATE credentials SET revoked_at=? WHERE id=?',('now',self.s.authenticate(self.reader)['id']))
  with self.assertRaises(Forbidden): self.s.call(self.reader,'contract',{})
