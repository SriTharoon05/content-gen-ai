import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {URL as NodeURL} from 'node:url';
import {build} from 'esbuild';
import {image,imageRetryDelay,POLLINATIONS_IMAGE_ENDPOINT} from '../src/providers';
import type {Env} from '../src/types';

const compiled=await build({stdin:{contents:"export {generateImageBatch,GenerationStageWorkflow} from './src/stages';",resolveDir:process.cwd()},bundle:true,platform:'node',format:'esm',write:false,plugins:[{name:'test-workflows',setup(b){b.onResolve({filter:/^cloudflare:workers$/},()=>({path:'stub',namespace:'test'}));b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:'export class WorkflowEntrypoint {constructor(ctx,env){this.env=env;}}',loader:'js'}));}}]});
const {generateImageBatch,GenerationStageWorkflow}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const env={SUPABASE_URL:'https://database.test',SUPABASE_KEY:'db-fixture',CLOUDINARY_CLOUD_NAME:'test',CLOUDINARY_API_KEY:'cloud-fixture',CLOUDINARY_API_SECRET:'cloud-fixture',CLOUDINARY_PREFIX:'test'} as Env;
const id='a'.repeat(32);
const prompt='An original close-up scene with a clear focal point.';
const asset={url:'https://res.cloudinary.com/test/image/upload/a.png',sha256:'a'.repeat(64)};
const cfg=(keys=['first-image-key'])=>({settings:{models:{image_model:'lykon/dreamshaper-8-lcm',image_catalog:[{id:'lykon/dreamshaper-8-lcm',credits:.0001}],image_endpoint:POLLINATIONS_IMAGE_ENDPOINT,image_size:'1080x1920'},keys:{pollinations:keys}}});
const hash=(text:string)=>createHash('sha256').update(text).digest('hex');
const result=()=>Response.json({data:[{url:'https://generated.test/asset.png'}]},{headers:{'X-Cache':'HIT'}});

// All external operations are mocked, including the PostgreSQL transaction result.
// No provider credentials, database connections, or live writes are used.
async function fixture(run:(f:any)=>Promise<void>){
  const originalFetch=globalThis.fetch,originalTimeout=globalThis.setTimeout;
  const rows=new Map<string,any>(),provider:any[]=[],actions:any[]=[],delays:number[]=[];
  let externals=0;
  const f:any={rows,provider,actions,delays,response:async()=>result(),permit:()=>({wait_ms:0}),
    get externals(){return externals;},start(payload:any){
      const timestamp=new Date().toISOString();
      return {status:'reserved',model:payload.model,created_at:timestamp,detail_json:{image_request:{...payload.request,started_at:timestamp,auth_rotations:0}}};
    }};
  globalThis.setTimeout=((callback:any,ms:any,...args:any[])=>{delays.push(Number(ms));queueMicrotask(()=>callback(...args));return 0;}) as any;
  globalThis.fetch=async(url,init)=>{
    externals++;const target=String(url);
    if(target.startsWith('https://database.test/')){
      const rpc=JSON.parse(String(init?.body)),p=rpc.p_payload;actions.push(rpc);
      if(rpc.p_action==='status')return Response.json({steps:{}});
      if(rpc.p_action==='config')return Response.json({channel:{},settings:cfg().settings});
      if(rpc.p_action==='call_start'){
        if(rows.has(p.key))return Response.json(rows.get(p.key));
        const row=f.start(p);rows.set(p.key,row);return Response.json({...row,new:true});
      }
      if(rpc.p_action==='call_rekey'){
        const row=rows.get(p.key),request=row.detail_json.image_request;
        assert.equal(request.key_fingerprint,p.expected_fingerprint);assert.equal(request.request_hash,p.request_hash);
        assert.ok([401,403].includes(p.auth_status));request.key_fingerprint=p.key_fingerprint;request.auth_rotations++;
        return Response.json(row);
      }
      if(rpc.p_action==='image_permit')return Response.json(f.permit(p));
      if(rpc.p_action==='call_finish'){
        const row=rows.get(p.key);assert.equal(row.detail_json.image_request.request_hash,p.request_hash);
        row.status='settled';row.detail_json.asset=p.value;row.detail_json.provider_response={cache_status:p.cache_status,provider_attempts:p.provider_attempts};
        return Response.json({ok:true});
      }
      if(rpc.p_action==='save')return Response.json(p.value);
      throw new Error('Unexpected RPC '+rpc.p_action);
    }
    if(target===POLLINATIONS_IMAGE_ENDPOINT){
      const sent={body:String(init?.body),key:new Headers(init?.headers).get('Authorization'),redirect:init?.redirect};provider.push(sent);
      return f.response(sent,provider.length);
    }
    if(target==='https://generated.test/asset.png')return new Response(new Uint8Array(4096));
    if(target.startsWith('https://api.cloudinary.com/'))return Response.json({secure_url:asset.url});
    throw new Error('Unexpected external URL');
  };
  try{await run(f);}finally{globalThis.fetch=originalFetch;globalThis.setTimeout=originalTimeout;}
}

test('503 then success reuses exact bytes, deterministic seed and key, records cache evidence',async()=>fixture(async f=>{
  f.response=async(_:any,attempt:number)=>attempt===1?new Response('{}',{status:503,headers:{'Retry-After':'999'}}):result();
  await image(env,id,'image-s001',cfg(),prompt);
  assert.equal(f.provider.length,2);assert.deepEqual(f.provider[0],f.provider[1]);
  const body=JSON.parse(f.provider[0].body),request=f.rows.get('image-s001').detail_json.image_request;
  assert.equal(body.seed,parseInt(hash(JSON.stringify([id,'image-s001',prompt])).slice(0,8),16)&0x7fffffff);
  assert.equal(request.body_json,f.provider[0].body);assert.equal(request.key_fingerprint,hash('first-image-key'));
  assert.equal(request.request_hash,hash(JSON.stringify([POLLINATIONS_IMAGE_ENDPOINT,f.provider[0].body])));
  assert.equal(request.started_at,f.rows.get('image-s001').created_at);
  assert.ok(!JSON.stringify(f.actions[0].p_payload).includes('first-image-key'));
  assert.deepEqual(f.delays,[30000]);assert.equal(f.rows.get('image-s001').detail_json.provider_response.cache_status,'HIT');
  assert.equal(f.actions.filter((r:any)=>r.p_action==='call_start').length,1);
  assert.equal(f.actions.filter((r:any)=>r.p_action==='call_finish').length,1);
}));

test('transport timeout and response-body loss retry the exact request',async()=>{
  for(const loss of ['request','body'])await fixture(async f=>{
    f.response=async(_:any,attempt:number)=>{
      if(attempt===1){if(loss==='request')throw new DOMException('Timeout','TimeoutError');const response=result();response.json=async()=>{throw new TypeError('Body connection lost');};return response;}
      return result();
    };
    await image(env,id,'image-s001',cfg(['first-image-key','second-image-key']),prompt);
    assert.equal(f.provider.length,2);assert.deepEqual(f.provider[0],f.provider[1]);assert.deepEqual(f.delays,[2000]);
    assert.ok(!f.actions.some((r:any)=>r.p_action==='call_rekey'));
  });
});

test('legacy uncertain reservation and unsupported metadata versions fail before provider HTTP',async()=>{
  for(const version of [undefined,0,2])await fixture(async f=>{
    f.rows.set('image-s001',{status:'reserved',detail_json:version===undefined?{}:{image_request:{version}}});
    await assert.rejects(image(env,id,'image-s001',cfg(),prompt),/uncertain.*versioned/);
    assert.equal(f.provider.length,0);assert.equal(f.externals,1);
  });
});

test('changed body, key, seed, hash, model and timestamp reject replay; expired/future replay rejects',async()=>{
  for(const change of ['body','key','seed','hash','model','timestamp','expired','future'])await fixture(async f=>{
    f.response=async()=>new Response('{}',{status:503});
    await assert.rejects(image(env,id,'image-s001',cfg(),prompt),/three identical/);
    f.provider.length=0;
    const row=f.rows.get('image-s001'),request=row.detail_json.image_request;
    let c=cfg(),nextPrompt=prompt;
    if(change==='body')nextPrompt+=' A different scene.';
    if(change==='key')c=cfg(['replacement-key']);
    if(change==='seed')request.seed++;
    if(change==='hash')request.request_hash='f'.repeat(64);
    if(change==='model')row.model='different-model';
    if(change==='timestamp')request.started_at=new Date(Date.now()-10000).toISOString();
    if(change==='expired')request.started_at=row.created_at=new Date(Date.now()-15*60*1000-1).toISOString();
    if(change==='future')request.started_at=row.created_at=new Date(Date.now()+60000).toISOString();
    await assert.rejects(image(env,id,'image-s001',c,nextPrompt),/uncertain/);assert.equal(f.provider.length,0);
  });
});

test('versioned ambiguous reservation replays the original key even when owner key order changes',async()=>fixture(async f=>{
  f.response=async()=>new Response('{}',{status:502});
  await assert.rejects(image(env,id,'image-s001',cfg(),prompt),/three identical/);
  const original=f.provider[0];f.provider.length=0;f.response=async()=>result();
  await image(env,id,'image-s001',cfg(['second-image-key','first-image-key']),prompt);
  assert.deepEqual(f.provider,[original]);assert.ok(!f.actions.some((r:any)=>r.p_action==='call_rekey'));
}));

test('settled versioned or legacy reservation recovers existing asset without provider purchase',async()=>{
  for(const detail_json of [asset,{image_request:{version:1},asset}])await fixture(async f=>{
    f.rows.set('image-s001',{status:'settled',detail_json});
    assert.deepEqual(await image(env,id,'image-s001',cfg(),prompt),asset);
    assert.equal(f.provider.length,0);assert.equal(f.externals,1);
  });
});

test('maximum three provider attempts share six permit checks and never rotate on ambiguous responses',async()=>fixture(async f=>{
  f.response=async()=>new Response('{}',{status:429});
  let gates=0;f.permit=()=>({wait_ms:++gates%2?100:0});
  await assert.rejects(image(env,id,'image-s001',cfg(['first-image-key','second-image-key']),prompt,{maxKeys:3,maxPermitChecks:999}),/three identical/);
  assert.equal(f.provider.length,3);assert.equal(f.actions.filter((r:any)=>r.p_action==='image_permit').length,6);
  assert.deepEqual(f.provider,[f.provider[0],f.provider[0],f.provider[0]]);
  assert.ok(!f.actions.some((r:any)=>r.p_action==='call_rekey'));
  assert.ok(f.delays.includes(2000)&&f.delays.includes(4000));
}));

test('busy gate safely stops at six checks with no provider HTTP',async()=>fixture(async f=>{
  f.permit=()=>({wait_ms:8000});
  await assert.rejects(image(env,id,'image-s001',cfg(),prompt),/rate gate busy/);
  assert.equal(f.provider.length,0);assert.equal(f.actions.filter((r:any)=>r.p_action==='image_permit').length,6);
}));

test('definitive new-call 401/403 key rotation is durable before HTTP; replay and ambiguous calls cannot rotate',async()=>{
  await fixture(async f=>{
    f.response=async(_:any,attempt:number)=>attempt<3?new Response('{}',{status:attempt===1?401:403}):result();
    await image(env,id,'image-s001',cfg(['first-image-key','second-image-key','third-image-key']),prompt);
    assert.deepEqual(f.provider.map((p:any)=>p.key),['Bearer first-image-key','Bearer second-image-key','Bearer third-image-key']);
    assert.equal(new Set(f.provider.map((p:any)=>p.body)).size,1);
    assert.equal(f.rows.get('image-s001').detail_json.image_request.key_fingerprint,hash('third-image-key'));
    assert.equal(f.actions.filter((r:any)=>r.p_action==='call_rekey').length,2);
  });
  await fixture(async f=>{
    f.response=async(_:any,attempt:number)=>new Response('{}',{status:attempt===1?503:401});
    await assert.rejects(image(env,id,'image-s001',cfg(['first-image-key','second-image-key']),prompt),/no ambiguous key rotation/);
    assert.equal(new Set(f.provider.map((p:any)=>p.key)).size,1);assert.ok(!f.actions.some((r:any)=>r.p_action==='call_rekey'));
    f.provider.length=0;f.response=async()=>new Response('{}',{status:401});
    await assert.rejects(image(env,id,'image-s001',cfg(['first-image-key','second-image-key']),prompt),/no ambiguous key rotation/);
    assert.equal(f.provider.length,1);assert.ok(!f.actions.some((r:any)=>r.p_action==='call_rekey'));
  });
});

test('400/402 are not retried and custom endpoints fail closed',async()=>{
  for(const status of [400,402])await fixture(async f=>{
    f.response=async()=>new Response('{}',{status});
    await assert.rejects(image(env,id,'image-s001',cfg(),prompt),new RegExp('HTTP '+status));
    assert.equal(f.provider.length,1);assert.deepEqual(f.delays,[]);
  });
  await fixture(async f=>{
    const c=cfg();c.settings.models.image_endpoint='https://custom.test/images';
    await assert.rejects(image(env,id,'image-s001',c,prompt),/custom endpoint/);assert.equal(f.provider.length,0);
  });
});

test('408/500/504 use the same bounded request, never another account',async()=>{
  for(const status of [408,500,504])await fixture(async f=>{
    f.response=async(_:any,attempt:number)=>attempt===1?new Response('{}',{status}):result();
    await image(env,id,'image-s001',cfg(['first-image-key','second-image-key']),prompt);
    assert.equal(f.provider.length,2);assert.deepEqual(f.provider[0],f.provider[1]);
    assert.ok(!f.actions.some((r:any)=>r.p_action==='call_rekey'));
  });
});

test('Retry-After supports seconds/date, clamps to thirty seconds, otherwise waits two/four seconds',()=>{
  const now=Date.parse('2026-10-02T12:00:00Z');
  assert.equal(imageRetryDelay(0,'999',now),30000);assert.equal(imageRetryDelay(0,'0',now),0);
  assert.equal(imageRetryDelay(0,'Fri, 02 Oct 2026 12:00:12 GMT',now),12000);
  assert.equal(imageRetryDelay(0,'invalid',now),2000);assert.equal(imageRetryDelay(1,null,now),4000);
});

test('three-image batch worst-case successful retry path stays at forty-five external requests',async()=>fixture(async f=>{
  const attempts=new Map<string,number>();f.response=async(sent:any)=>{
    const n=(attempts.get(sent.body)||0)+1;attempts.set(sent.body,n);
    return n<3?new Response('{}',{status:503}):result();
  };
  // Each image waits once before each grant: six shared-budget checks total.
  const perImagePermits=new Map<string,number>();
  f.permit=(p:any)=>{const n=(perImagePermits.get(p.key)||0)+1;perImagePermits.set(p.key,n);return {wait_ms:n%2?100:0};};
  const shots=[1,2,3].map(i=>({shot_id:'s00'+i,prompt:prompt+' scene '+i}));
  const value=await generateImageBatch(env,id,shots,{steps:{}},cfg());
  assert.equal(value.length,3);assert.equal(f.provider.length,9);
  assert.equal(f.actions.filter((r:any)=>r.p_action==='call_start').length,3);
  assert.equal(f.actions.filter((r:any)=>r.p_action==='image_permit').length,18);
  assert.equal(f.actions.filter((r:any)=>r.p_action==='call_finish').length,3);
  assert.equal(f.actions.filter((r:any)=>r.p_action==='save').length,3);
  assert.equal(f.externals,42); // + status/config/final batch checkpoint =45.
}));

test('image-batch execution overrides caller retries to zero; full child fits forty-five externals',async()=>fixture(async f=>{
  const shots=[1,2,3].map(i=>({shot_id:'s00'+i,prompt:prompt+' scene '+i}));
  const attempts=new Map<string,number>();f.response=async(sent:any)=>{const n=(attempts.get(sent.body)||0)+1;attempts.set(sent.body,n);return n<3?new Response('{}',{status:503}):result();};
  const perImagePermits=new Map<string,number>();
  f.permit=(p:any)=>{const n=(perImagePermits.get(p.key)||0)+1;perImagePermits.set(p.key,n);return {wait_ms:n%2?100:0};};
  let limit=-1;
  const step={do:async(name:string,...args:any[])=>{if(name==='execute')limit=args[0].retries.limit;return args.at(-1)();}};
  await new GenerationStageWorkflow({},env).run({payload:{videoId:id,name:'image-batch-s001-s002-s003',op:'image-batch',data:{shots},retries:9}},step);
  assert.equal(limit,0);assert.equal(f.externals,45);
}));

test('SQL reserves exact metadata atomically, returns it NEW, guards rekey and preserves settlement metadata',()=>{
  const sql=readFileSync(new NodeURL('../sql/002_generation_rpc.sql',import.meta.url),'utf8');
  assert.match(sql,/jsonb_build_object\('image_request',opts\),now\(\)\)\s+RETURNING to_jsonb\(provider_calls\) INTO data/);
  assert.match(sql,/RETURN data\|\|'.*"new":true/);
  assert.match(sql,/jsonb_build_object\('started_at',now\(\),'auth_rotations',0\)/);
  assert.match(sql,/expected_fingerprint/);assert.match(sql,/created_at>now\(\)-interval '15 minutes'/);
  assert.match(sql,/jsonb_build_object\('asset',p_payload->'value','provider_response'/);
});
