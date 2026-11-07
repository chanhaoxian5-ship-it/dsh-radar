import copy
import tempfile
import unittest
from pathlib import Path
from radar.engine import Radar, Conflict

class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'radar.sqlite'; self.r=Radar(self.path)
        self.r.subscription('manager','sub',{'id':'s','name':'关注','rules':{},'status':'active','expected_revision':0})
        self.r.source('c','学院','https://example.org')
        self.run=self.r.start('manager','run','s','manual')['run_id']; self.lease=self.r.claim(self.run,'worker')
        self.item=self.r.discover(self.run,'worker',self.lease['epoch'],'c',['https://example.org/1'])[0]['id']
        self.p={'run_id':self.run,'item_id':self.item,'owner':'worker','epoch':self.lease['epoch'],'external_key':'1','freshness':'fresh','body_status':'complete','asset_status':'unknown','http_status':200,'content':{'title':'通知','blocks':[{'block_id':'b1','type':'paragraph','text':'11月20日前提交。'}],'attachments':[]},'extractor_version':'1','error':None}
    def tearDown(self): self.r.close(); self.tmp.cleanup()
    def observe(self,key,p=None): return self.r.observation('collector',key,p or self.p)
    def test_revisions_retries_and_response_recovery(self):
        a=self.observe('o1'); self.assertEqual(a,self.observe('o1')); self.assertEqual(a,self.r.receipt('collector','o1'))
        changed=copy.deepcopy(self.p); changed['content']['blocks'][0]['text']='11月25日前提交。'
        with self.assertRaises(Conflict): self.observe('o1',changed)
        self.assertEqual(self.observe('o2')['message_revision'],1)
        self.assertEqual(self.observe('o3',changed)['message_revision'],2)
        self.assertEqual(self.observe('o4')['message_revision'],3)
        failed=copy.deepcopy(self.p); failed.update(freshness='unknown',body_status='failed',http_status=403,content=None,error='http_403')
        self.assertEqual(self.observe('o5',failed)['message_revision'],3)
        self.assertEqual(self.r.db.execute('select count(*) from message_revisions').fetchone()[0],3)
    def test_analysis_failure_preserves_observation_and_evidence(self):
        o=self.observe('o1'); p={'run_id':self.run,'item_id':self.item,'owner':'worker','epoch':self.lease['epoch'],'message_id':o['message_id'],'message_revision':1,'expected_revision':0,'result':{'summary':'提交','items':[{'action':'提交','evidence':[{'block_id':'b1','quote':'不存在'}]}],'decision':'relevant','reason':'相关','uncertainties':[]}}
        with self.assertRaises(ValueError): self.r.analysis('analyst','a1',p)
        self.assertEqual(self.r.db.execute('select count(*) from observations').fetchone()[0],1)
        p['result']['items'][0]['evidence'][0]['quote']='11月20日'; self.r.analysis('analyst','a2',p)
        self.assertEqual(self.r.db.execute('select count(*) from analyses').fetchone()[0],1)
    def test_expired_owner_and_override(self):
        o=self.observe('o1'); p={'subscription_id':'s','message_id':o['message_id'],'expected_revision':0,'authority':'user','state':'paused','interval_seconds':None,'next_check_at':None,'reason':'人工暂停'}
        self.r.review('user','p1',p); p.update(expected_revision=1,authority='agent',state='ended')
        with self.assertRaises(Conflict): self.r.review('agent','p2',p)
        self.r.db.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00Z' WHERE id=?",(self.run,))
        self.r.claim(self.run,'new-worker')
        with self.assertRaises(Conflict): self.observe('o2')
    def test_restart_and_stale_analysis(self):
        o=self.observe('o1'); self.r.close(); self.r=Radar(self.path); self.assertEqual(self.r.receipt('collector','o1'),o)
        changed=copy.deepcopy(self.p); changed['content']['title']='修改标题'; self.observe('o2',changed)
        item=self.r.db.execute('select * from run_items where id=?',(self.item,)).fetchone(); self.assertEqual(item['message_revision'],2)

if __name__=='__main__': unittest.main()

class LifecycleTest(unittest.TestCase):
    def test_lease_and_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            r=Radar(Path(directory)/'test.sqlite')
            r.subscription('p','s',{'id':'s','name':'n','rules':{},'status':'active','expected_revision':0})
            run=r.start('p','r','s','manual')['run_id']; lease=r.claim(run,'first')
            self.assertEqual(r.start('p','r2','s','manual')['run_id'],run)
            self.assertEqual(r.heartbeat(run,'first',lease['epoch'])['epoch'],lease['epoch'])
            self.assertEqual(r.cancel('p','cancel',run)['status'],'cancelled')
            with self.assertRaises(Conflict): r.heartbeat(run,'first',lease['epoch'])
            self.assertEqual(r.cancel('p','cancel',run)['status'],'cancelled')
            self.assertNotEqual(r.start('p','r3','s','manual')['run_id'],run)
            r.close()
