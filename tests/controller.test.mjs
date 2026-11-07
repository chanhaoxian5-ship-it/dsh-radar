import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {Controller} from '../lib/controller.mjs';
test('host controller starts real service and reads durable receipt',async()=>{
 const directory=await fs.mkdtemp(path.join(os.tmpdir(),'radar-host-'));const c=new Controller({dataDir:directory});
 try{await c.start();const contract=await c.call('contract');assert.equal(contract.schema_version,1);
 const args={operation_key:'host-create',id:'test',name:'隔离',rules:{schema_version:2,purpose:'test',audiences:[],include:[],exclude:[],source_ids:[]},status:'active',expected_revision:0};const receipt=await c.call('subscription_save',args);assert.deepEqual(await c.call('receipt',{operation_key:'host-create'}),receipt);
 assert.equal((await c.call('subscriptions'))[0].name,'隔离');
 }finally{await c.stop();await fs.rm(directory,{recursive:true,force:true});}
});
