import {test} from 'node:test';
import assert from 'node:assert/strict';
import {scriptWithRepair} from '../src/scriptRepair';

const config={settings:{keys:{gemini_free:['fake-gemini'],groq:['fake-groq']}}};
const validate=(value:any)=>{if(value.beats.length<20)throw new Error(`Expected 20–30 contextual scenes; received ${value.beats.length}`);};
test('invalid script persists count and next attempt repairs with Groq and concrete feedback',async()=>{
  let saved:any;
  await assert.rejects(scriptWithRepair(config,'reserved premise',{},null,async()=>({beats:[{}]}),validate,async v=>{saved=v;}),/attempt 1\/3/);
  assert.equal(saved.scene_count,1);
  const result=await scriptWithRepair(config,'reserved premise',{},saved,async(c,p)=>{
    assert.deepEqual(c.settings.keys.gemini_free,[]);
    assert.match(p,/received 1/);assert.match(p,/reserved premise/);
    return {beats:Array(25).fill({})};
  },validate,async()=>{throw new Error('Valid output must not be rejected');});
  assert.equal(result.beats.length,25);
  assert.equal(config.settings.keys.gemini_free.length,1);
});
test('three invalid attempts exhaust; fourth makes no provider call',async()=>{
  let saved:any;let calls=0;
  const generate=async()=>{calls++;return {beats:[]};};
  for(let i=0;i<3;i++)await assert.rejects(scriptWithRepair(config,'',{},saved,generate,validate,async v=>{saved=v;}));
  await assert.rejects(scriptWithRepair(config,'',{},saved,generate,validate,async()=>{}),/exhausted 3/);
  assert.equal(calls,3);
});
test('without Groq repair keeps configured Gemini keys',async()=>{
  const c={settings:{keys:{gemini_free:['fake']}}};
  await scriptWithRepair(c,'',{}, {attempts:1,error:'bad',scene_count:1},async selected=>{
    assert.equal(selected.settings.keys.gemini_free[0],'fake');return {beats:Array(25).fill({})};
  },validate,async()=>{});
});
