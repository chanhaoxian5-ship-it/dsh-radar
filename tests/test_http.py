import json,subprocess,sys,tempfile,time,unittest,urllib.request,urllib.error
from pathlib import Path

class HttpTest(unittest.TestCase):
 def test_real_server_auth_receipt_and_restart(self):
  with tempfile.TemporaryDirectory() as d:
   def start():
    p=subprocess.Popen([sys.executable,'-m','radar.server','--data-dir',d],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    end=time.monotonic()+8
    while time.monotonic()<end:
     f=Path(d)/'endpoint.json'
     if f.exists():
      try:
       ep=json.loads(f.read_text()); token=(Path(d)/'agent.token').read_text(); return p,ep,token
      except (ValueError,OSError): pass
     if p.poll() is not None: raise AssertionError('server exited')
     time.sleep(.05)
    p.terminate();p.wait();raise AssertionError('server startup timeout')
   def call(ep,token,method,args,origin=None):
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    if origin: headers['Origin']=origin
    req=urllib.request.Request(ep['api_base']+'/v1/call',data=json.dumps({'method':method,'args':args}).encode(),headers=headers)
    with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)['value']
   p,ep,token=start()
   try:
    contract=call(ep,token,'contract',{});self.assertEqual(contract['schema_version'],1)
    with self.assertRaises(urllib.error.HTTPError) as e:call(ep,token,'contract',{},'https://example.org')
    self.assertEqual(e.exception.code,403)
    payload={'operation_key':'create','id':'s','name':'测试','rules':{'schema_version':2,'purpose':'test','audiences':[],'include':[],'exclude':[],'source_ids':[]},'status':'active','expected_revision':0}
    receipt=call(ep,token,'subscription_save',payload)
    self.assertEqual(call(ep,token,'subscription_save',payload),receipt)
   finally:p.terminate();p.wait(timeout=5)
   (Path(d)/'endpoint.json').unlink(missing_ok=True)
   p,ep,token=start()
   try:
    self.assertEqual(call(ep,token,'receipt',{'operation_key':'create'}),receipt)
    self.assertEqual(call(ep,token,'subscriptions',{})[0]['name'],'测试')
   finally:p.terminate();p.wait(timeout=5)
