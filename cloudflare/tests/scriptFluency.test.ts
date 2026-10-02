import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {scriptWithRepair} from '../src/scriptRepair';
import contract from '../src/contract.json';

const compiled=await build({stdin:{contents:"export {validateScript,GenerationWorkflow} from './src/generation';",resolveDir:process.cwd()},bundle:true,platform:'node',format:'esm',write:false,plugins:[{name:'workflow-fixture',setup(b){
  b.onResolve({filter:/^cloudflare:workers$/},()=>({path:'stub',namespace:'test'}));
  b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:'export class WorkflowEntrypoint {constructor(ctx,env){this.env=env;}}',loader:'js'}));
}}]});
const {validateScript,GenerationWorkflow}=await import('data:text/javascript;base64,'+Buffer.from(compiled.outputFiles[0].text).toString('base64'));
const hook='Why does your brain hide what happens when you blink?';
const explanation='Your eyes briefly close with each blink, but your experience normally feels continuous because your brain combines incoming signals with what it expects from the scene. Rather than treating each interruption as an entirely new event, that processing helps maintain a stable picture while you read a sentence or look around the room. The important point is not that your mind invents every detail, but that perception is an active process rather than a perfect recording.';
const response='Yeah, exactly. That explains why an ordinary blink feels different from deliberately switching off the lights and waiting in complete darkness. We should not pretend that one mechanism explains every part of vision, though, because attention and context also matter when your brain interprets changing signals. Once you notice that distinction, a simple blink becomes a reminder that seeing involves processing information, not just collecting snapshots.';
const badTail='So you never notice. Brain fills blackouts. Neurons fire pre-blink. They predict the next scene. Prediction matches sight. Experience stays smooth. Hidden shortcut prevents disorientation. Enables walking, reading, driving. Remember the invisible edit. Reality edited frame by frame.';
const split=(text:string,parts:number)=>{
  const words=text.trim().split(/\s+/);const chunks:string[]=[];
  for(let i=0;i<parts;i++)chunks.push(words.slice(Math.floor(i*words.length/parts),Math.floor((i+1)*words.length/parts)).join(' '));
  return chunks;
};
const script=(texts:string[],duo=false)=>({hook_kind:'question',hook,flagged_claims:[],beats:texts.map((narration,i)=>({shot_id:'s'+String(i+1).padStart(3,'0'),speaker:duo?(i<12?'Alex':'Sam'):'',narration,scene_note:'',emphasis_words:[]}))});
const badScript=()=>script([hook,...split(explanation+' This familiar stability makes ordinary surroundings feel continuous. '+badTail,24)]);
const naturalDuo=()=>script([hook,...split(explanation,11),...split(response,13)],true);

test('real robotic tail fails on joined English narration with actionable repair feedback',()=>{
  assert.throws(()=>validateScript(badScript()),(error:any)=>{
    assert.match(error.message,/\$\.beats: English fluency guard/);
    assert.match(error.message,/three consecutive sentences or telegraphic fragments/);
    assert.match(error.message,/So you never notice/);assert.match(error.message,/Brain fills blackouts/);assert.match(error.message,/Neurons fire pre-blink/);
    assert.match(error.message,/connected, substantive narration/);assert.match(error.message,/130–205/);
    return true;
  });
});
test('natural duo allows an isolated short response followed by substantive narration',()=>{
  assert.doesNotThrow(()=>validateScript(naturalDuo(),{language:'en',conversation:true}));
});
test('duo with mostly fragment-only alternating turns is rejected by the existing sentence guard',()=>{
  const confetti=['Yeah, exactly.','Brain edits vision.','We see continuity.','Signals become experience.','Yeah, right.','The scene stays stable.','Hidden gaps vanish.','So we continue.','Our attention shifts.','It seems natural.','Reality stays smooth.',"That's the secret.",'We hardly notice.','It just works.','Yes, absolutely.','The picture persists.','Everything feels connected.','You get it.'];
  const s=script([hook,...split(explanation+' This familiar stability makes ordinary surroundings feel continuous.',6),...confetti],true);
  s.beats.forEach((beat,i)=>{beat.speaker=i<7?'Alex':(i%2?'Sam':'Alex');});
  assert.equal(s.beats.length,25);
  assert.ok(s.beats.slice(7).every(b=>b.narration.split(/\s+/).length<=4));
  assert.throws(()=>validateScript(s,{language:'en',conversation:true}),/English fluency guard/);
});
test('three or more short image fragments within a fluent sentence are not short sentences',()=>{
  const words=(explanation+' '+response).split(/\s+/);
  const shortCuts=Array.from({length:5},(_,i)=>words.slice(i*3,(i+1)*3).join(' '));
  const s=script([hook,...shortCuts,...split(words.slice(15).join(' '),19)]);
  assert.ok(s.beats.slice(1,6).every(b=>b.narration.split(/\s+/).length===3));
  assert.doesNotThrow(()=>validateScript(s));
});
test('English guard permits two brief sentences but rejects three; localized validation is unchanged',()=>{
  const base=explanation+' '+response;
  assert.doesNotThrow(()=>validateScript(script([hook,...split(base+' You notice. That matters. Yet the familiar scene stays continuous as you look around the room.',24)])));
  assert.throws(()=>validateScript(script([hook,...split(base+' You notice. That matters. Look again.',24)])),/English fluency guard/);
  assert.doesNotThrow(()=>validateScript(badScript(),{language:'ta',conversation:false}));
});
test('fluency failure is persisted and supplied to the existing bounded script repair',async()=>{
  const config={channel:{strategy_json:{conversation:true}},settings:{keys:{gemini_free:['fixture'],groq:['fixture']}}};
  let saved:any;
  await assert.rejects(scriptWithRepair(config,'reserved concept',contract.schemas.Script,null,async()=>badScript(),validateScript,async v=>{saved=v;}),/validation attempt 1\/3:.*English fluency guard/);
  const repaired=await scriptWithRepair(config,'reserved concept',contract.schemas.Script,saved,async(_c,p)=>{
    assert.match(p,/three consecutive sentences or telegraphic fragments/);
    assert.match(p,/Brain fills blackouts/);
    assert.match(p,/connected, substantive narration/);
    return naturalDuo();
  },s=>validateScript(s,{language:'en',conversation:true}),async()=>{throw new Error('Valid revision must not fail');});
  assert.deepEqual(repaired,naturalDuo());assert.equal(saved.attempts,1);
});
test('cached robotic script also stops coordinator before QA, TTS or image purchases',async()=>{
  const original=globalThis.fetch;const actions:string[]=[];let children=0;let recordedError='';
  globalThis.fetch=async(_url,init)=>{
    const p=JSON.parse(String(init?.body));actions.push(p.p_action);
    if(p.p_action==='status')return Response.json({steps:{profile:{language:'en',conversation:false,options:{}},'default-bgm':{music:null},concepts:{},concept:{},premise:{},script:badScript()}});
    if(p.p_action==='fail'){recordedError=p.p_payload.error;return Response.json({ok:true});}
    throw new Error('No provider, approved-script or media mutation expected');
  };
  const env={SUPABASE_URL:'https://database.test',SUPABASE_KEY:'fixture',GENERATION_STAGE:{create:async()=>{children++;throw new Error('No new stages');}}};
  const step={do:async(_name:string,...args:any[])=>args.at(-1)(),sleep:async()=>{throw new Error('No TTS');},waitForEvent:async()=>{throw new Error('No provider');}};
  try{
    await assert.rejects(new GenerationWorkflow({},env).run({instanceId:'fixture',payload:{videoId:'a'.repeat(32)}},step),/English fluency guard/);
    assert.match(recordedError,/English fluency guard/);
    assert.equal(children,0);assert.deepEqual(actions,['status','fail']);
  }finally{globalThis.fetch=original;}
});
