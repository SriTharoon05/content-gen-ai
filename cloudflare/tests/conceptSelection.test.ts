import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const compiled=await build({stdin:{contents:"export {reserveConceptSet,context} from './src/stages';export {GenerationWorkflow} from './src/generation';",resolveDir:process.cwd()},bundle:true,platform:'node',format:'esm',write:false,plugins:[{name:'mock-workflows',setup(b){b.onResolve({filter:/^cloudflare:workers$/},()=>({path:'stub',namespace:'test'}));b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:'export class WorkflowEntrypoint { constructor(ctx,env){this.env=env;} }',loader:'js'}));}}]});
const {reserveConceptSet,context,GenerationWorkflow}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const env={SUPABASE_URL:'https://database.test',SUPABASE_KEY:'fixture'};
const id='a'.repeat(32);
const candidates=(round=0)=>Array.from({length:5},(_,i)=>({core_entity:'entity-'+round+'-'+i,content_angle:'angle-'+i,core_concept:'Original concept '+round+' '+i}));
test('only concept planning receives prior history; every schema retains owner instructions and run context',()=>{
  const c={channel:{name:'Test channel',niche:'Science',slug:'custom',strategy_json:{instructions:'Owner rules'},overrides_json:{tone:'Warm'}},options:{topic:'Requested topic'},prior:Array.from({length:40},(_,i)=>({core_concept:'historic-'+i}))};
  const planning=context(c,'UniqueConceptSet');assert.ok(planning.includes('historic-29'));assert.ok(!planning.includes('historic-30'));assert.equal(planning.split('Owner rules').length-1,1);
  for(const schema of ['Premise','Script','QAFinding','VoiceDirection','VisualPlan','EditPlan','PublishCopy']){
    const value=context(c,schema);assert.ok(!value.includes('PRIOR CONCEPTS'));assert.ok(!value.includes('historic-0'));
    assert.ok(value.includes('Owner rules'));assert.ok(value.includes('Requested topic'));assert.ok(value.includes('Warm'));assert.ok(value.includes('Science'));
  }
});
test('five collisions become a durable replan result without weakening SQL reservations',async()=>{
  const original=globalThis.fetch;let embeds=0,reserves=0;
  globalThis.fetch=async(_url,init)=>{const p=JSON.parse(String(init?.body));assert.equal(p.p_action,'reserve');reserves++;return Response.json({collision:true});};
  try{
    const result=await reserveConceptSet(env,id,{candidates:candidates()},{settings:{keys:{gemini_free:['1','2','3','4','5','6']}}},async(c:any)=>{embeds++;assert.equal(c.settings.keys.gemini_free.length,5);return 'measured-vector';});
    assert.equal(result.collision,true);assert.equal(result.attempted.length,5);assert.equal(embeds,5);assert.equal(reserves,5);
  }finally{globalThis.fetch=original;}
});
test('replans skip normalized rejected pairs and return the canonical first accepted ledger row',async()=>{
  const original=globalThis.fetch;const calls:string[]=[];const set=candidates(1);const reserved={id:'ledger-id',core_entity:set[1].core_entity,content_angle:set[1].content_angle,core_concept:set[1].core_concept};
  globalThis.fetch=async(_url,init)=>{const p=JSON.parse(String(init?.body));calls.push(p.p_payload.core_entity);return Response.json(reserved);};
  try{
    const result=await reserveConceptSet(env,id,{candidates:set,avoid:[{core_entity:' ENTITY-1-0 ',content_angle:'ANGLE-0'}]},{settings:{keys:{gemini_free:['fixture']}}},async()=> 'measured-vector');
    assert.deepEqual(result,reserved);assert.deepEqual(calls,['entity-1-1']);
  }finally{globalThis.fetch=original;}
});
test('three exhausted concept sets stop before TTS/images and keep the eight-stage continuation boundary',async()=>{
  await exerciseCoordinator(false);
});
test('third-set acceptance saves the canonical concept before continuation and replay reuses it',async()=>{
  await exerciseCoordinator(true);
});
async function exerciseCoordinator(acceptThird:boolean){
  const original=globalThis.fetch;const saved:any={};const children:any[]=[];const continuations:any[]=[];const mutations:string[]=[];let selected:any;
  globalThis.fetch=async(_url,init)=>{
    const p=JSON.parse(String(init?.body));
    if(p.p_action==='status')return Response.json({steps:saved});
    if(p.p_action==='save'){mutations.push(p.p_payload.key);saved[p.p_payload.key]=p.p_payload.value;return Response.json(p.p_payload.value);}
    if(p.p_action==='fail'){mutations.push('fail');return Response.json({ok:true});}
    throw new Error('No media or direct reservation from coordinator');
  };
  const e={...env,GENERATION_STAGE:{create:async(p:any)=>{children.push(p);},get:async()=>({status:async()=>({status:'errored'})})},GENERATION_WORKFLOW:{create:async(p:any)=>{continuations.push(p);}}};
  const runner:any={do:async(_name:string,...args:any[])=>args.at(-1)(),sleep:async()=>{throw new Error('No TTS rate sleeps');},waitForEvent:async()=>{
    const p=children.at(-1).params;let value:any;
    if(p.op==='profile')value={language:'en',conversation:false,options:{}};
    else if(p.op==='default-bgm')value={music:null};
    else if(p.op==='model'&&p.data.schema==='UniqueConceptSet'){
      assert.match(p.data.prompt,/established explanatory mechanism/);
      assert.match(p.data.prompt,/Do not invent breakthroughs/);
      assert.match(p.data.prompt,/future, noncurrent and unproven/);
      const round=p.name==='concepts'?0:Number(p.name.at(-1));value={candidates:candidates(round)};
      if(round>0){assert.match(p.data.prompt,/REJECTED ENTITY\/ANGLE PAIRS/);assert.ok(p.data.prompt.includes('entity-0-0'));}
    }else if(p.op==='concept'){
      assert.equal(p.retries,0);assert.equal(p.data.candidates.length,5);
      if(p.name==='concept-retry-2'&&acceptThird){selected={id:'reserved',...p.data.candidates[0]};value=selected;}
      else value={collision:true,attempted:p.data.candidates};
    }else if(p.name==='premise'){
      assert.ok(p.data.prompt.includes('entity-2-0'));
      assert.match(p.data.prompt,/creative proposal, NOT evidence/);
      assert.match(p.data.prompt,/Remove invented details, studies or speculative practical applications/);
      throw new Error('Stop test after verified concept replay');
    }
    else throw new Error('Must not call TTS or image operations');
    saved[p.name]=value;return {payload:{ok:true,value}};
  }};
  try{
    const workflow=new GenerationWorkflow({},e);
    if(!acceptThird){
      await assert.rejects(workflow.run({instanceId:id,payload:{videoId:id}},runner),/three distinct sets; no TTS or image spend/);
      assert.deepEqual(mutations,['fail']);assert.equal(continuations.length,0);
    }else{
      const first=await workflow.run({instanceId:id,payload:{videoId:id}},runner);
      assert.equal(first.status,'continued');assert.equal(continuations.length,1);assert.deepEqual(saved.concept,selected);assert.deepEqual(mutations,['concept']);
      const previous=children.length;const next=continuations[0];
      await assert.rejects(workflow.run({instanceId:next.id,payload:next.params},runner),/verified concept replay/);
      assert.deepEqual(children.slice(previous).map(c=>c.params.name),['premise']);
    }
    assert.equal(children.slice(0,8).length,8);
    assert.deepEqual(children.slice(0,8).map(c=>c.params.name),['profile','default-bgm','concepts','concept','reconcepts-1','concept-retry-1','reconcepts-2','concept-retry-2']);
    assert.ok(children.every(c=>!['gemini-audio','groq-audio','image','image-batch','media-submit'].includes(c.params.op)));
  }finally{globalThis.fetch=original;}
}
