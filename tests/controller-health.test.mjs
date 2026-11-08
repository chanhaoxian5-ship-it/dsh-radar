import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {once} from 'node:events';
import {Controller} from '../lib/controller.mjs';
async function fixture(fn){
 const directory=await fs.mkdtemp(path.join(os.tmpdir(),'radar-health-'));
 const c=new Controller({dataDir:directory});
 try{await fn(c,directory)}finally{await c.dispose();await fs.rm(directory,{recursive:true,force:true})}
}
test('self-check provisions fresh directory, crash recovery preserves durable records',()=>fixture(async(c,dir)=>{
 const init=await c.repair();assert.equal(init.outcome,'repaired');assert.equal(init.ok,true);
 await c.call('subscription_save',{operation_key:'health-persist',id:'test',name:'test',rules:{schema_version:2,purpose:'test',audiences:[],include:[],exclude:[],source_ids:[]},status:'active',expected_revision:0});
 const child=c.child;const closed=once(child,'close');child.kill();await closed;
 await c.watchOnce();assert.equal((await c.status()).state,'running');assert.equal((await c.call('subscriptions')).length,1);
 assert.equal(c.recovery.outcome,'repaired');
 const pid=c.child.pid;const reports=await Promise.all([c.repair(),c.repair()]);assert.ok(reports.every(r=>r.outcome==='healthy'));assert.equal(c.child.pid,pid);
 await c.stop();await c.watchOnce();assert.equal((await c.status()).state,'stopped');assert.equal(c.child,null);
}));
test('missing credential blocks repair instead of rotating it',()=>fixture(async(c,dir)=>{
 await c.start();await c.stop();await fs.unlink(path.join(dir,'agent.token'));
 const report=await c.repair();assert.equal(report.outcome,'blocked');assert.ok(report.checks.some(x=>x.name==='credentials'&&!x.ok));
 await assert.rejects(fs.access(path.join(dir,'agent.token')));assert.equal(c.child,null);
}));
test('corrupt database is not replaced',()=>fixture(async(c,dir)=>{
 await c.start();await c.stop();const db=path.join(dir,'radar.sqlite3');await fs.rm(db+'-wal',{force:true});await fs.rm(db+'-shm',{force:true});await fs.writeFile(db,'deliberately invalid database');
 const before=await fs.readFile(db);const report=await c.repair();assert.equal(report.outcome,'blocked');assert.ok(report.checks.some(x=>x.name==='database'&&!x.ok));assert.deepEqual(await fs.readFile(db),before);assert.equal(c.child,null);
}));
test('malformed endpoint remains untouched even with valid database',()=>fixture(async(c,dir)=>{
 await c.start();await c.stop();const endpoint=path.join(dir,'endpoint.json');await fs.writeFile(endpoint,'{invalid');
 const report=await c.repair();assert.equal(report.outcome,'blocked');assert.equal(await fs.readFile(endpoint,'utf8'),'{invalid');assert.equal(c.child,null);
}));
test('automatic recovery budget limits repeated failures',()=>fixture(async(c)=>{
 c.diagnose=async()=>({backend:{state:'stopped'},can_repair:true,checks:[]});let attempts=0;
 c.startInner=async()=>{attempts++;throw Error('simulated startup failure')};
 for(let i=0;i<4;i++)await c.repair({automatic:true});
 assert.equal(attempts,3);assert.equal(c.recovery.outcome,'limited');
 await c.repair();assert.equal(attempts,4);
}));
test('missing Python reports a check failure without starting',()=>fixture(async(c,dir)=>{
 c.python=path.join(dir,'missing-python.exe');const report=await c.repair();assert.equal(report.outcome,'blocked');assert.ok(report.checks.some(x=>x.name==='python_environment'&&!x.ok));assert.ok(!c.child);
}));
test('missing database in an existing owned directory is not recreated',()=>fixture(async(c,dir)=>{
 await fs.writeFile(path.join(dir,'.radar-owner'),'');
 const report=await c.repair();assert.equal(report.outcome,'blocked');assert.ok(report.checks.some(x=>x.name==='database'&&!x.ok));await assert.rejects(fs.access(path.join(dir,'radar.sqlite3')));
}));
test('live instance identity mismatch prevents repair',()=>fixture(async(c,dir)=>{
 await c.start();const pid=c.child.pid;const file=path.join(dir,'endpoint.json');const original=await fs.readFile(file,'utf8');
 try{const endpoint=JSON.parse(original);endpoint.instance_id='b'.repeat(32);await fs.writeFile(file,JSON.stringify(endpoint));const report=await c.repair();assert.equal(report.outcome,'blocked');assert.equal(report.backend.current_error.code,'instance_changed');assert.equal(c.child.pid,pid);}finally{await fs.writeFile(file,original)}
}));
