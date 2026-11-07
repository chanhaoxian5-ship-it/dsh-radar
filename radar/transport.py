"""Fetch bounded public HTTPS responses with system curl and default TLS validation."""
import shutil,subprocess,tempfile,re,time
from functools import lru_cache
from pathlib import Path
from .backend import Response

@lru_cache(maxsize=4)
def check_curl(executable):
 result=subprocess.run([executable,'--disable','--version'],capture_output=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 match=re.match(rb'curl (\d+)\.(\d+)\.(\d+)',result.stdout)
 if result.returncode or not match or tuple(map(int,match.groups()))<(8,4,0):raise OSError('curl >=8.4 required for bounded response size')

def curl_fetch(url,timeout=20,data=None):
 executable=shutil.which('curl.exe') or shutil.which('curl')
 if not executable:raise OSError('curl transport unavailable')
 check_curl(executable)
 with tempfile.TemporaryDirectory(prefix='radar-http-') as directory:
  body=Path(directory)/'body';headers=Path(directory)/'headers'
  arguments=[executable,'--disable','--silent','--show-error','--max-time',str(timeout),'--proto','=https','--max-redirs','0','--max-filesize','10485760','--dump-header',str(headers),'--output',str(body),'--write-out','%{http_code}','--url',url]
  if data is not None:arguments.extend(['--data-raw',data.decode('ascii')])
  for attempt in range(2):
   try:result=subprocess.run(arguments,capture_output=True,timeout=timeout+5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
   except subprocess.TimeoutExpired as error:raise TimeoutError('curl transport timeout') from error
   if result.returncode!=35 or attempt==1:break
   time.sleep(.5)  # One bounded retry of an intermittent TLS handshake failure; validation remains on.
  status=int(result.stdout.decode('ascii','replace').strip() or '0')
  if result.returncode not in (0,63):
   # No raw stderr: it can contain local proxy configuration. Keep a deterministic failure code.
   raise OSError('curl transport failed (exit '+str(result.returncode)+')')
  raw=body.read_bytes() if body.exists() else b'';metadata={}
  if headers.exists():
   for line in headers.read_text(encoding='iso-8859-1').splitlines():
    if line.startswith('HTTP/'):metadata={}
    elif ':' in line:
     key,value=line.split(':',1);metadata[key.lower()]=value.strip()
  declared=metadata.get('content-length');complete=result.returncode==0 and len(raw)<=10485760
  if declared and (not declared.isdigit() or int(declared)!=len(raw)):complete=False
  return Response(status,url,raw[:10485760],metadata,complete)
