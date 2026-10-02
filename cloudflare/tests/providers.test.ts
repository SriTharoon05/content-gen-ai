import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseJSON,speechChunks,pcmWav,textModel,merge,image as generateImage,geminiSpeech} from '../src/providers';
import type {Env} from '../src/types';
test('JSON rejects trailing content but accepts a single fenced object',()=>{
  assert.deepEqual(parseJSON('```json\n{"a":1}\n```'),{a:1});
  assert.throws(()=>parseJSON('{"a":1} trailing'));
});
test('Orpheus chunks never exceed documented 200 characters',()=>{
  const s=('A natural sentence with enough detail. ').repeat(50);
  const chunks=speechChunks(s);assert.ok(chunks.every(x=>x.length<=200));assert.equal(chunks.join(' '),s.trim());
});
test('Gemini PCM uses correct mono 24kHz WAV header',()=>{
  const wav=pcmWav(new Uint8Array(48000));assert.equal(wav.length,48044);
  assert.equal(wav.readUInt32LE(24),24000);assert.equal(wav.readUInt32LE(40),48000);
});

test('selected Groq speech avoids unnecessary Gemini calls',async()=>{
  const original=globalThis.fetch;globalThis.fetch=async()=>{throw new Error('No request expected');};
  try{assert.equal(await geminiSpeech({} as Env,{settings:{models:{tts:'canopylabs/orpheus-v1-english'}}},'hello','warm'),null);}
  finally{globalThis.fetch=original;}
});

test('legacy breathy narrator uses clear voice and selected Gemini model',async()=>{
  const original=globalThis.fetch;let sent:any,url='';
  globalThis.fetch=async(u,init)=>{url=String(u);sent=JSON.parse(String(init?.body));return Response.json({candidates:[]});};
  try{
    await geminiSpeech({} as Env,{channel:{strategy_json:{voice_name:'Enceladus'}},settings:{models:{tts:'gemini-2.5-flash-preview-tts'},voice:{},keys:{gemini_free:['fixture']}}},'Exact words.','clear');
    assert.match(url,/gemini-2.5-flash-preview-tts/);
    assert.equal(sent.generationConfig.speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName,'Puck');
    assert.match(sent.contents[0].parts[0].text,/never husky/);
  }finally{globalThis.fetch=original;}
});
test('settings preserve defaults while applying nested owner values',()=>{
  assert.deepEqual(merge({a:{b:1,c:2}},{a:{b:3}}),{a:{b:3,c:2}});
});
test('429 on one key immediately advances independently to next and falls back to Groq',async()=>{
  const original=globalThis.fetch;const seen:string[]=[];
  globalThis.fetch=async(_url,init)=>{
    const h=new Headers(init?.headers);const k=h.get('x-goog-api-key')||h.get('Authorization')||'';seen.push(k);
    if(k!=='Bearer groq2')return new Response('{}',{status:429});
    return Response.json({choices:[{message:{content:'{"ok":true}'}}]});
  };
  try {
    assert.deepEqual(await textModel({settings:{keys:{gemini_free:['g1','g2'],groq:['groq1','groq2'],gemini_audio_paid:'never-use'}}},'test',{}),{ok:true});
    assert.deepEqual(seen,['g1','g2','Bearer groq1','Bearer groq2']);
  } finally{globalThis.fetch=original;}
});

test('settled image retry returns checkpoint without purchasing again',async()=>{
  const original=globalThis.fetch;let requests=0;
  const asset={url:'https://res.cloudinary.com/test/image/upload/a.png',sha256:'a'.repeat(64)};
  globalThis.fetch=async()=>{requests++;return Response.json({status:'settled',detail_json:asset});};
  try {
    const c={settings:{models:{image_model:'fixture',image_catalog:[{id:'fixture',credits:.002}]}}};
    assert.deepEqual(await generateImage({SUPABASE_URL:'https://test.supabase.co',SUPABASE_KEY:'fake'} as Env,'a'.repeat(32),'image-s001',c,'test'),asset);
    assert.equal(requests,1);
  } finally{globalThis.fetch=original;}
});

test('uncertain image reservation refuses automatic duplicate billing',async()=>{
  const original=globalThis.fetch;let requests=0;
  globalThis.fetch=async()=>{requests++;return Response.json({status:'reserved'});};
  try {
    const c={settings:{models:{image_model:'fixture',image_catalog:[{id:'fixture',credits:.002}]}}};
    await assert.rejects(()=>generateImage({SUPABASE_URL:'https://test.supabase.co',SUPABASE_KEY:'fake'} as Env,'a'.repeat(32),'image-s001',c,'test'),/uncertain/);
    assert.equal(requests,1);
  } finally{globalThis.fetch=original;}
});
