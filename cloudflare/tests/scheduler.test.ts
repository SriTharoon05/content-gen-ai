import test from 'node:test';
import assert from 'node:assert/strict';
import {validateSchedule,saveSchedule,tick} from '../src/scheduler';
import type {Env} from '../src/types';
const valid=()=>({owner:'cloudflare',schedule:{enabled:false,run_at:'10:00',videos_per_channel:2,timezone_offset_minutes:330,daily_credit_ceiling:.8,channels:['lorehush']}});
test('schedule validates full timezone/time/budget contract',()=>assert.deepEqual(validateSchedule(valid()),valid()));
test('schedule rejects missing fields and unsafe limits',()=>{
  for(const value of [{}, {...valid(),owner:'other'}, {...valid(),schedule:{...valid().schedule,run_at:'25:00'}},
    {...valid(),schedule:{...valid().schedule,daily_credit_ceiling:NaN}},
    {...valid(),schedule:{...valid().schedule,channels:['../x']}},
    {...valid(),schedule:{...valid().schedule,enabled:'true'}}])assert.throws(()=>validateSchedule(value));
});
const env=()=>({SUPABASE_URL:'https://database.test',SUPABASE_KEY:'fixture',RENDER_API_ORIGIN:'https://render.test'} as Env);
test('verified ownership guard allows Cloudflare schedule edits without contacting Render',async()=>{
  const original=globalThis.fetch;const actions:string[]=[];
  globalThis.fetch=async(url,init)=>{
    assert.ok(String(url).startsWith('https://database.test/'));
    const data=JSON.parse(String(init?.body));actions.push(data.p_action);
    return Response.json(data.p_action==='get'?{render_guard_verified:true}:{saved:true});
  };
  try{assert.deepEqual(await saveSchedule(env(),valid()),{saved:true});assert.deepEqual(actions,['get','save']);}
  finally{globalThis.fetch=original;}
});
test('first Cloudflare ownership transfer verifies the configured Render guard server-side',async()=>{
  const original=globalThis.fetch;const actions:string[]=[];
  globalThis.fetch=async(url,init)=>{
    if(String(url)==='https://render.test/health'){actions.push('health');return Response.json({scheduler_owner_guard:true});}
    const data=JSON.parse(String(init?.body));actions.push(data.p_action);
    return Response.json(data.p_action==='get'?{render_guard_verified:false}:{saved:true});
  };
  try{await saveSchedule(env(),valid());assert.deepEqual(actions,['get','health','verify_guard','save']);}
  finally{globalThis.fetch=original;}
});
test('unverified guard remains blocked when Render times out; dashboard flags cannot bypass it',async()=>{
  const original=globalThis.fetch;let saves=0;
  globalThis.fetch=async(url,init)=>{
    if(String(url)==='https://render.test/health')throw new Error('timeout');
    const data=JSON.parse(String(init?.body));if(data.p_action==='save')saves++;
    return Response.json({render_guard_verified:false});
  };
  try{await assert.rejects(saveSchedule(env(),{...valid(),render_guard_verified:true}),error=>(error as any).status===409);assert.equal(saves,0);}
  finally{globalThis.fetch=original;}
});
test('a failed scheduled submission does not starve siblings and remains unsubmitted for replay',async()=>{
  const original=globalThis.fetch;const submitted:string[]=[];const created:string[]=[];
  globalThis.fetch=async(_url,init)=>{
    const data=JSON.parse(String(init?.body));
    if(data.p_action==='tick')return Response.json({videos:[{video_id:'failed'},{video_id:'healthy'}]});
    submitted.push(data.p_task_id);return Response.json({saved:true});
  };
  const e=env();e.GENERATION_WORKFLOW={create:async({id}:any)=>{created.push(id);if(id==='failed')throw new Error('create unavailable');},get:async()=>{throw new Error('not created');}} as any;
  try{await assert.rejects(tick(e),AggregateError);assert.deepEqual(created,['failed','healthy']);assert.deepEqual(submitted,['healthy']);}
  finally{globalThis.fetch=original;}
});
test('scheduled create response loss reuses the deterministic existing Workflow and caps dispatch at five',async()=>{
  const original=globalThis.fetch;let lookups=0;let submitted=0;
  globalThis.fetch=async(_url,init)=>{
    const data=JSON.parse(String(init?.body));
    if(data.p_action==='tick')return Response.json({videos:Array.from({length:9},(_,i)=>({video_id:String(i)}))});
    submitted++;return Response.json({saved:true});
  };
  const e=env();e.GENERATION_WORKFLOW={create:async()=>{throw new Error('response lost');},get:async()=>({status:async()=>{lookups++;return {status:'running'};}})} as any;
  try{assert.deepEqual(await tick(e),{queued:5});assert.equal(lookups,5);assert.equal(submitted,5);}
  finally{globalThis.fetch=original;}
});
