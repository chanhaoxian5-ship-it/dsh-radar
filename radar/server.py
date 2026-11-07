"""Loopback HTTP service with authenticated calls, collection worker, and plan ticks."""
import argparse, json, os, secrets, threading, time, sqlite3
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from .service import Service, Forbidden
from .engine import Conflict

def serve(directory, port=0):
    directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
    from .windows import owned_directory,secure_directory,own_child_processes
    owned_directory(directory);(directory/'.radar-owner').touch(exist_ok=True)
    lock=os.fdopen(os.open(directory/'server.lock',os.O_RDWR|os.O_CREAT),'r+b'); lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    secure_directory(directory);own_child_processes()
    service=Service(directory)
    for name,permissions,authority in [('agent',['read','manage','analyze','execute'],'agent'),('collector',['read','collect','execute'],'agent'),('owner',['read','manage','analyze','execute','user'],'user'),('runner',['read','collect','execute'],'agent'),('scheduler',['schedule'],'agent'),('gateway',['delegate'],'agent')]:
        token_file=directory/(name+'.token')
        if not token_file.exists():
            token=service.provision(permissions,['*'],authority)
            token_file.write_text(token,encoding='ascii'); os.chmod(token_file,0o600)
    instance=secrets.token_hex(16)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup();self.connection.settimeout(15)
        def log_message(self,*args): pass
        def do_POST(self):
            status=200
            try:
                if self.path!='/v1/call' or self.headers.get('Origin') or self.headers.get('Sec-Fetch-Site') not in (None,'none'): raise Forbidden('browser requests require host bridge')
                if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}': raise Forbidden('host rejected')
                if self.headers.get('Transfer-Encoding'): raise ValueError('transfer encoding unsupported')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=262144: raise ValueError('invalid request size')
                if self.headers.get_content_type()!='application/json': raise ValueError('JSON required')
                auth=self.headers.get('Authorization','')
                if not auth.startswith('Bearer '): raise Forbidden('credential required')
                def pairs(items):
                    result={}
                    for k,v in items:
                        if k in result: raise ValueError('duplicate JSON key')
                        result[k]=v
                    return result
                request=json.loads(self.rfile.read(length),object_pairs_hook=pairs,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
                if set(request)!={'method','args'} or not isinstance(request['args'],dict): raise ValueError('invalid envelope')
                result=service.call(auth[7:],request['method'],request['args'])
                response={'ok':True,'value':result,'instance_id':instance}
            except PermissionError as e: status=403; response={'ok':False,'error':{'code':'forbidden','message':str(e)}}
            except Conflict as e: status=409; response={'ok':False,'error':{'code':'conflict','message':str(e)}}
            except (ValueError,KeyError,TypeError) as e: status=400; response={'ok':False,'error':{'code':'invalid_request','message':str(e)}}
            except sqlite3.OperationalError as error:
                status=507 if getattr(error,'sqlite_errorcode',None)==sqlite3.SQLITE_FULL else 503;response={'ok':False,'error':{'code':'storage_full' if status==507 else 'storage_unavailable','message':'Read original operation receipt; storage must be repaired before retrying.'}}
            except Exception: status=500; response={'ok':False,'error':{'code':'outcome_unknown','message':'Read original receipt before retrying a write.'}}
            body=json.dumps(response,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)
    from .worker import Worker
    from .engine import now
    runner_token=(directory/'runner.token').read_text(encoding='ascii')
    scheduler_token=(directory/'scheduler.token').read_text(encoding='ascii')
    service.engine.startup_recover(service.authenticate(scheduler_token)['id'],instance)
    stop_event=threading.Event()
    service.runtime_state={'worker_state':'idle','last_error':None}
    def collector_loop():
        worker_service=None
        try:
            worker_service=Service(directory);worker=Worker(worker_service,runner_token,None)
            while not stop_event.is_set():
                try:
                    worker_service.load_adapters();service.runtime_state['worker_state']='collecting'
                    worker.drain(limit=1);service.runtime_state['worker_state']='idle'
                except Exception as error:
                    service.runtime_state['last_error']=type(error).__name__;service.runtime_state['worker_state']='error'
                stop_event.wait(.25)
        finally:
            if worker_service:worker_service.close()
    worker_thread=threading.Thread(target=collector_loop,name='radar-collector',daemon=True);worker_thread.start()
    class RadarHTTPServer(HTTPServer):
        last_tick=0
        def service_actions(self):
            if time.monotonic()-self.last_tick>=60:
                try:
                    clock=now();service.call(scheduler_token,'tick',{'operation_key':'tick:'+clock,'at':clock});self.last_tick=time.monotonic()
                except Exception as error:service.runtime_state['last_error']=type(error).__name__
    server=RadarHTTPServer(('127.0.0.1',port),Handler); server.timeout=1
    endpoint={'api_base':f'http://127.0.0.1:{server.server_port}','instance_id':instance,'pid':os.getpid()}
    temp=directory/'endpoint.tmp'; temp.write_text(json.dumps(endpoint),encoding='utf-8'); temp.replace(directory/'endpoint.json')
    try: server.serve_forever()
    finally:
        stop_event.set();worker_thread.join(timeout=2);server.server_close(); service.close(); (directory/'endpoint.json').unlink(missing_ok=True); lock.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--data-dir',required=True); parser.add_argument('--port',type=int,default=0); args=parser.parse_args(); serve(args.data_dir,args.port)
