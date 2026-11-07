import json,subprocess,sys
from radar.service import Service

def compete(directory,token,rid,key):
 code="import sys,json; from radar.service import Service; p=json.load(sys.stdin); s=Service(p['directory']);\ntry: print(json.dumps({'ok':True,'value':s.call(p['token'],'claim',{'operation_key':p['key'],'run_id':p['run_id']})}))\nexcept Exception as e: print(json.dumps({'ok':False,'error':type(e).__name__}))\nfinally:s.close()"
 p=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
 return p,json.dumps({'directory':directory,'token':token,'run_id':rid,'key':key})
