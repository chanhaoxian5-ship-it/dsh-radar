/** Bridge the DSH host to Radar's owned loopback service and manage its lifecycle. */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import {spawn,execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
const version=JSON.parse(await fs.readFile(path.join(root,'package.json'),'utf8')).version;
const fail=(code,message=code)=>Object.assign(Error(message),{code});
const execute=promisify(execFile);
export const defaultDataDir=()=>path.join(process.env.DSH_HOME||path.join(os.homedir(),'.dsh'),'storages','radar');
export class Controller {
 constructor(config={}){this.dataDir=config.dataDir||defaultDataDir();if(!path.isAbsolute(this.dataDir))throw fail('absolute_data_directory_required');this.python=config.pythonPath||'python';this.pending=Promise.resolve();this.lastError=null;this.desiredRunning=true;this.repairAttempts=[];this.recovery=null;this.watchTimer=null;this.watchChecking=false;}
 serial(fn){const task=this.pending.then(fn,fn);this.pending=task.catch(()=>{});return task;}
 async call(method,args={},role='agent',signal){
  if(!['agent','owner','gateway','collector','runner','scheduler'].includes(role))throw fail('invalid_role');
  const endpoint=JSON.parse(await fs.readFile(path.join(this.dataDir,'endpoint.json'),'utf8'));
  if(!/^http:\/\/127\.0\.0\.1:\d{1,5}$/.test(endpoint.api_base)||!/^[a-f0-9]{32}$/.test(endpoint.instance_id))throw fail('invalid_endpoint');
  const token=(await fs.readFile(path.join(this.dataDir,role+'.token'),'ascii')).trim();
  const body=JSON.stringify({method,args});if(Buffer.byteLength(body)>262144)throw fail('invalid_request','Request exceeds 256KiB');
  let response;
  try{response=await fetch(endpoint.api_base+'/v1/call',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body,signal:signal?AbortSignal.any([signal,AbortSignal.timeout(15000)]):AbortSignal.timeout(15000)});}
  catch(error){
   if(error.cause?.message!=='bad port')throw error;
   // A TCP-only probe sends no credentials and never identifies a live listener as Radar.
   const refused=await new Promise(resolve=>{const socket=net.createConnection({host:'127.0.0.1',port:Number(new URL(endpoint.api_base).port)});const done=value=>{socket.destroy();resolve(value)};socket.setTimeout(1500,()=>done(false));socket.once('connect',()=>done(false));socket.once('error',e=>done(e.code==='ECONNREFUSED'));});
   if(refused)throw Object.assign(fail('stale_endpoint','Previous backend is no longer listening'),{cause:{code:'ECONNREFUSED'}});
   throw fail('backend_port_blocked','Client rejects the existing backend port; a listener may still be active. No second instance was started.');
  }
  const reader=response.body.getReader();let bytes=0;const chunks=[];
  while(true){const {done,value}=await reader.read();if(done)break;bytes+=value.length;if(bytes>4*1024*1024){await reader.cancel();throw fail('response_too_large','Use a bounded message/evidence query');}chunks.push(Buffer.from(value));}
  const result=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(!result.ok)throw fail(result.error.code,result.error.message);if(result.instance_id!==endpoint.instance_id)throw fail('instance_changed');return result.value;
 }
 async sessionCall(method,args,sessionId,signal){if(typeof sessionId!=='string'||!sessionId||sessionId.length>200)throw fail('session_identity_required','Authenticated DSH session unavailable');return this.call('session_dispatch',{session_id:sessionId,method,args},'gateway',signal);}
 async status(){
  try{const runtime=await this.call('runtime');return {state:'running',owned:Boolean(this.child),dataDir:this.dataDir,...runtime,recovery:this.recovery};}
  catch(error){const stopped=error.code==='ENOENT'||error.cause?.code==='ECONNREFUSED';return {state:this.lastError?'failed':stopped?'stopped':'unavailable',owned:Boolean(this.child),dataDir:this.dataDir,plugin_version:version,current_error:{code:error.code||error.cause?.code||'backend_unavailable',message:error.message,connection_refused:error.cause?.code==='ECONNREFUSED'},last_error:this.lastError||{code:error.code||'backend_unavailable',message:error.message},recovery:this.recovery};}
 }
 start(){this.desiredRunning=true;this.repairAttempts=[];return this.serial(async()=>{try{return await this.startInner()}catch(error){this.lastError={code:error.code||'backend_start_failed',message:error.message};throw error}});}
 stop(){this.desiredRunning=false;return this.serial(()=>this.stopInner());}
 async diagnose(){
  const backend=await this.status();let checks;
  try{const result=await execute(this.python,['-m','radar.health','--data-dir',this.dataDir],{cwd:root,env:{...process.env,PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1'},windowsHide:true,timeout:10000,maxBuffer:65536});checks=JSON.parse(result.stdout);}
  catch(error){checks=[{name:'python_environment',ok:false,message:error.code==='ENOENT'?'找不到配置的 Python，请修复运行环境':'本地自检未完成（超时、权限或运行环境异常），不自动修复'}];}
  const current=backend.current_error;
  // ENOENT alone is insufficient: it could mean a missing credential, not a dead service.
  let endpointMissing=false;try{await fs.access(path.join(this.dataDir,'endpoint.json'));}catch(e){endpointMissing=e.code==='ENOENT';}
  const restartable=backend.state!=='running'&&(current?.connection_refused||endpointMissing)&&checks.every(c=>c.ok);
  const connected=backend.state==='running';
  checks.push({name:'backend_connection',ok:connected,message:connected?'后端应答及实例身份验证通过':restartable?'后端未运行，可安全尝试重新启动':current?.code==='backend_port_blocked'?'旧端口被客户端拒绝且仍可能有监听者，需要人工核对':current?.code==='forbidden'?'服务拒绝凭据，不自动重置身份':'服务身份或连接状态未确认，保留现场'});
  if(connected)checks.push({name:'backend_version',ok:backend.plugin_version===version,message:backend.plugin_version===version?'插件和后端版本一致':'后端与插件版本不一致，需要受控升级'});
  if(connected)checks.push({name:'collector',ok:backend.worker_state!=='error'&&!backend.last_error,message:backend.worker_state==='error'||backend.last_error?'采集器报告异常，请查看运行记录；不会因此重启或重复采集':'未检测到采集器运行错误'});
  return {checked_at:new Date().toISOString(),ok:checks.every(c=>c.ok),can_repair:restartable,checks,backend};
 }
 repair({automatic=false}={}){return this.serial(async()=>{
  if(automatic&&!this.desiredRunning)return {outcome:'paused',message:'已手动停止，不自动拉起',backend:await this.status()};
  if(!automatic){this.desiredRunning=true;this.repairAttempts=[];}
  const before=await this.diagnose();
  if(before.backend.state==='running')return {...before,outcome:before.ok?'healthy':'needs_attention',message:before.ok?'自检通过，无需修复':'服务运行中，存在需处理的检查项；未重启服务'};
  if(!before.can_repair){this.recovery={at:new Date().toISOString(),outcome:'blocked',message:'不能安全自动修复，请按检查结果处理'};return {...before,...this.recovery};}
  const now=Date.now();this.repairAttempts=this.repairAttempts.filter(t=>now-t<600000);
  if(automatic&&this.repairAttempts.length>=3){this.recovery={at:new Date().toISOString(),outcome:'limited',message:'10 分钟内已尝试恢复 3 次，暂缓自动恢复；可手动自检'};return {...before,...this.recovery};}
  this.repairAttempts.push(now);
  try{await this.startInner();this.lastError=null;const after=await this.diagnose();this.recovery={at:new Date().toISOString(),outcome:after.ok?'repaired':'needs_attention',message:after.ok?'服务已恢复，复检通过':'已尝试恢复，仍有检查项未通过'};return {...after,...this.recovery};}
  catch(error){this.lastError={code:error.code||'backend_start_failed',message:error.message};this.recovery={at:new Date().toISOString(),outcome:'failed',message:'恢复未成功，保留原数据；请查看自检结果'};return {...await this.diagnose(),...this.recovery};}
 });}
 watch(){if(this.watchTimer)return;this.watchTimer=setInterval(()=>{this.watchOnce().catch(()=>{});},30000);this.watchTimer.unref?.();}
 async watchOnce(){
  if(this.watchChecking||!this.desiredRunning)return;
  this.watchChecking=true;
  try{const state=await this.status();if(state.state!=='running')await this.repair({automatic:true});}
  finally{this.watchChecking=false;}
 }
 dispose(){clearInterval(this.watchTimer);this.watchTimer=null;return this.stop();}
 async startInner(){
  this.lastError=null;
  const existing=await this.status();
  if(existing.state==='running'){
   if(existing.plugin_version!==version)throw fail('backend_version_conflict','Stop the older owned instance before upgrade');
   return existing;
  }
  if(existing.state==='unavailable')throw fail(existing.last_error?.code==='backend_port_blocked'?'backend_port_blocked':'backend_unverified',existing.last_error?.message||'Cannot verify existing backend; no second instance was started');
  await fs.mkdir(this.dataDir,{recursive:true});const files=await fs.readdir(this.dataDir);
  if(files.length&&!files.includes('.radar-owner')&&!files.includes('radar.sqlite3'))throw fail('unowned_data_directory','Choose an empty directory or an existing Radar data directory');
  if(!files.length)await fs.writeFile(path.join(this.dataDir,'.radar-owner'),'');
  let stderr='';let spawnError=null;
  const child=spawn(this.python,['-m','radar.server','--data-dir',this.dataDir],{cwd:this.dataDir,env:{...process.env,PYTHONPATH:[root,process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1'},windowsHide:true,stdio:['ignore','ignore','pipe']});this.child=child;
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
