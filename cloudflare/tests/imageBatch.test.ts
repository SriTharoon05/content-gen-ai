import test from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {enforceFactualReview} from '../src/factualReview';
const compiled=await build({stdin:{contents:"export {generateImageBatch,imageBatchConcurrency} from './src/stages';export {validateSchema} from './src/generation';",resolveDir:process.cwd()},bundle:true,platform:'node',format:'esm',write:false,plugins:[{name:'fake-workflows',setup(b){b.onResolve({filter:/^cloudflare:workers$/},()=>({path:'stub',namespace:'test'}));b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:'export class WorkflowEntrypoint {}',loader:'js'}));}}]});
const {generateImageBatch,imageBatchConcurrency,validateSchema}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const env={SUPABASE_URL:'https://database.test',SUPABASE_KEY:'fixture'};
const id='a'.repeat(32);
const asset={url:'https://res.cloudinary.com/test/image/upload/a.png',sha256:'a'.repeat(64)};
const shots=[1,2,3].map(i=>({shot_id:'s'+String(i).padStart(3,'0'),prompt:'Fresh scene '+i}));
test('three-image child returns stable scene order, canonical ledger keys and bounded provider options',async()=>{
  const original=globalThis.fetch;const writes:string[]=[];const requests:string[]=[];let active=0,peak=0;
  globalThis.fetch=async(_url,init)=>{writes.push(JSON.parse(String(init?.body)).p_payload.key);return Response.json({ok:true});};
  try{
    const result=await generateImageBatch(env,id,shots,{steps:{}},{},async(_env:any,_id:string,key:string,_c:any,prompt:string,options:any)=>{
      requests.push(key);assert.equal(prompt,shots[requests.length-1].prompt);assert.deepEqual(options,{maxPermitChecks:6,maxKeys:1});
      active++;peak=Math.max(peak,active);await new Promise(resolve=>setTimeout(resolve,key==='image-s001'?15:1));active--;return asset;
    });
    assert.equal(peak,3);assert.equal(active,0);assert.deepEqual(requests,['image-s001','image-s002','image-s003']);
    assert.deepEqual(result.map((x:any)=>x.shot_id),['s001','s002','s003']);assert.deepEqual([...writes].sort(),requests);
  }finally{globalThis.fetch=original;}
});
test('batch profile honors owner limits and lowers concurrency when multiple image keys are configured',()=>{
  assert.equal(imageBatchConcurrency({settings:{keys:{pollinations:['one']}}}),3);
  assert.equal(imageBatchConcurrency({settings:{runtime:{image_concurrency:3},keys:{pollinations:['one','two','three','four']}}}),2);
  assert.equal(imageBatchConcurrency({settings:{runtime:{image_concurrency:2},keys:{pollinations:['one']}}}),2);
  assert.equal(imageBatchConcurrency({settings:{runtime:{image_concurrency:1},keys:{pollinations:['one','two']}}}),1);
});
test('image batch drains and checkpoints paid siblings before surfacing a failed image',async()=>{
  const original=globalThis.fetch;const finished:string[]=[];const saved:string[]=[];
  globalThis.fetch=async(_url,init)=>{saved.push(JSON.parse(String(init?.body)).p_payload.key);return Response.json({ok:true});};
  try{
    await assert.rejects(generateImageBatch(env,id,shots,{steps:{}},{},async(_env:any,_id:string,key:string)=>{
      if(key==='image-s001')throw new Error('Image generation HTTP 503');
      await new Promise(resolve=>setTimeout(resolve,10));finished.push(key);return asset;
    }),/image-s001: Image generation HTTP 503/);
    assert.deepEqual(finished.sort(),['image-s002','image-s003']);assert.deepEqual(saved.sort(),finished);
  }finally{globalThis.fetch=original;}
});
test('old standalone checkpoints and settled ledger assets recover without a provider purchase',async()=>{
  const original=globalThis.fetch;const actions:string[]=[];
  globalThis.fetch=async(url,init)=>{
    assert.ok(String(url).startsWith('https://database.test/'));
    const p=JSON.parse(String(init?.body));actions.push(p.p_action+':'+p.p_payload.key);
    if(p.p_action==='call_start')return Response.json({status:'settled',detail_json:asset});
    if(p.p_action==='save')return Response.json({ok:true});
    throw new Error('Unexpected request');
  };
  try{
    const c={settings:{models:{image_model:'test',image_catalog:[{id:'test',credits:.0001}]}}};
    const result=await generateImageBatch(env,id,shots.slice(0,2),{steps:{'image-s001':asset}},c);
    assert.deepEqual(result.map((x:any)=>x.asset),[asset,asset]);
    assert.deepEqual(actions,['call_start:image-s002','save:image-s002']);
  }finally{globalThis.fetch=original;}
});
test('batch bounds reject a fourth image and duplicate scene IDs before any request',async()=>{
  await assert.rejects(generateImageBatch(env,id,[...shots,{shot_id:'s004',prompt:'extra'}],{steps:{}},{}),/one to three/);
  await assert.rejects(generateImageBatch(env,id,[shots[0],shots[0]],{steps:{}},{}),/distinct scene IDs/);
});
test('schema repair errors identify nested ref, array position and concrete string bounds',()=>{
  const schema={type:'object',required:['beats'],properties:{beats:{type:'array',minItems:1,maxItems:20,items:{$ref:'#/$defs/Beat'}}},$defs:{Beat:{type:'object',required:['narration'],properties:{narration:{type:'string',minLength:5,maxLength:10}}}}};
  const beats=Array.from({length:17},()=>({narration:'valid'}));beats[16].narration='four';
  assert.throws(()=>validateSchema({beats},schema),/\$\.beats\[16\]\.narration: String length 4; expected 5–10 characters/);
  assert.throws(()=>validateSchema({beats:[{}]},schema),/\$\.beats\[0\]\.narration: Required field is missing/);
  assert.throws(()=>validateSchema({beats:[]},schema),/\$\.beats: Array length 0/);
  assert.throws(()=>validateSchema('incorrect',{type:'string',pattern:'^s[0-9]{3}$'},undefined,'shot_id'),/shot_id: String does not match required pattern/);
});
test('writer-flagged unsupported claims override a positive model review with actionable repair findings',()=>{
  const qa={passed:true,made_for_kids:false,findings:['Existing quality finding'],reasoning:'Model approved'};
  const corrected=enforceFactualReview({flagged_claims:['Device feeds memories directly into the cortex today','Laser phosphor cuts latency by half']},qa);
  assert.equal(corrected.passed,false);assert.equal(qa.passed,true);assert.equal(corrected.findings.length,3);
  assert.match(corrected.findings[1],/not-currently-available thought experiment/);assert.match(corrected.findings[2],/Do not invent.*quantitative comparisons/);
});
test('a script without unresolved flags preserves its original review outcome',()=>{
  const qa={passed:true,findings:[]};assert.equal(enforceFactualReview({flagged_claims:[]},qa),qa);
  const rejected={passed:false,findings:['Weak hook']};assert.equal(enforceFactualReview({},rejected),rejected);
});
