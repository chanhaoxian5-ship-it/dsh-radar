import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {Controller} from '../lib/controller.mjs';
test('startup failure stays observable and corrected runtime can restart',async()=>{
 const directory=await fs.mkdtemp(path.join(os.tmpdir(),'radar-failure-'));const c=new Controller({dataDir:directory,pythonPath:path.join(directory,'missing-python.exe')});
 try{await assert.rejects(c.start(),e=>e.code==='python_unavailable');assert.equal((await c.status()).state,'failed');c.python='python';await c.start();assert.equal((await c.status()).state,'running');await c.stop();assert.equal((await c.status()).state,'stopped');}finally{await c.stop();await fs.rm(directory,{recursive:true,force:true});}
});
