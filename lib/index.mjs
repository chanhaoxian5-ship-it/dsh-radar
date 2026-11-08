/** Register Radar's DSH service, authenticated tools, Skill, references, and native views. */
import os from 'node:os';
import path from 'node:path';
import fs from 'node:fs/promises';
import z from '@deepseek-ai/schemastery';
import {defineTool} from '@deepseek-ai/dsh-tools';
import {Remote,TypertRemoteService} from '@deepseek-ai/dsh-typert-protocol';
import {Controller,defaultDataDir} from './controller.mjs';
export const name='dsh-radar';
export const inject=['tools','skills','agents'];
export const Config=z.object({dataDir:z.string().default(defaultDataDir()),pythonPath:z.string().default('python')});
const initializers=[];
for(const method of ['query','action','diagnostics'])Remote(undefined,{kind:'method',name:method,static:false,private:false,addInitializer:fn=>initializers.push(fn)});
class RadarService extends TypertRemoteService {
 constructor(ctx,config){super(ctx,'radar');this.controller=new Controller(config);for(const fn of initializers)fn.call(this);}
 owner(){if(this.ctx.invocation?.peer?.principal?.kind!=='owner')throw Error('owner required');}
 async diagnostics(){this.owner();const skill=await this.ctx.skills.get('radar');const manual=this.ctx.tools.get('radar_manual');const read=manual?await manual.execute({name:'data-operations'},{}):null;return {registered_tools:['radar_call','radar_manual'].filter(name=>Boolean(this.ctx.tools.get(name))),skill_discovered:(await this.ctx.skills.list()).some(s=>s.name==='radar'),skill_loaded:Boolean(skill?.content),manual_read:Boolean(read?.ok),manual_length:read?.content?.length||0};}
 async query(method,args){this.owner();if(method==='backend_status')return this.controller.status();if(method==='backend_diagnose')return this.controller.diagnose();if(!['contract','subscriptions','messages','message','run','runs','sources','plans','rule_proposals','processing_queue','formal_messages','categories','events','event','diff','evidence_read','receipt','changes'].includes(method))throw Error('query method rejected');return this.controller.call(method,args,'owner');}
 async action(method,args){this.owner();if(method==='backend_start'){await this.controller.start();return this.controller.status();}if(method==='backend_stop')return this.controller.stop();if(method==='backend_repair')return this.controller.repair();if(!['subscription_save','run_start','recheck','review','discovery_plan','retry_run','cancel','source_save','rule_proposal_decide','category_save','event_save','event_message_save','queue_decide'].includes(method))throw Error('action rejected');return this.controller.call(method,args,'owner');}
}
export async function apply(ctx,config){
 const fork=ctx.plugin(RadarService,config);await fork.await();const service=ctx.get('radar');try{await service.controller.repair();}catch{/* Keep owner diagnostics, Skill and recovery controls discoverable on startup failure. */}
 const dispose=ctx.tools.register(defineTool({name:'radar_call',description:'Use DSH Radar JSON service. First read contract and bundled radar Skill. Preserve operation_key on retry. Source observation writes are excluded from this Agent credential.',parameters:{method:{type:'string',required:true},args:{type:'object',additionalProperties:true,required:true}},output:{schema:{type:'json'},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},async execute({method,args},exec){try{const agent=exec?.agent;if(!agent||ctx.agents.get(agent.id)!==agent)throw Object.assign(Error('Authenticated DSH Agent unavailable'),{code:'session_identity_required'});return {ok:true,value:await service.controller.sessionCall(method,args,agent.id,exec?.signal)}}catch(error){return {ok:false,error:{code:error.code||'outcome_unknown',message:error.message}}}}}));
 if(typeof dispose==='function')ctx.effect(()=>dispose);
 const manuals=new Set(['agent-operations','data-operations','source-onboarding','crawl4ai-engine','automation','rechecking','content-processing','queue-processing','troubleshooting','sources/cau-cast']);
 const manualTool=ctx.tools.register(defineTool({name:'radar_manual',description:'Read a bundled Radar reference by its exact reference name.',parameters:{name:{type:'string',required:true}},output:{schema:{type:'json'},render:(_args,value)=>[{type:'text',text:JSON.stringify(value)}]},async execute({name}){if(!manuals.has(name))return {ok:false,error:{code:'unknown_manual'}};return {ok:true,content:await fs.readFile(new URL('../skills/radar/references/'+name+'.md',import.meta.url),'utf8')};}}));
 if(typeof manualTool==='function')ctx.effect(()=>manualTool);
 const provider=ctx.skills.registerProvider(()=>({name:'dsh-radar',list:async()=>[{name:'radar',description:'使用DSH消息雷达关注通知或事件、整理待处理消息、管理来源和回查；为雷达接入新网站、用Crawl4AI建设或修复来源适配器时使用。不用于无关的一次性网页阅读或通用搜索。',provider:'dsh-radar',source:'bundled',rank:1000,invocation:{modelInvocable:true,userInvocable:true},locator:'radar'}],get:async candidate=>candidate.name==='radar'?{...candidate,content:await fs.readFile(new URL('../skills/radar/SKILL.md',import.meta.url),'utf8')}:undefined}));
 if(typeof provider==='function')ctx.effect(()=>provider);
 service.controller.watch();
 ctx.effect(()=>()=>service.controller.dispose());
}
