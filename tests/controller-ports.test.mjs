import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {Controller} from '../lib/controller.mjs';
for (const live of [false,true]) test(`restricted port with ${live?'live listener':'stale endpoint'}`,async()=>{
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'radar-port-'));
 const server=net.createServer(s=>s.end());
 const c=new Controller({dataDir:dir});
 try{
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(1720,'127.0.0.1',resolve)});
  if(!live)await new Promise(resolve=>server.close(resolve));
  await c.start();await c.stop();
  await fs.writeFile(path.join(dir,'endpoint.json'),JSON.stringify({api_base:'http://127.0.0.1:1720',instance_id:'a'.repeat(32)}));
  if(live){await assert.rejects(c.start(),e=>e.code==='backend_port_blocked');assert.ok(!c.child);}
  else{
   assert.equal((await c.status()).state,'stopped');
   await c.start();assert.equal((await c.status()).state,'running');
   const endpoint=JSON.parse(await fs.readFile(path.join(dir,'endpoint.json')));
   assert.ok(Number(new URL(endpoint.api_base).port)>=49152);
   const pid=c.child.pid;await c.start();assert.equal(c.child.pid,pid);
  }
 }finally{await c.stop();if(server.listening)await new Promise(resolve=>server.close(resolve));await fs.rm(dir,{recursive:true,force:true});}
});
