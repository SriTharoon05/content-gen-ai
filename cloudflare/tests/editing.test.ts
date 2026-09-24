import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';

const compiled=await build({stdin:{contents:"export * from './src/editing';",resolveDir:process.cwd()},bundle:true,platform:'node',format:'esm',write:false,
  plugins:[{name:'workflow-runtime',setup(b){b.onResolve({filter:/^cloudflare:workers$/},()=>({path:'stub',namespace:'test'}));b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:'export class WorkflowEntrypoint {constructor(ctx,env){this.env=env;}}',loader:'js'}));}}]});
const {validateEdit,cropBounds,buildEditManifest,EditingWorkflow,handleEditing}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const asset={url:'https://res.cloudinary.com/test/video/upload/voice.wav',sha256:'a'.repeat(64)};
const snapshot=()=>({manifest:{version:1,operation:'assemble_script',files:{'narration.wav':asset,'images/1.png':asset},images:['images/1.png'],script:{beats:[{}]},words:[{word:'hello',start:0,end:1}],settings:{video:{width:720,height:1280,fps:30},music:{max_intensity:.25}},transitions:[],music:null},output:asset.url,duration:60});
test('editing validates revisions speed volume crop and strips unrelated fields',()=>{
  assert.deepEqual(validateEdit({operation:'rerender',revision:0,secrets:'discard'}),{operation:'rerender',revision:0});
  for(const x of [{operation:'rerender'},{operation:'speed',revision:0,playback_rate:2},{operation:'bgm',revision:0,music_volume_pct:101},{operation:'bgm',revision:0,music_start_seconds:-1}])assert.throws(()=>validateEdit(x));
  assert.deepEqual(cropBounds(20,2,8),[2,8]);assert.throws(()=>cropBounds(20,19,20));assert.throws(()=>cropBounds(20,0,21));
});
test('rerender reuses exact saved assets without mutation or generation',()=>{
  const s=snapshot();const m=buildEditManifest(s,{operation:'rerender',revision:0});
  assert.deepEqual(m.files,s.manifest.files);assert.deepEqual(m.images,s.manifest.images);
  m.words[0].word='changed';assert.equal(s.manifest.words[0].word,'hello');
});
test('legacy captions and Supabase asset references rerender without copying or new ASR',()=>{
  const s:any=snapshot();delete s.manifest.script;delete s.manifest.words;
  s.manifest.files['captions.ass']={key:'render-tasks/old/captions.ass',sha256:'b'.repeat(64)};
  const m=buildEditManifest(s,{operation:'rerender',revision:0});assert.equal(m.operation,'assemble');
  assert.equal(m.files['captions.ass'].key,'render-tasks/old/captions.ass');
  assert.throws(()=>buildEditManifest(s,{operation:'speed',revision:0,playback_rate:.9}),/legacy speed/);
});
test('BGM old output without checksum reassembles; checksummed output uses canonical remix',()=>{
  const s:any=snapshot();s.music={path:asset.url,sha256:asset.sha256,duration_seconds:20,trim_start:2,trim_end:10,default_volume_pct:30};
  const input={operation:'bgm',revision:0,music_track:'b'.repeat(32),music_volume_pct:20};
  const rebuilt=buildEditManifest(s,input);assert.equal(rebuilt.operation,'assemble_script');assert.equal(rebuilt.intensity,.05);assert.equal(rebuilt.music_start,2);assert.equal(rebuilt.music_end,10);
  s.output_sha256='c'.repeat(64);const remixed=buildEditManifest(s,input);assert.equal(remixed.operation,'remix');assert.equal(remixed.files['source.mp4'].sha256,s.output_sha256);
  const removed=buildEditManifest(s,{operation:'bgm',revision:0,music_track:null});assert.equal(removed.music,null);assert.equal(removed.intensity,0);
});
test('rerender Workflow only calls DB/media binding, no generation provider',async()=>{
  const original=globalThis.fetch;const calls:any[]=[];const created:any[]=[];
  const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_KEY:'fake',CLOUDINARY_CLOUD_NAME:'test',MEDIA_WORKFLOW:{create:async(x:any)=>created.push(x)}};
  globalThis.fetch=async(url,init)=>{
    assert.match(String(url),/test.supabase.co\/rest\/v1\/rpc\//);const body=JSON.parse(String(init?.body));calls.push(body.p_action);
    if(body.p_action==='claim')return Response.json({status:'running',video_id:'b'.repeat(32),revision:1,input:{operation:'rerender',revision:0},snapshot:snapshot()});
    if(body.p_action==='load')return Response.json({status:'succeeded',result_json:{url:asset.url,duration:60}});
    return Response.json({ok:true});
  };
  try {
    const step={do:async(_name:string,...args:any[])=>args.at(-1)(),waitForEvent:async()=>{throw Error('must not wait');}};
    const result=await new EditingWorkflow({},env).run({instanceId:'a'.repeat(32),payload:{editId:'a'.repeat(32)}},step);
    assert.equal(result.status,'succeeded');assert.deepEqual(calls,['claim','create','expire','load','complete']);assert.equal(created.length,1);assert.equal(created[0].params.notifyEditing,'a'.repeat(32));
  } finally {globalThis.fetch=original;}
});
test('upload registration rejects missing rights before network',async()=>{
  await assert.rejects(handleEditing(new Request('https://worker/api/music',{method:'POST',body:JSON.stringify({sha256:'a'.repeat(64),name:'test',category:'drama'})}),{}),/rights/);
});
test('speed edit checkpoints a fresh Workflow instance before ASR and final render',async()=>{
  const original=globalThis.fetch;const created:any[]=[];const continuations:any[]=[];
  const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_KEY:'fake',CLOUDINARY_CLOUD_NAME:'test',
    MEDIA_WORKFLOW:{create:async(x:any)=>created.push(x)},EDITING_WORKFLOW:{create:async(x:any)=>continuations.push(x)}};
  globalThis.fetch=async(url,init)=>{
    assert.match(String(url),/test.supabase.co/);const body=JSON.parse(String(init?.body));
    if(body.p_action==='claim')return Response.json({status:'running',video_id:'b'.repeat(32),revision:1,input:{operation:'speed',revision:0,playback_rate:.9},snapshot:snapshot()});
    if(body.p_action==='load')return Response.json({status:'succeeded',result_json:{url:asset.url,duration:66,metrics:{sha256:'c'.repeat(64)}}});
    return Response.json({ok:true});
  };
  try {
    const step={do:async(_name:string,...args:any[])=>args.at(-1)(),waitForEvent:async()=>{throw Error('must not wait');}};
    const result=await new EditingWorkflow({},env).run({instanceId:'a'.repeat(32),payload:{editId:'a'.repeat(32)}},step);
    assert.equal(result.status,'continued');assert.equal(created.length,1);assert.equal(continuations.length,1);
    assert.equal(continuations[0].id,'a'.repeat(32)+'-render');assert.equal(continuations[0].params.prepared.sha256,'c'.repeat(64));
  }finally{globalThis.fetch=original;}
});
test('speed continuation aligns new audio, checkpoints then reuses image/script inputs',async()=>{
  const original=globalThis.fetch;const created:any[]=[];const continuations:any[]=[];let asr=0;let submitted:any;
  const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_KEY:'fake',CLOUDINARY_CLOUD_NAME:'test',MEDIA_WORKFLOW:{create:async(x:any)=>created.push(x)},EDITING_WORKFLOW:{create:async(x:any)=>continuations.push(x)}};
  globalThis.fetch=async(url,init)=>{
    if(String(url).includes('api.groq.com')){asr++;return Response.json({words:Array.from({length:25},()=>({word:'test',start:0,end:1}))});}
    if(String(url)===asset.url)return new Response(new Blob(['fixture audio']));
    assert.match(String(url),/test.supabase.co/);const body=JSON.parse(String(init?.body));
    if(body.p_action==='claim')return Response.json({status:'running',video_id:'b'.repeat(32),revision:1,input:{operation:'speed',revision:0,playback_rate:.9},snapshot:snapshot()});
    if(body.p_action==='config')return Response.json({channel:{strategy_json:{},overrides_json:{}},settings:{keys:{groq:['fake-key']}}});
    if(body.p_action==='create'){submitted=body.p_payload.manifest;return Response.json({ok:true});}
    if(body.p_action==='load')return Response.json({status:'succeeded',result_json:{url:asset.url,duration:66,metrics:{sha256:'d'.repeat(64)}}});
    return Response.json({ok:true});
  };
  try{
    const step={do:async(_name:string,...args:any[])=>args.at(-1)(),waitForEvent:async()=>{throw Error('must not wait');}};
    const result=await new EditingWorkflow({},env).run({instanceId:'a'.repeat(32)+'-render',payload:{editId:'a'.repeat(32),prepared:{url:asset.url,duration:66,sha256:'c'.repeat(64)}}},step);
    assert.equal(result.status,'continued');assert.equal(asr,1);assert.equal(created.length,0);
    const final=await new EditingWorkflow({},env).run({instanceId:continuations[0].id,payload:continuations[0].params},step);
    assert.equal(final.status,'succeeded');assert.equal(asr,1);assert.deepEqual(submitted.images,snapshot().manifest.images);
    assert.equal(submitted.files['narration.wav'].sha256,'c'.repeat(64));assert.equal(submitted.words.length,25);assert.equal(created.length,1);
  }finally{globalThis.fetch=original;}
});
