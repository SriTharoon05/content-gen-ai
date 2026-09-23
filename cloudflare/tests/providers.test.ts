import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseJSON,speechChunks,pcmWav,textModel,merge} from '../src/providers';
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
