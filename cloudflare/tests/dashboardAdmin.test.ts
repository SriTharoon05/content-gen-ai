import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {URL as NodeURL} from 'node:url';
import {validateChannel,validateSettings,publicSettings,channelView,handleDashboardAdmin,readAdminBody} from '../src/dashboardAdmin';
import contract from '../src/contract.json';
import type {Env} from '../src/types';

test('channel create/edit validates known fields without restricting stored language/conversation',()=>{
  assert.equal(validateChannel({slug:'new-channel',name:' New ',niche:'Science',conversation:true},true).name,'New');
  assert.deepEqual(validateChannel({overrides:{primary_language:'ta',publishing_mode:'review'}}).overrides,{primary_language:'ta',publishing_mode:'review'});
  for(const input of [{slug:'bad/slug',name:'x',niche:'x'},{slug:'valid-slug',name:'',niche:'x'},{slug:'valid-slug',name:'x',niche:'x',keys:{secret:'x'}}])assert.throws(()=>validateChannel(input,true));
  for(const input of [{enabled:'false'},{overrides:{speech_tempo:20}},{overrides:{primary_language:'invalid'}},{topic_seeds:[3]}])assert.throws(()=>validateChannel(input));
});
test('capabilities are explicit and default instructions are populated',()=>{
  const v=channelView({slug:'lorehush',strategy:{},overrides:{}});assert.match(v.instructions,/LoreHush/);assert.equal(v.supported,true);
  assert.equal(channelView({slug:'x',strategy:{conversation:true},overrides:{primary_language:'ta'}}).supported,true);
  assert.equal(channelView({slug:'x',strategy:{conversation:true},overrides:{languages:['ta']}}).supported,false);
  assert.equal(channelView({slug:'x',strategy:{instructions:'My own'},overrides:{primary_language:'ta'}}).instructions,'My own');
});
test('channel reads omit unknown fields and malformed nested values',()=>{
  const view=channelView({slug:'test',token:'leaked',strategy:{instructions:'safe',keys:'leaked',audience:{secret:'leaked'}},overrides:{primary_language:'en',secret:'leaked',voice:{token:'leaked'},languages:[{token:'leaked'}]}});
  assert.doesNotMatch(JSON.stringify(view),/leaked/);assert.deepEqual(view.overrides,{primary_language:'en'});
});
test('streaming body limit cancels chunked overflow without trusting Content-Length',async()=>{
  let cancelled=false;const stream=new ReadableStream<Uint8Array>({start(c){c.enqueue(new Uint8Array(100));c.enqueue(new Uint8Array(100));},cancel(){cancelled=true;}});
  const request=new Request('https://worker/api/settings',{method:'PUT',body:stream,duplex:'half'} as any);
  await assert.rejects(readAdminBody(request,128),(e:any)=>e.status===413);assert.equal(cancelled,true);
  const valid=new Request('https://worker/api/settings',{method:'PUT',body:'{"ok":true}'});
  assert.deepEqual(await readAdminBody(valid),{ok:true});
  await assert.rejects(readAdminBody(new Request('https://worker/api/settings',{method:'PUT',body:'bad'})),(e:any)=>e.status===400);
  await assert.rejects(readAdminBody(new Request('https://worker/api/settings',{method:'PUT',body:'{"x":"தமிழ்"}'}),15),(e:any)=>e.status===413);
});
test('runtime credentials and unknown root/section secrets are not exposed',()=>{
  const current={...contract.defaults,private_secret:'never-return',models:{...contract.defaults.models,api_key:'never-return'},keys:{groq:['raw-secret-1','raw-secret-2'],gemini_audio_paid:'raw-secret-paid'}};
  const view=publicSettings(current);assert.doesNotMatch(JSON.stringify(view),/raw-secret|never-return/);
  assert.deepEqual(view.keys.groq,['__keep__:0','__keep__:1']);assert.equal(view.keys.gemini_audio_paid,'__keep__');
});
test('partial credential writes preserve unspecified fields and reject unknowns',()=>{
  const c=publicSettings(contract.defaults);
  assert.deepEqual(validateSettings({voice:{speech_tempo:1.1}},c),{voice:{speech_tempo:1.1}});
  assert.deepEqual(validateSettings({keys:{groq:['__keep__:0','new-key'],gemini_audio_paid:null}},c),{keys:{groq:['__keep__:0','new-key']}});
  assert.throws(()=>validateSettings({keys:{admin_token:'x'}},c));
  assert.throws(()=>validateSettings({models:{image_endpoint:'https://evil.example'}},c));
  assert.throws(()=>validateSettings({voice:{speech_tempo:9}},c));
  assert.throws(()=>validateSettings({video:{fps:60}},c));
  assert.throws(()=>validateSettings({schedule:{enabled:true}},c));
  assert.throws(()=>validateSettings(JSON.parse('{"__proto__":{"polluted":true}}'),c));
});
test('admin routes use service-role RPC, never reflect write-only credentials',async()=>{
  const old=globalThis.fetch;const calls:any[]=[];
  globalThis.fetch=async(_url:any,init:any)=>{const p=JSON.parse(init.body);calls.push(p);return Response.json({settings:{...contract.defaults,keys:{groq:['__keep__:0']}},revision:'v1'});};
  try{
    const env={SUPABASE_URL:'https://db.example',SUPABASE_KEY:'service'} as Env;
    const response=await handleDashboardAdmin(new Request('https://worker/api/settings',{method:'PUT',body:JSON.stringify({settings:{keys:{groq:['new-secret']}}})}),env);
    assert.equal(response?.status,200);assert.doesNotMatch(await response!.text(),/new-secret/);
    assert.equal(calls[1].p_payload.patch.keys.groq[0],'new-secret');assert.equal(calls[1].p_payload.revision,'v1');
    assert.equal(await handleDashboardAdmin(new Request('https://worker/api/channels/lorehush/run'),env),null);
  }finally{globalThis.fetch=old;}
});
test('SQL enforces ACL, transaction lock, redaction, optimistic concurrency and no dynamic SQL',()=>{
  const sql=readFileSync(new NodeURL('../sql/007_admin_rpc.sql',import.meta.url),'utf8');
  assert.match(sql,/FROM PUBLIC,anon,authenticated/);assert.match(sql,/TO service_role/);
  assert.match(sql,/pg_advisory_xact_lock/);assert.match(sql,/IS DISTINCT FROM revision/);
  assert.match(sql,/current_config-'keys'/);assert.match(sql,/Unknown credential placeholder/);
  assert.doesNotMatch(sql,/EXECUTE\s+(?:format|p_payload|')/i);
  assert.match(sql,/foreign_key_violation/);
});
