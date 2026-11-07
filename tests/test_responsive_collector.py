import unittest,tempfile,subprocess,sys,time,json,urllib.request
from pathlib import Path
class ResponsiveCollectorTest(unittest.TestCase):
 def test_cancel_api_responds_while_trusted_collector_is_blocked(self):
  code="""import sys,time
from pathlib import Path
sys.path.insert(0,'tests')
from radar.service import Service
from support_phase import FixtureAdapter
from radar.server import serve
original=Service.load_adapters
class Slow(FixtureAdapter):
 def discover(self,fetch=None):
  Path(sys.argv[2]).touch();time.sleep(3);return super().discover(fetch)
def load(self):original(self);self.engine.register_adapter('c',Slow())
Service.load_adapters=load
serve(sys.argv[1])
"""
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);data=root/'data';signal=root/'started'
   proc=subprocess.Popen([sys.executable,'-c',code,str(data),str(signal)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   try:
    end=time.monotonic()+8
    while not (data/'endpoint.json').exists():
     if proc.poll() is not None or time.monotonic()>end:raise AssertionError('startup failed')
     time.sleep(.05)
    endpoint=json.loads((data/'endpoint.json').read_text());token=(data/'agent.token').read_text()
    def call(method,args):
     req=urllib.request.Request(endpoint['api_base']+'/v1/call',data=json.dumps({'method':method,'args':args}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
     with urllib.request.urlopen(req,timeout=2) as response:return json.load(response)['value']
    call('source_save',{'operation_key':'source','id':'c','name':'SYNTHETIC slow collector','root_url':'https://fixture.invalid','expected_revision':0})
    call('subscription_save',{'operation_key':'sub','id':'s','name':'SYNTHETIC','rules':{'schema_version':2,'purpose':'test','audiences':[],'include':[],'exclude':[],'source_ids':['c']},'status':'active','expected_revision':0})
    run=call('run_start',{'operation_key':'run','subscription_id':'s','trigger':'manual'})
    while not signal.exists():
     if time.monotonic()>end:raise AssertionError('collector did not start')
     time.sleep(.03)
    started=time.monotonic();call('cancel',{'operation_key':'cancel','run_id':run['run_id']});self.assertLess(time.monotonic()-started,1)
    time.sleep(3.1);self.assertEqual(call('run',{'run_id':run['run_id']})['status'],'cancelled')
   finally:proc.terminate();proc.wait(timeout=5)
