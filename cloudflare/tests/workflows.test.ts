import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';

const compiled=await build({stdin:{contents:"export {runStage} from './src/stages'; export {GenerationWorkflow} from './src/generation';",resolveDir:process.cwd()},bundle:true,platform:'node',format:'esm',write:false,
  plugins:[{name:'workflow-test-runtime',setup(b){
    b.onResolve({filter:/^cloudflare:workers$/},()=>({path:'stub',namespace:'test'}));
    b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:'export class WorkflowEntrypoint { constructor(ctx,env){this.env=env;this.ctx=ctx;} }',loader:'js'}));
  }}]});
const {runStage,GenerationWorkflow}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const step=()=>({do:async(_name:string,...args:any[])=>args.at(-1)(),sleep:async()=>{},waitForEvent:async()=>({payload:{ok:true,value:{url:'https://res.cloudinary.com/test/image/upload/a.png',sha256:'a'.repeat(64)}}})});

test('stage completion uses an event, not binding-status polling',async()=>{
  let creates=0;
  const env={GENERATION_STAGE:{create:async()=>{creates++;},get:async()=>{throw new Error('Unexpected polling');}}};
  const value=await runStage(env,step(),'fixture',{videoId:'a'.repeat(32),name:'image-s001',op:'image',data:{},retries:0,parentId:'parent'});
  assert.equal(creates,1);assert.equal(value.sha256,'a'.repeat(64));
});

test('coordinator checkpoints exactly eight new stages then continues durably',async()=>{
  const beats=Array.from({length:20},(_,i)=>({shot_id:'s'+String(i+1).padStart(3,'0'),speaker:'',narration:'Could you imagine how this secret survived forever?',emphasis_words:[]}));
  const saved:any={concepts:{candidates:[]},concept:{},premise:{},script:{hook_kind:'question',hook:beats[0].narration,beats},qa:{passed:true},voice:{multi_speaker:false,directors_notes:'Warm'},'gemini-audio':[{url:'https://res.cloudinary.com/test/video/upload/new.wav',sha256:'b'.repeat(64)}],
    'render-settings':{video:{width:720,height:1280,fps:30}},'audio-task':'c'.repeat(32),'audio-ready':{duration:60,url:'https://res.cloudinary.com/test/video/upload/new.wav',sha256:'b'.repeat(64)},words:[],
    visuals:{style_block:'period realism',shots:beats.map(b=>({shot_id:b.shot_id,image_prompt:'A new contextual portrait scene.'}))},edit:{transitions:beats.map(b=>({shot_id:b.shot_id,kind:'zoom_out',duration_ms:300}))}};
  const original=globalThis.fetch;let external=0;const children:any[]=[];const continuations:any[]=[];
  globalThis.fetch=async(_url,init)=>{
    external++;const p=JSON.parse(String(init?.body));
    if(p.p_action==='status')return Response.json({state:'CF_GENERATING',steps:saved});
    if(p.p_action==='save')return Response.json(p.p_payload.value);
    if(p.p_action==='load')return Response.json({status:'succeeded',result_json:{duration:60}});
    throw new Error('Unexpected database mutation '+p.p_action);
  };
  const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_KEY:'fake',
    GENERATION_STAGE:{create:async(p:any)=>{children.push(p);}},
    GENERATION_WORKFLOW:{create:async(p:any)=>{continuations.push(p);}},
    MEDIA_WORKFLOW:{get:async()=>({status:async()=>({status:'complete'})})}};
  try {
    let active=0,peak=0;
    const runner=step();const wait=runner.waitForEvent;
    runner.waitForEvent=async()=>{
      active++;peak=Math.max(peak,active);
      await new Promise(resolve=>setTimeout(resolve,10));
      try{return await wait();}finally{active--;}
    };
    const result=await new GenerationWorkflow({},env).run({instanceId:'fixture-run',payload:{videoId:'a'.repeat(32)}},runner);
    assert.equal(peak,3);assert.equal(active,0);
    assert.equal(result.status,'continued');assert.equal(children.length,8);assert.equal(continuations.length,1);
    assert.equal(continuations[0].params.segment,1);assert.ok(external<10);
    assert.ok(children.every(c=>c.params.op==='image'));
  } finally{globalThis.fetch=original;}
});
