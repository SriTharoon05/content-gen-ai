import {test} from 'node:test';
import assert from 'node:assert/strict';
import {rpc,createTask,claim,finish} from '../src/db';
import type {Env,Manifest} from '../src/types';
const env={SUPABASE_URL:'https://test.supabase.co',SUPABASE_KEY:'test-secret'} as Env;

test('database uses HTTPS RPC and sends only bound data',async()=>{
  const original=globalThis.fetch;let calls:any[]=[];
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response('{"id":"test"}');};
  try {
    await createTask(env,'a'.repeat(32),'b'.repeat(32),{version:1} as Manifest);
    assert.equal(calls[0].url,'https://test.supabase.co/rest/v1/rpc/cf_media_task');
    assert.equal(JSON.parse(calls[0].options.body).p_action,'create');
    assert.equal(calls[0].options.headers.apikey,'test-secret');
  } finally {globalThis.fetch=original;}
});
test('database provider error body never exposes secrets',async()=>{
  const original=globalThis.fetch;const log=console.error;console.error=()=>{};
  globalThis.fetch=async()=>new Response('{"code":"42501","message":"test-secret"}',{status:403});
  try {await assert.rejects(()=>rpc(env,'load','a'.repeat(32)),error=>{
    assert.ok(String(error).includes('42501'));assert.ok(!String(error).includes('test-secret'));return true;
  });} finally {globalThis.fetch=original;console.error=log;}
});
test('unknown task is a 404, not a connection failure',async()=>{
  const original=globalThis.fetch;
  globalThis.fetch=async()=>new Response('{"error":"Unknown Cloudflare task","status":404}');
  try {await assert.rejects(()=>rpc(env,'load'),(error:any)=>error.status===404);}
  finally {globalThis.fetch=original;}
});

test('failed task row is readable and not mistaken for an RPC error envelope',async()=>{
  const original=globalThis.fetch;
  globalThis.fetch=async()=>Response.json({id:'a'.repeat(32),status:'failed',error:'Media failed'});
  try {assert.equal((await rpc(env,'load')).status,'failed');}
  finally {globalThis.fetch=original;}
});
test('claim duplicate and completion wrappers preserve ownership',async()=>{
  const original=globalThis.fetch;let payload:any;
  globalThis.fetch=async(_url,options)=>{payload=JSON.parse(String(options?.body));return new Response('{}');};
  try {
    assert.equal(await claim(env,'a'.repeat(32),'b'.repeat(64)),undefined);
    assert.equal(payload.p_payload.owner_hash,'b'.repeat(64));
    await finish(env,'a'.repeat(32),'b'.repeat(64),'succeeded',{bytes:123},'');
    assert.equal(payload.p_action,'finish');assert.equal(payload.p_payload.result.bytes,123);
  } finally {globalThis.fetch=original;}
});
