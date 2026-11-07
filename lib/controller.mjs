/** Bridge the DSH host to Radar's owned loopback service and manage its lifecycle. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
const version=JSON.parse(await fs.readFile(path.join(root,'package.json'),'utf8')).version;
const fail=(code,message=code)=>Object.assign(Error(message),{code});
export const defaultDataDir=()=>path.join(process.env.DSH_HOME||path.join(os.homedir(),'.dsh'),'storages','radar');
export class Controller {
 constructor(config={}){this.dataDir=config.dataDir||defaultDataDir();if(!path.isAbsolute(this.dataDir))throw fail('absolute_data_directory_required');this.python=config.pythonPath||'python';this.pending=Promise.resolve();this.lastError=null;}
 serial(fn){const task=this.pending.then(fn,fn);this.pending=task.catch(()=>{});return task;}
 async call(method,args={},role='agent',signal){
  if(!['agent','owner','gateway','collector','runner','scheduler'].includes(role))throw fail('invalid_role');
  const endpoint=JSON.parse(await fs.readFile(path.join(this.dataDir,'endpoint.json'),'utf8'));
  if(!/^http:\/\/127\.0\.0\.1:\d{1,5}$/.test(endpoint.api_base)||!/^[a-f0-9]{32}$/.test(endpoint.instance_id))throw fail('invalid_endpoint');
  const token=(await fs.readFile(path.join(this.dataDir,role+'.token'),'ascii')).trim();
  const body=JSON.stringify({method,args});if(Buffer.byteLength(body)>262144)throw fail('invalid_request','Request exceeds 256KiB');
  const response=await fetch(endpoint.api_base+'/v1/call',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body,signal:signal?AbortSignal.any([signal,AbortSignal.timeout(15000)]):AbortSignal.timeout(15000)});
  const reader=response.body.getReader();let bytes=0;const chunks=[];
  while(true){const {done,value}=await reader.read();if(done)break;bytes+=value.length;if(bytes>4*1024*1024){await reader.cancel();throw fail('response_too_large','Use a bounded message/evidence query');}chunks.push(Buffer.from(value));}
  const result=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(!result.ok)throw fail(result.error.code,result.error.message);if(result.instance_id!==endpoint.instance_id)throw fail('instance_changed');return result.value;
 }
 async sessionCall(method,args,sessionId,signal){if(typeof sessionId!=='string'||!sessionId||sessionId.length>200)throw fail('session_identity_required','Authenticated DSH session unavailable');return this.call('session_dispatch',{session_id:sessionId,method,args},'gateway',signal);}
 async status(){
  try{const runtime=await this.call('runtime');return {state:'running',owned:Boolean(this.child),dataDir:this.dataDir,...runtime};}
  catch(error){const stopped=error.code==='ENOENT'||error.cause?.code==='ECONNREFUSED';return {state:this.lastError?'failed':stopped?'stopped':'unavailable',owned:Boolean(this.child),dataDir:this.dataDir,plugin_version:version,last_error:this.lastError||{code:error.code||'backend_unavailable'}};}
 }
 start(){return this.serial(async()=>{try{return await this.startInner()}catch(error){this.lastError={code:error.code||'backend_start_failed',message:error.message};throw error}});}
 stop(){return this.serial(()=>this.stopInner());}
 async startInner(){
  this.lastError=null;
  const existing=await this.status();
  if(existing.state==='running'){
   if(existing.plugin_version!==version)throw fail('backend_version_conflict','Stop the older owned instance before upgrade');
   return existing;
  }
  if(existing.state==='unavailable')throw fail('backend_unverified','Cannot verify existing backend; no second instance was started');
  await fs.mkdir(this.dataDir,{recursive:true});const files=await fs.readdir(this.dataDir);
  if(files.length&&!files.includes('.radar-owner')&&!files.includes('radar.sqlite3'))throw fail('unowned_data_directory','Choose an empty directory or an existing Radar data directory');
  if(!files.length)await fs.writeFile(path.join(this.dataDir,'.radar-owner'),'');
  let stderr='';let spawnError=null;
  const child=spawn(this.python,['-m','radar.server','--data-dir',this.dataDir],{cwd:root,env:{...process.env,PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1'},windowsHide:true,stdio:['ignore','ignore','pipe']});this.child=child;
  child.stderr.on('data',chunk=>{stderr=(stderr+chunk.toString('utf8')).slice(-4096)});
  child.on('error',error=>{spawnError=error;if(this.child===child)this.child=null});child.on('exit',()=>{if(this.child===child)this.child=null});
  try{
   const end=Date.now()+10000;
   while(Date.now()<end){
    if(spawnError)throw fail('python_unavailable','Configured Python executable is unavailable');
    const state=await this.status();if(state.state==='running'){if(state.plugin_version!==version)throw fail('backend_version_conflict');return state;}
    if(child.exitCode!==null){if(!child.pid)throw fail('python_unavailable','Configured Python executable is unavailable');throw fail('backend_start_failed',stderr.includes('jsonschema')?'Install Python requirements in the configured runtime':stderr.includes('nonempty')?'Configured data directory is not owned by Radar':'Backend startup failed; check Python dependencies and private data directory access');}
    await new Promise(resolve=>setTimeout(resolve,80));
   }
   throw fail('backend_start_timeout');
  }catch(error){if(child.exitCode===null)child.kill();this.lastError={code:error.code||'backend_start_failed',message:error.message};await fs.writeFile(path.join(this.dataDir,'runtime-last-error.json'),JSON.stringify(this.lastError));throw error;}
 }
 async stopInner(){const child=this.child;if(!child)return this.status();if(child.exitCode===null)await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(fail('backend_stop_unconfirmed')),5000);child.once('close',()=>{clearTimeout(timer);resolve()});child.kill();});if(this.child===child)this.child=null;this.lastError=null;return this.status();}
}
