import json,subprocess,sys,tempfile,time,unittest,urllib.request
from pathlib import Path
class DispatchHttpTest(unittest.TestCase):
 def test_manual_request_dispatches_and_reports_missing_adapter(self):
  with tempfile.TemporaryDirectory() as d:
   process=subprocess.Popen([sys.executable,'-m','radar.server','--data-dir',d],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   try:
    deadline=time.monotonic()+8
    while not (Path(d)/'endpoint.json').exists():
     if process.poll() is not None:raise AssertionError('server exited')
     if time.monotonic()>deadline:raise AssertionError('startup timeout')
     time.sleep(.05)
    endpoint=json.loads((Path(d)/'endpoint.json').read_text());token=(Path(d)/'agent.token').read_text()
    def call(method,args):
     req=urllib.request.Request(endpoint['api_base']+'/v1/call',data=json.dumps({'method':method,'args':args}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
     with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)['value']
    call('source_save',{'operation_key':'source','id':'unavailable','name':'unregistered fixture','root_url':'https://fixture.invalid','expected_revision':0})
    call('subscription_save',{'operation_key':'sub','id':'s','name':'s','rules':{'schema_version':2,'purpose':'synthetic dispatch check','audiences':[],'include':[],'exclude':[],'source_ids':['unavailable']},'status':'active','expected_revision':0})
    run=call('run_start',{'operation_key':'run','subscription_id':'s','trigger':'manual'})
    deadline=time.monotonic()+5
    while True:
     detail=call('run',{'run_id':run['run_id']})
     if detail['status']=='awaiting_executor':break
     if time.monotonic()>deadline:raise AssertionError('manual run never dispatched')
     time.sleep(.05)
    self.assertEqual(detail['epoch'],1);self.assertIn('adapter unavailable',detail['errors'][0]['error'])
    self.assertEqual(call('runs',{'subscription_id':'s'})[0]['id'],run['run_id'])
    retry=call('retry_run',{'operation_key':'retry','run_id':run['run_id'],'expected_epoch':1});self.assertEqual(retry['status'],'queued')
   finally:process.terminate();process.wait(timeout=5)
