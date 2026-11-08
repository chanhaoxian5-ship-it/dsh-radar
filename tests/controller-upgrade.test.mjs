import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
test('running backend does not lock its plugin installation directory',async()=>{
 const base=await fs.mkdtemp(path.join(os.tmpdir(),'radar-upgrade-'));const installed=path.join(base,'plugin');
 await fs.mkdir(path.join(installed,'lib'),{recursive:true});await fs.copyFile(new URL('../lib/controller.mjs',import.meta.url),path.join(installed,'lib/controller.mjs'));await fs.copyFile(new URL('../package.json',import.meta.url),path.join(installed,'package.json'));await fs.cp(new URL('../radar',import.meta.url),path.join(installed,'radar'),{recursive:true});
 const {Controller}=await import(pathToFileURL(path.join(installed,'lib/controller.mjs')));const c=new Controller({dataDir:path.join(base,'data')});
 try{await c.start();await fs.rename(installed,path.join(base,'plugin-old'));assert.equal((await c.status()).state,'running');}finally{await c.stop();await fs.rm(base,{recursive:true,force:true});}
});
